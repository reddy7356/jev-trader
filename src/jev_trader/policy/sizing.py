from __future__ import annotations


def kelly_fraction(p: float, cap: float = 0.25) -> float:
    """Fractional Kelly: (2p - 1) capped. Only defensible on calibrated probabilities."""
    return max(0.0, min(2.0 * p - 1.0, cap))


def inventory_skew(pressure: float, inventory: float) -> float:
    """Map inventory pressure (0-3) and sign to a quote skew in [-1, 1].

    Positive inventory (long) skews quotes down to encourage selling, negative
    inventory skews up to encourage buying.
    """
    if inventory == 0:
        return 0.0
    magnitude = max(0.0, min(1.0, pressure / 3.0))
    return -magnitude if inventory > 0 else magnitude
