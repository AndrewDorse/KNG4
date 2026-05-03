"""PRST1 live loop: 15m lane only (see ``PRST1_WINDOW_MINUTES`` in settings)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prst1.clob_shim import (
    Prst1Clob,
    fetch_binance_btcusdt,
    fetch_binance_window_open_btc,
)
from prst1.gamma_market import TokenMarket, discover_active_btc_window, window_start_ts_from_slug
from prst1.settings import Prst1Settings
from prst1.strategy_core import (
    OpenLeg,
    Side,
    buy_limit_proxy,
    should_take_profit,
    should_time_stop,
    signal_buy_up,
    signal_either_cheap,
)

LOGGER = logging.getLogger("prst1")


@dataclass
class _WindowState:
    slug: str | None = None
    start_btc: float | None = None
    trades: int = 0
    next_trade_mono: float = 0.0
    open_: OpenLeg | None = None


@dataclass
class _LaneState:
    """One PM window length: at most one open leg; up to N completed entries per slug."""

    w: _WindowState
    open_token: TokenMarket | None = None


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
        self._lanes: dict[int, _LaneState] = {
            wm: _LaneState(w=_WindowState(), open_token=None)
            for wm in settings.window_minutes_list
        }

    def _lane(self, wm: int) -> _LaneState:
        if wm not in self._lanes:
            self._lanes[wm] = _LaneState(w=_WindowState(), open_token=None)
        return self._lanes[wm]

    def _init_start_btc(self, lane: _LaneState, slug: str, wm: int) -> None:
        ts = window_start_ts_from_slug(slug)
        ob: float | None = None
        if ts is not None:
            ob = fetch_binance_window_open_btc(
                symbol=self.s.btc_feed_symbol,
                window_start_sec=ts,
                window_minutes=wm,
                timeout=self.s.request_timeout_seconds,
            )
        if ob is None or ob <= 0:
            ob = fetch_binance_btcusdt(
                self.s.request_timeout_seconds, symbol=self.s.btc_feed_symbol
            )
        lane.w.start_btc = ob

    def _flatten_lane(
        self, lane: _LaneState, token: TokenMarket, slug_log: str, reason: str, wm: int
    ) -> None:
        if lane.w.open_ is None:
            lane.open_token = None
            return
        sh = lane.w.open_.shares
        side = lane.w.open_.side
        if sh <= 0:
            lane.w.open_ = None
            lane.open_token = None
            return
        bid = self._clob.get_best_bid(token.token_id)
        px = max(0.01, min(0.99, (bid or 0.01) - 0.01))
        if self.s.dry_run:
            LOGGER.info(
                "DEAL_END lane=%dm slug=%s side=%s dry_run=1 reason=%s sh=%.4f px<=%.2f",
                wm,
                slug_log,
                side,
                reason,
                sh,
                px,
            )
        else:
            bal = self._clob.token_balance_allowance_refreshed(token.token_id)
            sell_sh = min(float(sh), float(bal)) if bal > 1e-8 else 0.0
            if sell_sh <= 1e-6:
                LOGGER.warning(
                    "DEAL_END lane=%dm slug=%s side=%s reason=%s skip_zero_balance",
                    wm,
                    slug_log,
                    side,
                    reason,
                )
            else:
                try:
                    self._clob.marketable_sell(token, 0.01, sell_sh)
                    LOGGER.info(
                        "DEAL_END lane=%dm slug=%s side=%s reason=%s sh=%.4f",
                        wm,
                        slug_log,
                        side,
                        reason,
                        sell_sh,
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("DEAL_END lane=%dm slug=%s sell_failed err=%s", wm, slug_log, exc)
        lane.w.open_ = None
        lane.open_token = None

    def _tick_lane(self, wm: int) -> None:
        lane = self._lane(wm)
        wst = lane.w

        c = discover_active_btc_window(
            market_symbol=self.s.market_symbol,
            window_minutes=wm,
            timeout=self.s.request_timeout_seconds,
        )
        if c is None:
            return

        slug_changed = wst.slug is None or wst.slug != c.slug
        if slug_changed:
            if wst.open_ is not None and lane.open_token is not None:
                self._flatten_lane(
                    lane, lane.open_token, wst.slug or c.slug, "WINDOW_ROLL", wm
                )
            lane.w = _WindowState(slug=c.slug, trades=0, next_trade_mono=0.0)
            lane.open_token = None
            wst = lane.w
            self._init_start_btc(lane, c.slug, wm)

        now = time.time()
        rem = c.end_time.timestamp() - now
        if rem <= float(self.s.force_exit_before_end_seconds):
            if wst.open_ is not None and lane.open_token is not None:
                self._flatten_lane(lane, lane.open_token, c.slug, "force_exit_window", wm)
            return

        up_mid = self._clob.get_midpoint(c.up.token_id)
        down_mid = self._clob.get_midpoint(c.down.token_id)
        btc = fetch_binance_btcusdt(
            self.s.request_timeout_seconds, symbol=self.s.btc_feed_symbol
        )
        if btc is None or up_mid is None or down_mid is None:
            return

        if wst.start_btc is None:
            self._init_start_btc(lane, c.slug, wm)

        st = wst.start_btc
        if st is None or st <= 0:
            return

        # --- at most one open deal per lane; exit on TP or timeout then allow next ---
        if wst.open_ is not None:
            tok = lane.open_token or c.up
            mid = self._clob.get_midpoint(tok.token_id)
            if mid is None:
                return
            mono = time.monotonic()
            if should_take_profit(
                open_=wst.open_,
                position_mid=mid,
                slip=self.s.slip_model,
                min_net=self.s.min_net,
            ) or should_time_stop(
                open_=wst.open_, now_mono=mono, max_hold_sec=self.s.max_hold_sec
            ):
                self._flatten_lane(lane, tok, c.slug, "tp_or_time", wm)
                wst.next_trade_mono = mono + self.s.cooldown_sec
            return

        # --- new $1 entry (max completed entries per window slug) ---
        if wst.trades >= self.s.max_trades_per_window:
            return
        if time.monotonic() < wst.next_trade_mono:
            return
        if rem <= float(self.s.new_order_cutoff_seconds):
            return
        if not self.s.dry_run:
            need = float(self.s.notional_usd) * 1.02
            bal = self._clob.wallet_balance_usdc()
            if bal < need:
                return

        side: Side | None = None
        if self.s.entry_mode == "TIGHT_BAND_UP":
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
            side = "UP"
        else:
            side = signal_either_cheap(
                up_mid=up_mid,
                down_mid=down_mid,
                btc=btc,
                start_btc=st,
                sigma=self.s.sigma,
                open_edge=self.s.open_edge,
            )
            if side is None:
                return

        if side == "UP":
            token = c.up
            entry_mid = up_mid
        else:
            token = c.down
            entry_mid = down_mid

        entry_buy = buy_limit_proxy(entry_mid, self.s.slip_model)
        ask = self._clob.get_best_ask(token.token_id) or entry_mid
        est_sh = self.s.notional_usd / max(ask, 0.01)

        if self.s.dry_run:
            LOGGER.info(
                "DEAL_START lane=%dm slug=%s side=%s dry_run=1 trade#%d/%d entry_model=%.4f est_sh=%.4f",
                wm,
                c.slug,
                side,
                wst.trades + 1,
                self.s.max_trades_per_window,
                entry_buy,
                est_sh,
            )
            wst.open_ = OpenLeg(
                entry_buy=entry_buy,
                entry_mono=time.monotonic(),
                shares=float(f"{est_sh:.4f}"),
                side=side,
            )
            lane.open_token = token
            wst.trades += 1
            return

        try:
            self._clob.market_buy_usdc(token, self.s.notional_usd)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("DEAL_START lane=%dm slug=%s buy_failed err=%s", wm, c.slug, exc)
            return

        wst.open_ = OpenLeg(
            entry_buy=entry_buy,
            entry_mono=time.monotonic(),
            shares=float(f"{est_sh:.4f}"),
            side=side,
        )
        lane.open_token = token
        wst.trades += 1
        LOGGER.info(
            "DEAL_START lane=%dm slug=%s side=%s trade#%d/%d notional=%.2f entry_model=%.4f est_sh=%.4f",
            wm,
            c.slug,
            side,
            wst.trades,
            self.s.max_trades_per_window,
            self.s.notional_usd,
            entry_buy,
            est_sh,
        )

    def tick_once(self) -> None:
        for wm in self.s.window_minutes_list:
            try:
                self._tick_lane(int(wm))
            except Exception:  # noqa: BLE001
                LOGGER.exception("PRST1[%dm] tick error", int(wm))

    def run_forever(self) -> None:
        lanes_s = ",".join(f"{m}m" for m in self.s.window_minutes_list)
        LOGGER.info(
            "PRST1_INIT lanes=%s entry_mode=%s dry_run=%s poll=%.2fs $%.2f/trade max=%d/window "
            "TP_min_net=%.3f slip=%.3f oe=%.3f sigma=%.1f hold=%.0fs cd=%.1fs band=[%.2f,%.2f] btc=%s",
            lanes_s,
            self.s.entry_mode,
            self.s.dry_run,
            self.s.poll_interval_seconds,
            self.s.notional_usd,
            self.s.max_trades_per_window,
            self.s.min_net,
            self.s.slip_model,
            self.s.open_edge,
            self.s.sigma,
            self.s.max_hold_sec,
            self.s.cooldown_sec,
            self.s.band_lo,
            self.s.band_hi,
            self.s.btc_feed_symbol,
        )
        while True:
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001
                LOGGER.exception("PRST1 tick_once error")
            time.sleep(self.s.poll_interval_seconds)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
