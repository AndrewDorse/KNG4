"""Pure strategy math (matches research sim semantics)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Side = Literal["UP", "DOWN"]


def implied_up(btc: float, start_btc: float, sigma: float) -> float:
    x = (btc - start_btc) / max(sigma, 1e-6)
    return max(0.06, min(0.94, 0.5 + 0.44 * math.tanh(x * 1.35)))


def signal_buy_up(
    *,
    up_mid: float,
    btc: float,
    start_btc: float,
    sigma: float,
    open_edge: float,
    band_lo: float,
    band_hi: float,
) -> bool:
    if not (band_lo <= up_mid <= band_hi):
        return False
    imp = implied_up(btc, start_btc, sigma)
    return (imp - up_mid) >= open_edge


def signal_either_cheap(
    *,
    up_mid: float,
    down_mid: float,
    btc: float,
    start_btc: float,
    sigma: float,
    open_edge: float,
) -> Side | None:
    """Pick UP or DOWN by larger mispricing vs BTC-implied fair (same as PALADIN ``entry_either_cheap``)."""
    imp = implied_up(btc, start_btc, sigma)
    eu = imp - up_mid
    ed = (1.0 - imp) - down_mid
    if eu >= open_edge and ed >= open_edge:
        return "UP" if eu >= ed else "DOWN"
    if eu >= open_edge:
        return "UP"
    if ed >= open_edge:
        return "DOWN"
    return None


def buy_limit_proxy(up_mid: float, slip: float) -> float:
    return min(up_mid + slip, 0.995)


def sell_limit_proxy(up_mid: float, slip: float) -> float:
    return max(up_mid - slip, 0.005)


@dataclass(slots=True)
class OpenLeg:
    """Open leg: ``side`` UP or DOWN; exit uses outcome token mid + slip model."""

    entry_buy: float
    entry_mono: float
    shares: float
    side: Side = "UP"


def should_take_profit(*, open_: OpenLeg, position_mid: float, slip: float, min_net: float) -> bool:
    sp = sell_limit_proxy(position_mid, slip)
    return (sp - open_.entry_buy) >= min_net


def should_time_stop(*, open_: OpenLeg, now_mono: float, max_hold_sec: float) -> bool:
    return (now_mono - open_.entry_mono) >= max_hold_sec
