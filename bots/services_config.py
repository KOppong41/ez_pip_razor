from typing import Any

from .models import Bot


def _normalize_decision_min_score(bot: Bot, default: Any = None) -> Any:
    try:
        value = float(bot.decision_min_score)
    except Exception:
        return default
    if value is None or value <= 0:
        return default
    return value


def get(bot: Bot, key: str, default: Any = None) -> Any:
    """
    Legacy helper retained for imports; now just reads fields on Bot.
    """
    if key == "decision_min_score":
        return _normalize_decision_min_score(bot, default=default)
    return getattr(bot, key, default)


def get_all(bot: Bot) -> dict[str, Any]:
    return {
        "decision_min_score": get(bot, "decision_min_score", None),
    }


def clear_cache(bot: Bot):
    return None
