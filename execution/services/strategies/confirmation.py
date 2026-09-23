"""Confirm a pullback using only the next completed candle."""
from dataclasses import replace

from execution.services.engine_types import EngineDecision
from execution.services.entry_contract import target_at_entry


def confirm_pullback(setup, setup_bar, confirmation):
    if setup.action != "open":
        return setup
    buy = setup.direction == "buy"
    trigger = setup_bar["high"] if buy else setup_bar["low"]
    metadata = {**(setup.metadata or {}), "confirmation": "next_closed_candle"}
    invalidated = (
        setup.sl is None
        or (confirmation["low"] <= setup.sl if buy else confirmation["high"] >= setup.sl)
    )
    if invalidated:
        return EngineDecision(action="skip", strategy=setup.strategy,
                              reason=f"{setup.strategy}_setup_invalidated", metadata=metadata)
    entry = confirmation["close"]
    confirmed = (
        entry > trigger and entry > confirmation["open"]
        if buy else entry < trigger and entry < confirmation["open"]
    )
    if not confirmed:
        return EngineDecision(action="skip", strategy=setup.strategy,
                              reason=f"{setup.strategy}_confirmation_failed", metadata=metadata)
    target = target_at_entry(setup.direction, entry, setup.sl, setup.target_rr, trigger)
    return replace(setup, entry_price=entry, entry_trigger=trigger, tp=target, metadata=metadata)
