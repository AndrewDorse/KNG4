"""PRST1 live loop: BTC 15m UP token scalp (tight band vs implied fair)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prst1.clob_shim import Prst1Clob, fetch_binance_btcusdt
from prst1.gamma_market import ActiveContract, discover_active_btc_window
from prst1.settings import Prst1Settings
from prst1.strategy_core import (
    OpenLeg,
    buy_limit_proxy,
    should_take_profit,
    should_time_stop,
    signal_buy_up,
)

LOGGER = logging.getLogger("prst1")


@dataclass
class _WindowState:
    slug: str | None = None
    start_btc: float | None = None
    trades: int = 0
    next_trade_mono: float = 0.0
    open_: OpenLeg | None = None


class Prst1LiveEngine:
    def __init__(self, settings: Prst1Settings) -> None:
        self.s = settings
        self._clob = Prst1Clob(
                private_key=settings.private_key,
                funder=settings.funder,
                signature_type=settings.signature_type,
                relayer_api_key=settings.relayer_api_key,
                relayer_secret=settings.relayer_secret,
                relayer_passphrase=settings.relayer_passphrase,
            )
        self._w = _WindowState()

    def _reset_window(self, slug: str) -> None:
        if self._w.slug != slug:
            LOGGER.info("PRST1 new window %s", slug)
        self._w = _WindowState(slug=slug, next_trade_mono=0.0)

    def _flatten(self, c: ActiveContract, reason: str) -> None:
        if self._w.open_ is None or self._clob is None:
            self._w.open_ = None
            return
        sh = self._w.open_.shares
        if sh <= 0:
            self._w.open_ = None
            return
        bid = self._clob.get_best_bid(c.up.token_id)
        px = max(0.01, min(0.99, (bid or 0.01) - 0.01))
        if self.s.dry_run:
            LOGGER.info("PRST1 DRY flatten %s sh=%.4f px<=%.2f (%s)", c.slug, sh, px, reason)
        else:
            try:
                self._clob.marketable_sell(c.up, px, sh)
                LOGGER.info("PRST1 sell UP %s sh=%.4f aggressive<=%.2f (%s)", c.slug, sh, px, reason)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("PRST1 flatten failed: %s", exc)
        self._w.open_ = None

    def tick_once(self) -> None:
        c = discover_active_btc_window(
            market_symbol=self.s.market_symbol,
            window_minutes=self.s.window_minutes,
            timeout=self.s.request_timeout_seconds,
        )
        if c is None:
            return
        if self._w.slug != c.slug:
            self._reset_window(c.slug)
            self._w.slug = c.slug

        now = time.time()
        rem = c.end_time.timestamp() - now
        if rem <= float(self.s.force_exit_before_end_seconds):
            self._flatten(c, "force_exit_window")
            return

        up_mid = self._clob.get_midpoint(c.up.token_id)
        btc = fetch_binance_btcusdt(self.s.request_timeout_seconds)
        if btc is None or up_mid is None:
            return

        if self._w.start_btc is None:
            self._w.start_btc = btc
            LOGGER.info("PRST1 start_btc=%.2f slug=%s", btc, c.slug)

        # --- manage open leg ---
        if self._w.open_ is not None:
            mid = self._clob.get_midpoint(c.up.token_id)
            if mid is None:
                return
            mono = time.monotonic()
            if should_take_profit(
                open_=self._w.open_,
                up_mid=mid,
                slip=self.s.slip_model,
                min_net=self.s.min_net,
            ) or should_time_stop(
                open_=self._w.open_, now_mono=mono, max_hold_sec=self.s.max_hold_sec
            ):
                self._flatten(c, "tp_or_time")
                self._w.next_trade_mono = mono + self.s.cooldown_sec
            return

        # --- new entry ---
        if self._w.trades >= self.s.max_trades_per_window:
            return
        if time.monotonic() < self._w.next_trade_mono:
            return
        st = self._w.start_btc
        if st is None:
            return
        if not signal_buy_up(
            up_mid=up_mid,
            btc=btc,
            start_btc=st,
            sigma=self.s.sigma,
            open_edge=self.s.open_edge,
            band_lo=self.s.band_lo,
            band_hi=self.s.band_hi,
        ):
            return

        entry_buy = buy_limit_proxy(up_mid, self.s.slip_model)
        ask = self._clob.get_best_ask(c.up.token_id) or up_mid
        est_sh = self.s.notional_usd / max(ask, 0.01)

        if self.s.dry_run:
            LOGGER.info(
                "PRST1 DRY BUY signal slug=%s up_mid=%.4f btc=%.2f implied_gap trade#%d est_sh=%.3f entry~%.4f",
                c.slug,
                up_mid,
                btc,
                self._w.trades + 1,
                est_sh,
                entry_buy,
            )
            self._w.open_ = OpenLeg(
                entry_buy=entry_buy,
                entry_mono=time.monotonic(),
                shares=float(f"{est_sh:.4f}"),
            )
            self._w.trades += 1
            return

        try:
            self._clob.market_buy_usdc(c.up, self.s.notional_usd)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("PRST1 buy failed: %s", exc)
            return

        self._w.open_ = OpenLeg(
            entry_buy=entry_buy,
            entry_mono=time.monotonic(),
            shares=float(f"{est_sh:.4f}"),
        )
        self._w.trades += 1
        LOGGER.info(
            "PRST1 BUY UP slug=%s notional=%.2f USDC est_sh=%.4f entry_model=%.4f",
            c.slug,
            self.s.notional_usd,
            est_sh,
            entry_buy,
        )

    def run_forever(self) -> None:
        LOGGER.info(
            "PRST1 engine started dry_run=%s poll=%.2fs band=[%.2f,%.2f] sigma=%.1f",
            self.s.dry_run,
            self.s.poll_interval_seconds,
            self.s.band_lo,
            self.s.band_hi,
            self.s.sigma,
        )
        while True:
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001
                LOGGER.exception("PRST1 tick error")
            time.sleep(self.s.poll_interval_seconds)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
