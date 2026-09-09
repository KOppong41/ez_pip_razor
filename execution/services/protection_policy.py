"""Shared entry/exit protection policy used by every execution layer."""


MANAGED_EXIT_MODES = {"hybrid", "trail_only"}


def decision_exit_mode(decision) -> str:
    params = getattr(decision, "params", None) or {}
    scalper = params.get("scalper") or {}
    return str(scalper.get("exit_mode") or "fixed_tp").lower()


def validate_protection(*, intent: str, sl, tp, exit_mode: str = "fixed_tp"):
    """Return ``(valid, reason)`` for an order's semantic protection policy."""
    if intent != "entry":
        return True, "ok"
    if sl is None:
        return False, "missing_sl"
    if str(exit_mode or "fixed_tp").lower() == "fixed_tp" and tp is None:
        return False, "missing_tp"
    return True, "ok"


def validate_order_protection(order):
    return validate_protection(
        intent=getattr(order, "intent", "entry"),
        sl=getattr(order, "sl", None),
        tp=getattr(order, "tp", None),
        exit_mode=decision_exit_mode(getattr(order, "decision", None)),
    )

