"""Authenticated, isolated historical simulation API. Never dispatches orders."""
import logging
from decimal import Decimal, InvalidOperation

from django.db.models import F
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bots.models import Bot
from execution.models import HistoricalBacktest
from execution.services.historical_backtest import (
    MAX_BARS, MAX_CSV_BYTES, TIMEFRAMES, int_field, json_safe, parse_csv,
    run_simulation, validate_config,
)
from execution.services.strategy_registry import SCALPER_STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


def _owned_bots(user):
    bots = Bot.objects.select_related("asset", "broker_account")
    return bots if user.is_superuser else bots.filter(owner=user)


def _serialize(run, *, detail=False):
    data = {
        "id": run.id, "bot_id": run.bot_id, "bot_name": run.bot_name,
        "symbol": run.symbol, "status": run.status, "source_name": run.source_name,
        "config": run.config, "dataset": run.dataset,
        "summary": run.summary_data if hasattr(run, "summary_data") else run.result.get("summary", {}), "error": run.error,
        "created_at": run.created_at, "completed_at": run.completed_at,
    }
    if detail:
        data["result"] = run.result
    return data


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def backtest_options(request):
    return Response({
        "bots": [
            {"id": bot.id, "name": bot.name, "symbol": bot.asset.symbol,
             "timeframe": bot.default_timeframe, "quantity": str(bot.default_qty)}
            for bot in _owned_bots(request.user).filter(asset__isnull=False)
        ],
        "strategies": list(SCALPER_STRATEGY_REGISTRY),
        "timeframes": list(TIMEFRAMES), "max_bars": MAX_BARS, "max_csv_bytes": MAX_CSV_BYTES,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def backtest_defaults(request, bot_id):
    bot = _owned_bots(request.user).filter(id=bot_id, asset__isnull=False).first()
    if bot is None:
        return Response({"detail": "Bot not found."}, status=404)
    keys = ("contract_size", "point_size", "currency", "spread_points",
            "slippage_points", "commission_per_lot")
    previous = HistoricalBacktest.objects.filter(
        owner=request.user, bot=bot, symbol=bot.asset.symbol, status="completed",
    ).only("config", "created_at").first()
    if previous:
        return Response({
            "values": {key: previous.config[key] for key in keys if key in previous.config},
            "source": "saved_backtest", "as_of": previous.created_at,
            "message": "Defaults from your latest completed backtest for this bot. Review costs and specifications before reuse.",
        })
    values = {}
    account = bot.broker_account
    if account and (request.user.is_superuser or account.owner_id == request.user.id) and account.requires_mt5_connector():
        try:
            from execution.connectors.mt5 import MT5Connector
            info = MT5Connector().current_symbol_info_for_account(account, bot.asset.symbol)
            for key, attribute in (("contract_size", "trade_contract_size"), ("point_size", "point")):
                try:
                    value = Decimal(str(getattr(info, attribute, "")))
                except (InvalidOperation, ValueError, TypeError):
                    continue
                if value.is_finite() and Decimal("1e-12") <= value <= Decimal("1e12"):
                    values[key] = str(value)
            currency = str(getattr(info, "currency_profit", "")).upper()
            if len(currency) == 3 and currency.isascii() and currency.isalpha():
                values["currency"] = currency
            spread = Decimal(str(getattr(info, "spread", "")))
            if spread.is_finite() and 0 <= spread <= Decimal("1e12"):
                values["spread_points"] = str(spread)
        except (InvalidOperation, ValueError, TypeError):
            pass  # Missing/invalid specifications must not become guessed defaults.
        except Exception:
            logger.info("Optional backtest specifications unavailable for bot %s", bot.id)
    return Response({
        "values": values, "source": "broker_snapshot" if values else "unavailable",
        "as_of": timezone.now() if values else None,
        "message": (
            "Defaults read from the connected broker symbol. Spread is a current snapshot, not a historical average. Confirm all values for your CSV period."
            if values else
            "Broker specifications unavailable. Connect this bot's account in the app and reload defaults, or copy Contract size, Point and Profit currency from MT5 Symbol Specification. Unknown sizes are left blank."
        ),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def backtest_preview(request):
    """Validate imported candles and suggest dates; no records or trades created."""
    if not isinstance(request.data, dict):
        return Response({"detail": "Expected a JSON object."}, status=400)
    try:
        config = validate_config({
            "strategy": request.data.get("strategy", "trend_pullback"),
            "timeframe": request.data.get("timeframe", "1m"),
            "warmup": request.data.get("warmup", 100),
            "csv_utc_offset_minutes": request.data.get("csv_utc_offset_minutes", 0),
            # Preview only needs candle validation, not trading economics.
            "quantity": "1", "contract_size": "1", "point_size": "1",
        })
        bars, dataset = parse_csv(request.data.get("csv"), config)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(json_safe({
        **dataset, "warmup": config["warmup"],
        "first_tradable_at": bars[dataset["first_index"]]["time"],
        "start_date": bars[dataset["first_index"]]["time"].date().isoformat(),
        "end_date": bars[-1]["time"].date().isoformat(),
    }))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def historical_backtests(request):
    # Simulations are private to their creator, including administrator-created runs.
    owned = HistoricalBacktest.objects.filter(owner=request.user)
    if request.method == "GET":
        try:
            page = int_field(request.query_params, "page", 1, 1, 1_000_000)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        size = 20
        page_runs = owned.annotate(summary_data=F("result__summary")).defer("source_csv", "result")
        return Response({"count": owned.count(), "page": page, "page_size": size,
                         "results": [_serialize(run) for run in page_runs[(page - 1) * size:page * size]]})

    if not isinstance(request.data, dict):
        return Response({"detail": "Expected a JSON object."}, status=400)
    try:
        bot_id = int_field(request.data, "bot_id", 0, 1, 2**63 - 1)
        bot = _owned_bots(request.user).filter(id=bot_id, asset__isnull=False).first()
        if bot is None:
            return Response({"detail": "Bot not found."}, status=404)
        config = validate_config(request.data)
        bars, dataset = parse_csv(request.data.get("csv"), config)
        if (dataset["last_index"] - dataset["first_index"] + 1) * config["warmup"] > 2_000_000:
            raise ValueError("Reduce the date range or warmup: this request exceeds the replay work limit.")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    source_name = str(request.data.get("source_name", "candles.csv")).replace("\\", "/").split("/")[-1][:255]
    run = HistoricalBacktest.objects.create(
        owner=request.user, bot=bot, bot_name=bot.name, symbol=bot.asset.symbol,
        source_name=source_name, source_csv=request.data["csv"],
        config=json_safe(config), dataset=json_safe(dataset),
    )
    try:
        run.result = run_simulation(bars, config, dataset, bot.asset.symbol)
        run.status = "completed"
    except Exception:
        logger.exception("Historical backtest %s failed", run.id)
        run.status = "failed"
        run.error = "Simulation failed. Check the backend log and retry."
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "result", "error", "completed_at"])
    return Response(_serialize(run, detail=True), status=201 if run.status == "completed" else 500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def historical_backtest_detail(request, run_id):
    run = HistoricalBacktest.objects.filter(owner=request.user, id=run_id).first()
    if run is None:
        return Response({"detail": "Backtest not found."}, status=404)
    return Response(_serialize(run, detail=True))
