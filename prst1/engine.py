"""PRST1 live loop: BTC Up/Down UP token scalp (tight band vs implied fair)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prst1.clob_shim import (
    Prst1Clob,
    fetch_binance_btcusdt,
    fetch_binance_window_open_btc,
)
from prst1.gamma_market import ActiveContract, TokenMarket, discover_active_btc_window, window_start_ts_from_slug
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
        self._open_up: TokenMarket | None = None

    def _init_start_btc(self, slug: str) -> None:
        ts = window_start_ts_from_slug(slug)
        ob: float | None = None
        if ts is not None:
            ob = fetch_binance_window_open_btc(
                symbol=self.s.btc_feed_symbol,
                window_start_sec=ts,
                window_minutes=self.s.window_minutes,
                timeout=self.s.request_timeout_seconds,
            )
        if ob is None or ob <= 0:
            ob = fetch_binance_btcusdt(
                self.s.request_timeout_seconds, symbol=self.s.btc_feed_symbol
            )
        self._w.start_btc = ob
        LOGGER.info(
            "PRST1 start_btc=%s slug=%s (window-open kline or spot fallback)",
            f"{ob:.2f}" if ob else "None",
            slug,
        )

    def _flatten(self, up: TokenMarket, slug_log: str, reason: str) -> None:
        if self._w.open_ is None:
            self._open_up = None
            return
        sh = self._w.open_.shares
        if sh <= 0:
            self._w.open_ = None
            self._open_up = None
            return
        bid = self._clob.get_best_bid(up.token_id)
        px = max(0.01, min(0.99, (bid or 0.01) - 0.01))
        if self.s.dry_run:
            LOGGER.info(
                "PRST1 DRY flatten slug=%s sh=%.4f px<=%.2f (%s)",
                slug_log,
                sh,
                px,
                reason,
            )
        else:
            try:
                self._clob.marketable_sell(up, 0.01, sh)
                LOGGER.info(
                    "PRST1 sell UP slug=%s sh=%.4f aggressive<=0.01 (%s)",
                    slug_log,
                    sh,
                    reason,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("PRST1 flatten failed: %s", exc)
        self._w.open_ = None
        self._open_up = None

    def tick_once(self) -> None:
        c = discover_active_btc_window(
            market_symbol=self.s.market_symbol,
            window_minutes=self.s.window_minutes,
            timeout=self.s.request_timeout_seconds,
        )
        if c is None:
            return

        slug_changed = self._w.slug is None or self._w.slug != c.slug
        if slug_changed:
            if self._w.open_ is not None and self._open_up is not None:
                self._flatten(self._open_up, self._w.slug or c.slug, "WINDOW_ROLL")
            self._w = _WindowState(slug=c.slug, trades=0, next_trade_mono=0.0)
            self._open_up = None
            self._init_start_btc(c.slug)

        now = time.time()
        rem = c.end_time.timestamp() - now
        if rem <= float(self.s.force_exit_before_end_seconds):
            if self._w.open_ is not None and self._open_up is not None:
                self._flatten(self._open_up, c.slug, "force_exit_window")
            return

        up_mid = self._clob.get_midpoint(c.up.token_id)
        btc = fetch_binance_btcusdt(
            self.s.request_timeout_seconds, symbol=self.s.btc_feed_symbol
        )
        if btc is None or up_mid is None:
            return

        if self._w.start_btc is None:
            self._init_start_btc(c.slug)

        st = self._w.start_btc
        if st is None or st <= 0:
            return

        # --- manage open leg ---
        if self._w.open_ is not None:
            mid = self._clob.get_midpoint(c.up.token_id)
            if mid is None:
                return
            mono = time.monotonic()
            up_tok = self._open_up or c.up
            if should_take_profit(
                open_=self._w.open_,
                up_mid=mid,
                slip=self.s.slip_model,
                min_net=self.s.min_net,
            ) or should_time_stop(
                open_=self._w.open_, now_mono=mono, max_hold_sec=self.s.max_hold_sec
            ):
                self._flatten(up_tok, c.slug, "tp_or_time")
                self._w.next_trade_mono = mono + self.s.cooldown_sec
            return

        # --- new entry ---
        if self._w.trades >= self.s.max_trades_per_window:
            return
        if time.monotonic() < self._w.next_trade_mono:
            return
        if rem <= float(self.s.new_order_cutoff_seconds):
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
                "PRST1 DRY BUY signal slug=%s up_mid=%.4f btc=%.2f trade#%d est_sh=%.3f entry~%.4f",
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
            self._open_up = c.up
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
        self._open_up = c.up
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
            "PRST1 engine started dry_run=%s poll=%.2fs band=[%.2f,%.2f] sigma=%.1f btc=%s",
            self.s.dry_run,
            self.s.poll_interval_seconds,
            self.s.band_lo,
            self.s.band_hi,
            self.s.sigma,
            self.s.btc_feed_symbol,
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
