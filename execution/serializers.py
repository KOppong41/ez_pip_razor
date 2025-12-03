from rest_framework import serializers
from .models import Decision, Signal, Order
from bots.models import Bot
from brokers.models import BrokerAccount
from .models import Signal, Order
from django.conf import settings
from bots.services_config import get as cfg_get
import json, hashlib, hmac
from datetime import datetime, timezone, timedelta
from django.conf import settings
from django.utils import timezone as dj_timezone


class SignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Signal
        fields = "__all__"
        read_only_fields = ("received_at",)

class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = ("status","created_at","updated_at")

class QuickOrderCreateSerializer(serializers.Serializer):
    bot_id = serializers.PrimaryKeyRelatedField(queryset=Bot.objects.all(), source="bot")
    broker_account_id = serializers.PrimaryKeyRelatedField(queryset=BrokerAccount.objects.all(), source="broker_account")
    symbol = serializers.CharField()
    side = serializers.ChoiceField(choices=[("buy","buy"),("sell","sell")])
    qty = serializers.DecimalField(max_digits=20, decimal_places=8)

    def create(self, validated):
        from .models import Order
        import uuid
        return Order.objects.create(
            bot=validated["bot"],
            broker_account=validated["broker_account"],
            client_order_id=str(uuid.uuid4())[:20],
            symbol=validated["symbol"],
            side=validated["side"],
            qty=validated["qty"],
        )


class AlertWebhookSerializer(serializers.Serializer):
    source = serializers.CharField()
    symbol = serializers.CharField()
    timeframe = serializers.CharField(default="5m")
    direction = serializers.ChoiceField(choices=[("buy","buy"),("sell","sell")])
    payload = serializers.JSONField()
    dedupe_key = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        # Require a timestamp in ms (from Pine)
        ts = (
            data.get("payload", {}).get("bar", {}).get("time")
            or data.get("payload", {}).get("ts")
        )
        if ts is None:
            raise serializers.ValidationError(
                {"payload": "Missing bar time 'payload.bar.time' or 'payload.ts' (ms)."}
            )
        try:
            ts_ms = int(str(ts))  # TradingView provides ms epoch
        except Exception:
            raise serializers.ValidationError({"payload": "Invalid 'time' format (expect epoch ms)."})

        # Route bot now (may be None)
        symbol = data.get("symbol")
        tf = data.get("timeframe", "5m")
        from bots.services import route_bot_for_signal
        bot = route_bot_for_signal(symbol, tf)

        # Max age (default 180s if no bot or config)
        max_age = 180
        try:
            if bot:
                
                max_age = int(cfg_get(bot, "ingest.max_age_sec", 180))
        except Exception:
            max_age = 180

        # ✅ Correct timestamp conversion + UTC handling (Django 5 compatible)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        now = dj_timezone.now()  # aware datetime
        if (now - dt) > timedelta(seconds=max_age):
            raise serializers.ValidationError({"stale": f"Alert too old (> {max_age}s)"})

        data["_routed_bot"] = bot
        return data

    def create(self, validated):
        # Pull out the routed bot and drop any private keys from the payload
        bot = validated.pop("_routed_bot", None)

        # Only keep fields that actually exist on Signal
        allowed = {"source", "symbol", "timeframe", "direction", "payload", "dedupe_key"}
        payload = {k: v for k, v in validated.items() if k in allowed}

        # Ensure dedupe_key
        dk = payload.get("dedupe_key") or make_dedupe_key(payload)
        payload["dedupe_key"] = dk

        # Create-or-get by dedupe_key, attach bot (FK) if your Signal has one
        # NOTE: route_bot_for_signal must return a Bot instance (not an id/string)
        defaults = {**payload, "bot": bot}
        signal, created = Signal.objects.get_or_create(dedupe_key=dk, defaults=defaults)
        return signal, created



def _canonical_bytes(data: dict) -> bytes:
    # Stable JSON (sorted keys, no extra spaces)
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

def make_dedupe_key(data: dict) -> str:
    body = _canonical_bytes({
        "source": data["source"],
        "symbol": data["symbol"],
        "timeframe": data.get("timeframe", "5m"),
        "direction": data["direction"],
        "payload": data.get("payload", {}),
    })
    secret = getattr(settings, "EXECUTION_ALERT_SECRET", None)
    if secret:
        return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hashlib.sha256(body).hexdigest()


class OrderCreateFromDecisionSerializer(serializers.Serializer):
    decision_id = serializers.PrimaryKeyRelatedField(queryset=Decision.objects.all(), source="decision")
    broker_account_id = serializers.PrimaryKeyRelatedField(queryset=BrokerAccount.objects.all(), source="broker_account")
    qty = serializers.DecimalField(max_digits=20, decimal_places=8)

class OrderTransitionSerializer(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=[("ack","ack"),("filled","filled"),("part_filled","part_filled"),("canceled","canceled"),("error","error")])
    price = serializers.DecimalField(max_digits=20, decimal_places=8, required=False)
    error_msg = serializers.CharField(required=False, allow_blank=True)