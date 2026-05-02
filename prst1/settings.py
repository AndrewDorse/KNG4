"""Environment-driven settings for PRST1 live engine."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


class Prst1ConfigError(RuntimeError):
    pass


def _strip(s: str | None) -> str:
    return (s or "").strip().strip('"').strip("'")


def _env_float(name: str, default: float) -> float:
    raw = _strip(os.getenv(name))
    if not raw:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = _strip(os.getenv(name))
    if not raw:
        return default
    return int(float(raw))


def _env_bool(name: str, default: bool) -> bool:
    raw = _strip(os.getenv(name))
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "y", "on")


def _parse_window_minutes_list(raw: str | None) -> tuple[int, ...]:
    """Comma-separated window lengths, e.g. ``5,15`` → simultaneous 5m and 15m BTC up/down."""
    s = _strip(raw)
    if not s:
        return (5, 15)
    out: list[int] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            v = int(float(p))
        except ValueError as exc:
            raise Prst1ConfigError(
                f"PRST1_WINDOW_MINUTES must be comma-separated integers (got {raw!r})"
            ) from exc
        if v <= 0:
            continue
        if v not in out:
            out.append(v)
    if not out:
        return (5, 15)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Prst1Settings:
    private_key: str
    funder: str
    signature_type: int
    relayer_api_key: str
    relayer_secret: str
    relayer_passphrase: str
    dry_run: bool
    poll_interval_seconds: float
    request_timeout_seconds: float
    force_exit_before_end_seconds: int
    new_order_cutoff_seconds: int
    market_symbol: str
    btc_feed_symbol: str
    window_minutes_list: tuple[int, ...]
    notional_usd: float
    open_edge: float
    min_net: float
    band_lo: float
    band_hi: float
    sigma: float
    slip_model: float
    max_hold_sec: float
    max_trades_per_window: int
    cooldown_sec: float
    log_level: str

    @classmethod
    def from_env(cls) -> Prst1Settings:
        pk = _strip(os.getenv("POLY_PRIVATE_KEY"))
        fu = _strip(os.getenv("POLY_FUNDER"))
        if not pk:
            raise Prst1ConfigError("POLY_PRIVATE_KEY is required.")
        if not fu or not re.fullmatch(r"0x[a-fA-F0-9]{40}", fu):
            raise Prst1ConfigError("POLY_FUNDER must be 0x + 40 hex.")
        return cls(
            private_key=pk,
            funder=fu,
            signature_type=_env_int("POLY_SIGNATURE_TYPE", 1),
            relayer_api_key=_strip(os.getenv("RELAYER_API_KEY")),
            relayer_secret=_strip(os.getenv("RELAYER_SECRET")),
            relayer_passphrase=_strip(os.getenv("RELAYER_PASSPHRASE")),
            dry_run=_env_bool("POLY_DRY_RUN", True),
            poll_interval_seconds=_env_float("PRST1_POLL_INTERVAL_SECONDS", 1.0),
            request_timeout_seconds=_env_float("PRST1_REQUEST_TIMEOUT_SECONDS", 12.0),
            force_exit_before_end_seconds=_env_int("PRST1_FORCE_EXIT_BEFORE_END_SECONDS", 20),
            new_order_cutoff_seconds=max(
                0, _env_int("PRST1_NEW_ORDER_CUTOFF_SECONDS", 30)
            ),
            market_symbol=_strip(os.getenv("PRST1_MARKET_SYMBOL")) or "BTC",
            btc_feed_symbol=(_strip(os.getenv("PRST1_BTC_FEED_SYMBOL")) or "BTCUSDT").upper(),
            window_minutes_list=_parse_window_minutes_list(os.getenv("PRST1_WINDOW_MINUTES")),
            notional_usd=_env_float("PRST1_NOTIONAL_USD", 1.0),
            open_edge=_env_float("PRST1_OPEN_EDGE", 0.065),
            min_net=_env_float("PRST1_MIN_NET", 0.12),
            band_lo=_env_float("PRST1_BAND_LO", 0.32),
            band_hi=_env_float("PRST1_BAND_HI", 0.68),
            sigma=_env_float("PRST1_SIGMA_BTC", 130.0),
            slip_model=_env_float("PRST1_SLIP_MODEL", 0.008),
            max_hold_sec=_env_float("PRST1_MAX_HOLD_SEC", 135.0),
            max_trades_per_window=max(
                1, min(50, _env_int("PRST1_MAX_TRADES_PER_WINDOW", 10))
            ),
            cooldown_sec=_env_float("PRST1_COOLDOWN_SEC", 2.0),
            log_level=_strip(os.getenv("PRST1_LOG_LEVEL")) or "INFO",
        )
