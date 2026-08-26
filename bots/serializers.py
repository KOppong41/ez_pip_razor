from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from brokers.models import BrokerAccount

from .models import Asset, Bot, STANDARD_TIMEFRAMES, STRATEGY_CHOICES


class BotSerializer(serializers.ModelSerializer):
    asset = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.filter(is_active=True),
    )
    broker_account = serializers.PrimaryKeyRelatedField(
        queryset=BrokerAccount.objects.none(),
    )
    asset_details = serializers.SerializerMethodField()
    broker_account_details = serializers.SerializerMethodField()
    enabled_strategies = serializers.ListField(
        child=serializers.ChoiceField(choices=STRATEGY_CHOICES),
        required=False,
        allow_empty=True,
        default=list,
    )
    allowed_timeframes = serializers.ListField(
        child=serializers.ChoiceField(choices=STANDARD_TIMEFRAMES),
        required=False,
        allow_empty=True,
    )
    allowed_trading_days = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        ),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = Bot
        fields = (
            "id",
            "bot_id",
            "name",
            "status",
            "asset",
            "asset_details",
            "broker_account",
            "broker_account_details",
            "engine_mode",
            "default_timeframe",
            "allowed_timeframes",
            "default_qty",
            "default_tp_pips",
            "default_sl_pips",
            "auto_trade",
            "enabled_strategies",
            "decision_min_score",
            "risk_max_concurrent_positions",
            "max_trades_per_day",
            "trade_interval_minutes",
            "allocation_amount",
            "allocation_profit_pct",
            "allocation_loss_pct",
            "trading_profile",
            "trading_schedule_enabled",
            "allowed_trading_days",
            "trading_window_start",
            "trading_window_end",
            "allow_opposite_scalp",
            "kill_switch_enabled",
            "kill_switch_max_unrealized_pct",
            "loss_streak_autopause_enabled",
            "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
            "created_at",
        )
        read_only_fields = ("id", "bot_id", "status", "created_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            self.fields["broker_account"].queryset = BrokerAccount.objects.filter(
                owner=request.user,
                is_active=True,
            )

    def get_asset_details(self, obj):
        asset = obj.asset
        if not asset:
            return None
        return {
            "id": asset.id,
            "symbol": asset.symbol,
            "display_name": asset.display_name,
            "category": asset.category,
            "min_qty": asset.min_qty,
            "recommended_qty": asset.recommended_qty,
        }

    def get_broker_account_details(self, obj):
        account = obj.broker_account
        if not account:
            return None
        return {
            "id": account.id,
            "name": account.name,
            "broker": account.broker,
            "login": account.mt5_login,
            "server": account.mt5_server,
            "is_verified": account.is_verified,
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        auto_trade = attrs.get(
            "auto_trade",
            getattr(self.instance, "auto_trade", True),
        )
        strategies = attrs.get(
            "enabled_strategies",
            getattr(self.instance, "enabled_strategies", []),
        )
        if not auto_trade and not strategies:
            raise serializers.ValidationError(
                {
                    "enabled_strategies": (
                        "Select at least one strategy when auto-trade is disabled."
                    )
                }
            )
        return attrs

    @staticmethod
    def _validation_detail(exc):
        if hasattr(exc, "message_dict"):
            return exc.message_dict
        return {"detail": exc.messages}

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["owner"] = request.user
        validated_data["status"] = "stopped"
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(self._validation_detail(exc)) from exc

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(self._validation_detail(exc)) from exc


class BotControlSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["start", "pause", "stop"])


class BotSettingsSerializer(BotSerializer):
    """Compatibility alias for older API clients using the settings action."""

    class Meta(BotSerializer.Meta):
        read_only_fields = BotSerializer.Meta.read_only_fields
