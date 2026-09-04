"""Celery priorities for the single serialized MT5 execution queue.

Celery/Kombu uses lower numeric values as higher priority for Redis and for the
desktop priority-aware filesystem transport configured by this project.
"""

MT5_PRIORITY_EMERGENCY = 0
MT5_PRIORITY_HIGH = 3
MT5_PRIORITY_NORMAL = 6
MT5_PRIORITY_LOW = 9
MT5_MAX_PRIORITY = 9
MT5_PRIORITY_STEPS = [
    MT5_PRIORITY_EMERGENCY,
    MT5_PRIORITY_HIGH,
    MT5_PRIORITY_NORMAL,
    MT5_PRIORITY_LOW,
]


def priority_for_order(order, *, emergency: bool = False) -> int:
    if emergency:
        return MT5_PRIORITY_EMERGENCY
    if getattr(order, "intent", "entry") == "exit":
        return MT5_PRIORITY_HIGH
    return MT5_PRIORITY_NORMAL
