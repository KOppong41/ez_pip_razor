from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from brokers.models import BrokerAccount
from core.utils import structured_log
from execution.models import MT5ConnectionState, RiskPolicy
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
        )
        accounts = BrokerAccount.objects.filter(
            owner=request.user,
            is_active=True,
        ).values(
            "id",
            "name",
            "broker",
            "mt5_login",
            "mt5_server",
            "is_verified",
        )
        return Response(
            {
                "assets": list(assets),
                "accounts": list(accounts),
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
            if connection.account_mode == "live" and not risk.live_trading_confirmed:
                return Response(
                    {"detail": "Live trading has not been explicitly confirmed."},
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
