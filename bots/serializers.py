from rest_framework import serializers
from .models import Bot, Asset, STRATEGY_CHOICES, STRATEGY_GUIDES


def _auto_trade_requested(serializer) -> bool:
    initial = getattr(serializer, "initial_data", None)
    if isinstance(initial, dict) and "auto_trade" in initial:
        raw = initial.get("auto_trade")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
    instance = getattr(serializer, "instance", None)
    if instance is not None:
        return bool(getattr(instance, "auto_trade", True))
    return True

class BotSerializer(serializers.ModelSerializer):
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.filter(is_active=True))
    enabled_strategies = serializers.ListField(
        child=serializers.ChoiceField(choices=[(s, s) for s in STRATEGY_CHOICES]),
        required=True,
        allow_empty=False,
        help_text="Tick one or more strategies used when auto-trade is disabled. See strategy_guides for recommendations.",
    )
    allowed_timeframes = serializers.ListField(
        child=serializers.ChoiceField(choices=[("1m","1m"),("5m","5m"),("15m","15m"),("30m","30m"),("1h","1h"),("4h","4h"),("1d","1d")]),
        required=False,
        allow_empty=True,
    )
    strategy_guides = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Bot
        fields = "__all__"

    def validate_enabled_strategies(self, value):
        if _auto_trade_requested(self):
            # Auto-trade mode ignores manual selections, but persist whatever was provided for future manual use.
            return value or []
        if not value:
            raise serializers.ValidationError("Select at least one strategy when auto-trade is disabled.")
        return value

    def get_strategy_guides(self, obj):
        return STRATEGY_GUIDES

class BotControlSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[("start","start"),("pause","pause"),("stop","stop")])

class BotSettingsSerializer(serializers.ModelSerializer):
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.filter(is_active=True))
    enabled_strategies = serializers.ListField(
        child=serializers.ChoiceField(choices=[(s, s) for s in STRATEGY_CHOICES]),
        required=True,
        allow_empty=False,
        help_text="Tick one or more strategies used when auto-trade is disabled. See strategy_guides for recommendations.",
    )
    allowed_timeframes = serializers.ListField(
        child=serializers.ChoiceField(choices=[("1m","1m"),("5m","5m"),("15m","15m"),("30m","30m"),("1h","1h"),("4h","4h"),("1d","1d")]),
        required=False,
        allow_empty=True,
    )
    strategy_guides = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Bot
        fields = ("asset", "default_qty", "allowed_timeframes", "enabled_strategies", "auto_trade", "strategy_guides")

    def validate_enabled_strategies(self, value):
        if _auto_trade_requested(self):
            return value or []
        if not value:
            raise serializers.ValidationError("Select at least one strategy when auto-trade is disabled.")
        return value

    def get_strategy_guides(self, obj):
        return STRATEGY_GUIDES
