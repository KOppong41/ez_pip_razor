"""Authenticated, isolated historical simulation API. Never dispatches orders."""
import logging

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
    bots = Bot.objects.select_related("asset")
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
