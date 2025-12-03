import re, json, time, requests
from django.conf import settings

def tg_api(method, **params):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    return requests.post(url, json=params, timeout=10).json()

def send_reply(chat_id, text):
    if chat_id is None: return
    try:
        tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

def parse_update_to_signal(update: dict):
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()

    m = JSON_RE.search(text)
    if m:
        payload = json.loads(m.group(0))
        symbol = payload.get("symbol")
        tf = payload.get("timeframe","")
        direction = payload.get("direction")
        bar = payload.get("bar") or payload.get("payload",{}).get("bar") or {}
        t = bar.get("time") or int(time.time()*1000)
        dk = payload.get("dedupe_key") or f"{symbol}-{tf}-{t}-{direction}"
        return ({
            "source":"telegram","symbol":symbol,"timeframe":tf,"direction":direction,
            "payload":{"bar":{"time":t}}, "dedupe_key":dk
        }, chat_id, user_id, text)

    if text.lower().startswith("/trade"):
        parts = text.split()
        if len(parts) < 4:
            raise ValueError("bad /trade")
        _, symbol, direction, *rest = parts
        kv = dict(p.split("=",1) for p in rest if "=" in p)
        tf = kv.get("tf") or kv.get("timeframe") or "5m"
        t  = kv.get("time") or int(time.time()*1000)
        dk = kv.get("dk") or f"{symbol}-{tf}-{t}-{direction}"
        return ({
            "source":"telegram","symbol":symbol.upper(),"timeframe":tf,"direction":direction.lower(),
            "payload":{"bar":{"time":t}}, "dedupe_key":dk
        }, chat_id, user_id, text)

    raise ValueError("not a trade")
