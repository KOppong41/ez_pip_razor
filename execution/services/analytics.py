from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Counter as TCounter

from collections import Counter

from django.utils import timezone

from bots.models import Bot
from execution.models import Decision


@dataclass
class BotStats:
    bot_id: int
    bot_name: str
    from_dt: datetime
    to_dt: datetime

    total_decisions: int
    opens: int
    closes: int
    ignores: int

    open_rate: float          # opens / total_decisions
    ignore_rate: float        # ignores / total_decisions

    ignores_by_reason: Dict[str, int]
    last_decision_at: datetime | None


def _default_range(days: int = 7) -> tuple[datetime, datetime]:
    now = timezone.now()
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now
    return start, end


def get_bot_stats(
    bot: Bot,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> BotStats:
    """
    Basic per-bot stats over a time range, using Decision:

      - total_decisions
      - opens / closes / ignores
      - open_rate / ignore_rate
      - ignores_by_reason (why the bot was blocked)
    """
    if from_dt is None or to_dt is None:
        from_dt, to_dt = _default_range()

    qs = Decision.objects.filter(
        bot=bot,
        decided_at__gte=from_dt,
        decided_at__lte=to_dt,
    )

    total = qs.count()
    opens = qs.filter(action="open").count()
    closes = qs.filter(action="close").count()
    ignores_qs = qs.filter(action="ignore")
    ignores = ignores_qs.count()

    # rates
    if total > 0:
        open_rate = opens / total
        ignore_rate = ignores / total
    else:
        open_rate = 0.0
        ignore_rate = 0.0

    # why decisions were ignored (risk, limits, etc.)
    reasons_counter: TCounter[str] = Counter(
        ignores_qs.values_list("reason", flat=True)
    )

    last = qs.order_by("-decided_at").values_list("decided_at", flat=True).first()

    return BotStats(
        bot_id=bot.id,
        bot_name=bot.name,
        from_dt=from_dt,
        to_dt=to_dt,
        total_decisions=total,
        opens=opens,
        closes=closes,
        ignores=ignores,
        open_rate=open_rate,
        ignore_rate=ignore_rate,
        ignores_by_reason=dict(reasons_counter),
        last_decision_at=last,
    )


def get_all_bots_stats(days: int = 7) -> Dict[int, BotStats]:
    """
    Stats for all active bots over last N days.
    """
    from_dt, to_dt = _default_range(days)
    stats: Dict[int, BotStats] = {}

    for bot in Bot.objects.filter(status="active"):
        stats[bot.id] = get_bot_stats(bot, from_dt, to_dt)

    return stats
