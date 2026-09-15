"""Comparable setup-quality scores, not estimated probabilities of profit.

Each component measures progress from its validity threshold (0) to an
exceptional observation (1). A valid setup starts at 0.50; reaching 1.00
requires every weighted component to be exceptional.
"""
from decimal import Decimal


def bounded(value):
    value = Decimal(str(value))
    if not value.is_finite():
        return Decimal("0")
    return min(Decimal("1"), max(Decimal("0"), value))


def above_minimum(value, minimum, *, strong_multiple=Decimal("3")):
    if minimum <= 0:
        return Decimal("0")
    return bounded((value - minimum) / (minimum * (strong_multiple - 1)))


def proximity(distance, maximum):
    return bounded(1 - distance / maximum) if maximum > 0 else Decimal("0")


def score_setup(components, weights):
    if set(components) != set(weights):
        raise ValueError("Every score component must have a weight")
    weights = {key: Decimal(str(weight)) for key, weight in weights.items()}
    total = sum(weights.values())
    if total <= 0 or any(not weight.is_finite() or weight <= 0 for weight in weights.values()):
        raise ValueError("Score weights must be positive and finite")
    qualities = {key: Decimal("0.5") + bounded(value) * Decimal("0.5") for key, value in components.items()}
    score = sum(qualities[key] * weights[key] for key in qualities) / total
    return score, {key: float(value) for key, value in qualities.items()}
