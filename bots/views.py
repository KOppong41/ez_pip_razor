from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from brokers.models import BrokerAccount
from core.utils import structured_log
from execution.models import MT5ConnectionState, RiskPolicy
from execution.services.brokers import get_broker_symbol_constraints
from subscription.utils import get_bot_limit

from .models import (
    ENGINE_MODES,
    STANDARD_TIMEFRAMES,
    STRATEGY_CHOICES,
    STRATEGY_GUIDES,
    TRADING_PROFILE_CHOICES,
    Asset,
    Bot,
)
from .serializers import BotControlSerializer, BotSerializer
from .services import apply_recommendations_to_bot


class BotViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """User-facing bot management API.

    Every queryset and writable relation is scoped to the authenticated owner.
    Global asset definitions and execution defaults remain platform-admin data.
    """

    permission_classes = [IsAuthenticated]
    queryset = Bot.objects.select_related("asset", "broker_account").order_by("id")
    serializer_class = BotSerializer

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    @action(detail=False, methods=["get"], url_path="options")
    def options(self, request):
        assets = Asset.objects.filter(is_active=True).values(
            "id",
            "symbol",
            "display_name",
            "category",
            "min_qty",
            "recommended_qty",
            "max_spread",
            "recommended_config_version",
            "recommended_config",
        )
        account_rows = BrokerAccount.objects.filter(
            owner=request.user,
            is_active=True,
        ).select_related("risk_policy")
        accounts = []
        for account in account_rows:
            try:
                risk = account.risk_policy
            except RiskPolicy.DoesNotExist:
                risk = None
            accounts.append(
                {
                    "id": account.id,
                    "name": account.name,
                    "broker": account.broker,
                    "mt5_login": account.mt5_login,
                    "mt5_server": account.mt5_server,
                    "is_verified": account.is_verified,
                    "risk_limits": None
                    if risk is None
                    else {
                        "max_order_lot_size": risk.max_order_lot_size,
                        "max_total_open_positions": risk.max_total_open_positions,
                        "max_positions_per_symbol": risk.max_positions_per_symbol,
                        "max_aggregate_open_lots": risk.max_aggregate_open_lots,
                    },
                }
            )
        return Response(
            {
                "assets": list(assets),
                "accounts": accounts,
                "engine_modes": [
                    {"value": value, "label": label}
                    for value, label in ENGINE_MODES
                ],
                "timeframes": STANDARD_TIMEFRAMES,
                "strategies": [
                    {
                        "value": value,
                        **STRATEGY_GUIDES.get(value, {"label": value.replace("_", " ").title()}),
                    }
                    for value in STRATEGY_CHOICES
                ],
                "trading_profiles": [
                    {"value": value, "label": label}
                    for value, label in TRADING_PROFILE_CHOICES
                ],
                "usage": {
                    "bots": self.get_queryset().count(),
                    "bot_limit": get_bot_limit(request.user),
                },
            }
        )

    @action(detail=True, methods=["post"], url_path="control")
    def control(self, request, pk=None):
        bot = self.get_object()
        serializer = BotControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action_name = serializer.validated_data["action"]

        if action_name == "start":
            if not bot.broker_account_id:
                return Response(
                    {"detail": "Assign a broker account before starting this bot."},
                    status=status.HTTP_409_CONFLICT,
                )
            connection = MT5ConnectionState.objects.filter(
                broker_account=bot.broker_account
            ).first()
            if not connection or not connection.connected:
                return Response(
                    {"detail": "MT5 must be connected before starting this bot."},
                    status=status.HTTP_409_CONFLICT,
                )
            risk, _ = RiskPolicy.objects.get_or_create(
                broker_account=bot.broker_account
            )
            if connection.account_mode == "live" and not bot.allow_live_account_execution:
                return Response(
                    {"detail": "Live-account execution is disabled for this bot."},
                    status=status.HTTP_409_CONFLICT,
                )
            risk.entries_enabled = True
            risk.emergency_stop = False
            risk.save(
                update_fields=["entries_enabled", "emergency_stop", "updated_at"]
            )

        new_status = {
            "start": "active",
            "pause": "paused",
            "stop": "stopped",
        }[action_name]
        bot.status = new_status
        bot.save(update_fields=["status"])

        if action_name in {"pause", "stop"} and bot.broker_account_id:
            has_active_sibling = Bot.objects.filter(
                owner=request.user,
                broker_account=bot.broker_account,
                status="active",
            ).exists()
            if not has_active_sibling:
                RiskPolicy.objects.filter(
                    broker_account=bot.broker_account
                ).update(entries_enabled=False)

        structured_log(
            "bot.control",
            bot_id=bot.id,
            control_action=action_name,
            status=new_status,
            owner_id=request.user.id,
        )
        return Response(BotSerializer(bot, context={"request": request}).data)

    @action(detail=True, methods=["patch"], url_path="settings")
    def update_settings(self, request, pk=None):
        bot = self.get_object()
        serializer = BotSerializer(
            bot,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        structured_log(
            "bot.settings",
            bot_id=bot.id,
            changes=serializer.validated_data,
            owner_id=request.user.id,
        )
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="apply-asset-recommendations")
    def apply_asset_recommendations(self, request, pk=None):
        bot = self.get_object()
        if not bot.asset_id:
            return Response(
                {"detail": "Assign an asset before applying recommendations."},
                status=status.HTTP_409_CONFLICT,
            )
        apply_recommendations_to_bot(bot)
        structured_log(
            "bot.asset_recommendations_applied",
            bot_id=bot.id,
            asset_id=bot.asset_id,
            preset_version=bot.asset_preset_version_applied,
            owner_id=request.user.id,
        )
        return Response(BotSerializer(bot, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="symbol-constraints")
    def symbol_constraints(self, request):
        """Return the connected broker's authoritative fixed-lot hints."""
        account_id = request.query_params.get("broker_account")
        asset_id = request.query_params.get("asset")
        if not account_id or not asset_id:
            return Response(
                {"detail": "broker_account and asset are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        account = BrokerAccount.objects.filter(
            pk=account_id,
            owner=request.user,
            is_active=True,
        ).first()
        asset = Asset.objects.filter(pk=asset_id, is_active=True).first()
        if account is None or asset is None:
            return Response(
                {"detail": "Broker account or asset was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        constraints = get_broker_symbol_constraints(account, asset.symbol)
        available = bool(
            constraints.min_lot is not None
            and constraints.min_lot > 0
            and constraints.lot_step is not None
            and constraints.lot_step > 0
        )
        return Response(
            {
                "available": available,
                "symbol": asset.symbol,
                "volume_min": str(constraints.min_lot) if constraints.min_lot is not None else None,
                "volume_max": str(constraints.max_lot) if constraints.max_lot is not None else None,
                "volume_step": str(constraints.lot_step) if constraints.lot_step is not None else None,
                "suggested_quantity": str(constraints.min_lot) if available else None,
            }
        )
