from celery import shared_task
from django.conf import settings
import requests
from .services import parse_update_to_signal, send_reply
from .models import TelegramSource
from execution.serializers import AlertWebhookSerializer

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True)
def poll_updates(self, offset=None):
    token = settings.TELEGRAM_BOT_TOKEN
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 25}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=30)
    data = r.json()
    next_offset = offset
    for upd in data.get("result", []):
        next_offset = max(next_offset or 0, upd["update_id"] + 1)
        # forward to the same processing path as webhook
        try:
            payload, chat_id, user_id, _ = parse_update_to_signal(upd)
            src = TelegramSource.objects.filter(chat_id=chat_id, is_enabled=True).first()
            if not src: 
                send_reply(chat_id, "🔒 Not authorized.")
                continue
            ser = AlertWebhookSerializer(data=payload)
            if ser.is_valid():
                signal, created = ser.save()
                send_reply(chat_id, "✅ Signal accepted.")
            else:
                send_reply(chat_id, f"⚠️ Invalid: {ser.errors}")
        except Exception:
            send_reply(chat_id, "❌ Error")
    return next_offset
