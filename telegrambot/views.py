from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.conf import settings
import traceback

from .models import TelegramSource
from .services import parse_update_to_signal, send_reply
from execution.serializers import AlertWebhookSerializer
from execution.services.decision import make_decision_from_signal
from execution.services.fanout import fanout_orders
from execution.services.brokers import dispatch_place_order
from core.metrics import signals_ingested_total

ENFORCE_SOURCE_ON_TRADES = True

@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def webhook(request, secret: str):
    # GET health (no secret required)
    if request.method == "GET":
        return Response({"status": "ok"}, status=200)

    # Secret guard (POST)
    cfg_secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "") or ""
    if (secret or "") != cfg_secret:
        return Response({"error": "forbidden"}, status=403)

    upd = request.data or {}
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()

    # Quick helpers
    if text.lower().startswith("/start"):
        send_reply(chat_id, "👋 Bot ready. Send:\n/trade SYMBOL buy|sell tf=5m\nor JSON {\"symbol\":\"EURUSD\",\"timeframe\":\"5m\",\"direction\":\"buy\"}")
        return Response({"ok": True})
    if text.lower().startswith("/help"):
        send_reply(chat_id, "Usage: /trade EURUSD buy tf=5m\nor JSON in message body.")
        return Response({"ok": True})
    if text.lower().startswith("/id"):
        send_reply(chat_id, f"chat_id={chat_id}\nuser_id={user_id}")
        return Response({"ok": True})
    if text.lower().startswith("/echo"):
        send_reply(chat_id, text.partition(" ")[2] or "pong")
        return Response({"ok": True})

    # Parse → Validate → Execute with pinpointed error replies
    try:
        # Parse JSON or /trade
        payload, _, _, _ = parse_update_to_signal(upd)
    except Exception as e:
        traceback.print_exc()
        send_reply(chat_id, f"⚠️ Parse error: {e}")
        return Response({"ok": False, "stage": "parse", "error": str(e)}, status=200)

    try:
        # Access control only for actual trades
        if ENFORCE_SOURCE_ON_TRADES:
            src = TelegramSource.objects.filter(chat_id=chat_id, is_enabled=True).first()
            if not src:
                send_reply(chat_id, "🔒 Not authorized. Ask admin to register this chat.")
                return Response({"ok": True, "stage": "auth"})
            if getattr(src, "allow_users", None):
                if user_id not in src.allow_users:
                    send_reply(chat_id, "🔒 You are not allowed to trade from this chat.")
                    return Response({"ok": True, "stage": "auth"})
    except Exception as e:
        traceback.print_exc()
        send_reply(chat_id, f"⚠️ Auth lookup error: {e}")
        return Response({"ok": False, "stage": "auth", "error": str(e)}, status=200)

    try:
        # Validate payload with your serializer
        ser = AlertWebhookSerializer(data=payload)
        if not ser.is_valid():
            send_reply(chat_id, f"⚠️ Invalid signal: {ser.errors}")
            return Response({"ok": False, "stage": "validate", "errors": ser.errors}, status=200)
        signal, created = ser.save()
    except Exception as e:
        traceback.print_exc()
        send_reply(chat_id, f"⚠️ Save error: {e}")
        return Response({"ok": False, "stage": "save", "error": str(e)}, status=200)

    try:
        # Metrics (make sure labels match your counter definition)
        signals_ingested_total.labels(signal.source, signal.symbol, signal.timeframe).inc()
    except Exception as e:
        traceback.print_exc()
        # Non-fatal; report and continue
        send_reply(chat_id, f"⚠️ Metrics error (non-fatal): {e}")

    orders_sent = 0
    try:
        if signal.bot and getattr(signal.bot, "status", None) == "active" and getattr(signal.bot, "auto_trade", False):
            decision = make_decision_from_signal(signal)
            if getattr(decision, "action", None) == "open":
                for order, _ in fanout_orders(decision, master_qty=None):
                    try:
                        dispatch_place_order(order)
                        orders_sent += 1
                    except Exception as oe:
                        traceback.print_exc()
                        # Log but keep going
                # fall-through reply below
    except Exception as e:
        traceback.print_exc()
        send_reply(chat_id, f"⚠️ Trade dispatch error: {e}")
        return Response({"ok": False, "stage": "dispatch", "error": str(e)}, status=200)

    # Success reply
    try:
        send_reply(chat_id, f"✅ {signal.symbol} {signal.timeframe} {signal.direction}. Orders sent: {orders_sent}")
    except Exception:
        traceback.print_exc()
        # still return ok

    return Response({"ok": True, "signal": getattr(signal, "id", None), "orders_sent": orders_sent}, status=201 if created else 200)
