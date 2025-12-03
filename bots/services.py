
from typing import Optional
from .models import Bot

def route_bot_for_signal(symbol: str, timeframe: str) -> Optional[Bot]:
    # naive: first ACTIVE bot that accepts symbol/timeframe
    for bot in Bot.objects.filter(status="active").order_by("id"):
        if bot.accepts(symbol, timeframe):
            return bot
    return None
