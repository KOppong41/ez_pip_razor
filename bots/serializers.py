from rest_framework import serializers
from .models import Bot, Asset, STRATEGY_CHOICES, STRATEGY_GUIDES

class BotSerializer(serializers.ModelSerializer):
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.filter(is_active=True))
    enabled_strategies = serializers.ListField(
        child=serializers.ChoiceField(choices=[(s, s) for s in STRATEGY_CHOICES]),
        required=True,
        allow_empty=False,
        help_text="Tick one or more strategies this bot may run. See strategy_guides for recommended assets per pattern.",
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
        if not value:
            raise serializers.ValidationError("Select at least one strategy.")
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
        help_text="Tick one or more strategies this bot may run. See strategy_guides for recommended assets per pattern.",
    )
    allowed_timeframes = serializers.ListField(
        child=serializers.ChoiceField(choices=[("1m","1m"),("5m","5m"),("15m","15m"),("30m","30m"),("1h","1h"),("4h","4h"),("1d","1d")]),
        required=False,
        allow_empty=True,
    )
    strategy_guides = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Bot
        fields = ("asset", "default_qty", "allowed_timeframes", "enabled_strategies", "strategy_guides")

    def validate_enabled_strategies(self, value):
        if not value:
            raise serializers.ValidationError("Select at least one strategy.")
        return value

    def get_strategy_guides(self, obj):
        return STRATEGY_GUIDES
