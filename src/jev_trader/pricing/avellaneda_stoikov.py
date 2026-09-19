from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Quote:
    reservation: float
    bid: float
    ask: float
    half_spread: float


def reservation_price(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    tau: float,
) -> float:
    """A-S reservation price: mid - q * gamma * sigma^2 * tau.

    `inventory` is normalized to [-1, 1] by max position, `sigma` is per-second
    volatility, `tau` is the remaining horizon in seconds.
    """
    return mid - inventory * gamma * sigma * sigma * tau


def optimal_half_spread(gamma: float, sigma: float, tau: float, kappa: float) -> float:
    """A-S optimal half spread: gamma * sigma^2 * tau + (2/gamma) * ln(1 + gamma/kappa)."""
    return gamma * sigma * sigma * tau + (2.0 / gamma) * math.log1p(gamma / kappa)


def compute_quote(
    *,
    mid: float,
    inventory: float,
    sigma: float,
    tau: float,
    gamma: float,
    kappa: float,
    min_half_spread: float,
    skew: float = 0.0,
    half_spread_multiplier: float = 1.0,
    tick_size: float = 0.0,
) -> Quote:
    half_spread = max(
        optimal_half_spread(gamma, sigma, tau, kappa) * half_spread_multiplier,
        min_half_spread,
    )
    reservation = reservation_price(mid, inventory, gamma, sigma, tau) + skew * half_spread
    bid = reservation - half_spread
    ask = reservation + half_spread
    if tick_size > 0:
        bid = math.floor(bid / tick_size) * tick_size
        ask = math.ceil(ask / tick_size) * tick_size
    return Quote(reservation=reservation, bid=bid, ask=ask, half_spread=half_spread)
