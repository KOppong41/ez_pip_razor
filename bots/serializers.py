from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from brokers.models import BrokerAccount
from bots.services import recommended_bot_defaults

from .models import Asset, Bot, STANDARD_TIMEFRAMES, STRATEGY_CHOICES


class BotSerializer(serializers.ModelSerializer):
    apply_asset_recommendations = serializers.BooleanField(
        write_only=True,
        required=False,
    )
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
            "apply_asset_recommendations",
            "default_timeframe",
            "allowed_timeframes",
            "default_qty",
            "position_sizing_mode",
            "risk_per_trade_pct",
            "max_bot_lot_size",
            "default_tp_pips",
            "default_sl_pips",
            "auto_trade",
            "enabled_strategies",
            "scalper_params",
            "decision_min_score",
            "risk_max_concurrent_positions",
            "max_trades_per_day",
            "trade_interval_minutes",
            "max_spread_points",
            "allowed_deviation_points",
            "allow_live_account_execution",
            "close_positions_on_emergency_stop",
            "allocation_amount",
            "allocation_profit_pct",
            "allocation_loss_pct",
            "trading_profile",
            "trading_schedule_enabled",
            "trading_timezone",
            "allowed_trading_days",
            "trading_window_start",
            "trading_window_end",
            "allow_opposite_scalp",
            "kill_switch_enabled",
            "kill_switch_max_unrealized_pct",
            "loss_streak_autopause_enabled",
            "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
            "soft_drawdown_limit_pct",
            "soft_size_multiplier",
            "hard_drawdown_limit_pct",
            "hard_size_multiplier",
            "asset_preset_version_applied",
            "asset_preset_applied_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "bot_id",
            "status",
            "asset_preset_version_applied",
            "asset_preset_applied_at",
            "created_at",
        )

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
            "recommended_config_version": asset.recommended_config_version,
            "recommended_config": asset.recommended_config,
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
            "risk_limits": self._account_risk_limits(account),
        }

    @staticmethod
    def _account_risk_limits(account):
        try:
            policy = account.risk_policy
        except Exception:
            return None
        return {
            "max_order_lot_size": policy.max_order_lot_size,
            "max_total_open_positions": policy.max_total_open_positions,
            "max_positions_per_symbol": policy.max_positions_per_symbol,
            "max_aggregate_open_lots": policy.max_aggregate_open_lots,
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def effective_value(field_name):
            """Use the value DRF will actually persist, including model defaults."""
            if field_name in attrs:
                return attrs[field_name]
            if self.instance is not None:
                return getattr(self.instance, field_name)
            return Bot._meta.get_field(field_name).get_default()

        account = attrs.get("broker_account", getattr(self.instance, "broker_account", None))
        sizing_mode = effective_value("position_sizing_mode")
        default_qty = effective_value("default_qty")
        bot_lot = effective_value("max_bot_lot_size")
        bot_positions = effective_value("risk_max_concurrent_positions")
        risk_pct = effective_value("risk_per_trade_pct")
        soft_drawdown = effective_value("soft_drawdown_limit_pct")
        hard_drawdown = effective_value("hard_drawdown_limit_pct")
        soft_multiplier = effective_value("soft_size_multiplier")
        hard_multiplier = effective_value("hard_size_multiplier")
        errors = {}
        if sizing_mode == "risk" and (risk_pct is None or risk_pct <= 0):
            errors["risk_per_trade_pct"] = "Risk per trade must be greater than 0 in risk-based mode."
        if sizing_mode == "fixed" and default_qty is not None and bot_lot is not None and default_qty > bot_lot:
            errors["default_qty"] = "Default lot size cannot exceed Maximum bot lot size."
        if soft_drawdown < 0 or hard_drawdown < 0:
            errors["soft_drawdown_limit_pct"] = "Drawdown limits cannot be negative."
        elif soft_drawdown > 0 and hard_drawdown > 0 and hard_drawdown < soft_drawdown:
            errors["hard_drawdown_limit_pct"] = "Hard drawdown must be greater than or equal to soft drawdown."
        if not (0 < soft_multiplier <= 1):
            errors["soft_size_multiplier"] = "Soft size multiplier must be greater than 0 and no greater than 1."
        if not (0 < hard_multiplier <= 1):
            errors["hard_size_multiplier"] = "Hard size multiplier must be greater than 0 and no greater than 1."
        elif hard_multiplier > soft_multiplier:
            errors["hard_size_multiplier"] = "Hard size multiplier cannot exceed the soft size multiplier."
        if account:
            try:
                policy = account.risk_policy
            except Exception:
                policy = None
            if policy and bot_lot is not None and policy.max_order_lot_size > 0 and bot_lot > policy.max_order_lot_size:
                errors["max_bot_lot_size"] = (
                    f"Cannot exceed the account hard limit of {policy.max_order_lot_size} lots."
                )
            if policy and bot_positions is not None and policy.max_total_open_positions > 0 and bot_positions > policy.max_total_open_positions:
                errors["risk_max_concurrent_positions"] = (
                    "Cannot exceed the account hard limit of "
                    f"{policy.max_total_open_positions} positions."
                )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @staticmethod
    def _validation_detail(exc):
        if hasattr(exc, "message_dict"):
            return exc.message_dict
        return {"detail": exc.messages}

    def create(self, validated_data):
        request = self.context["request"]
        apply_recommendations = validated_data.pop("apply_asset_recommendations", True)
        asset = validated_data.get("asset")
        if apply_recommendations and asset is not None:
            explicit_values = dict(validated_data)
            validated_data = recommended_bot_defaults(asset)
            validated_data.update(explicit_values)
            validated_data["asset_preset_version_applied"] = (
                asset.recommended_config_version
            )
            validated_data["asset_preset_applied_at"] = timezone.now()
        validated_data["owner"] = request.user
        validated_data["status"] = "stopped"
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(self._validation_detail(exc)) from exc

    def update(self, instance, validated_data):
        apply_recommendations = validated_data.pop(
            "apply_asset_recommendations", False
        )
        if apply_recommendations:
            asset = validated_data.get("asset", instance.asset)
            explicit_values = dict(validated_data)
            validated_data = recommended_bot_defaults(asset)
            validated_data.update(explicit_values)
            validated_data["asset_preset_version_applied"] = (
                asset.recommended_config_version
            )
            validated_data["asset_preset_applied_at"] = timezone.now()
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
