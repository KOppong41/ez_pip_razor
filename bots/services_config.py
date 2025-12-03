from typing import Any
from .models import Bot

# Legacy helper retained for imports; now just reads fields on Bot.

def get(bot: Bot, key: str, default: Any = None) -> Any:
    if key == "decision_min_score":
        try:
            return float(bot.decision_min_score)
        except Exception:
            return default
    return getattr(bot, key, default)


def get_all(bot: Bot) -> dict[str, Any]:
    return {
        "decision_min_score": get(bot, "decision_min_score", None),
    }


def clear_cache(bot: Bot):
    return None
