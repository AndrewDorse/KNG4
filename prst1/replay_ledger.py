#!/usr/bin/env python3
"""Replay PRST1 on one public ``*_prices.csv`` window — print each BUY/SELL with PM + BTC context.

Usage (from KNG4 repo root)::

    python -m prst1.replay_ledger --csv path/to/btc-updown-15m-*_prices.csv

Uses the same math as ``strategy_core`` + engine defaults (env not required).
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root on path when: cd KNG4 && python -m prst1.replay_ledger
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from prst1.strategy_core import (
    buy_limit_proxy,
    implied_up,
    sell_limit_proxy,
    should_take_profit,
    signal_buy_up,
)
from prst1.strategy_core import OpenLeg


@dataclass(slots=True)
class Row:
    el: int
    up: float
    dn: float
    btc: float
    rem: int


def load_tape(path: Path) -> tuple[str, list[Row]]:
    with path.open(newline="", encoding="utf-8") as f:
        rdr = list(csv.DictReader(f))
    slug = (rdr[0].get("slug") or path.stem).strip() if rdr else path.stem
    rows: list[Row] = []
    for x in rdr:
        try:
            el = int(float(x["elapsed_sec"]))
        except (KeyError, ValueError):
            continue
        br = (x.get("btc_price") or "").strip()
        if not br:
            continue
        try:
            btc = float(br)
        except ValueError:
            continue
        if btc <= 0:
            continue
        try:
            up = float(x["up_price"])
            dn = float(x["down_price"])
        except (KeyError, ValueError):
            continue
        rem = int(float(x.get("remaining_sec") or 900 - el))
        rows.append(Row(el=el, up=up, dn=dn, btc=btc, rem=rem))
    rows.sort(key=lambda r: r.el)
    return slug, rows


def replay(
    rows: list[Row],
    *,
    open_edge: float,
    min_net: float,
    band_lo: float,
    band_hi: float,
    sigma: float,
    slip: float,
    max_hold_sec: float,
    max_trades: int,
    cooldown_sec: float,
    force_exit_rem: int,
    notional_usd: float,
) -> list[str]:
    """Return printable ledger lines."""
    if not rows:
        return ["(no rows)"]
    # start_btc = first row with min elapsed (same as sweep / engine first tick)
    start_btc = rows[0].btc
    start_el = rows[0].el
    lines: list[str] = []
    lines.append(f"window_start_btc (first tape row @ el={start_el}): {start_btc:.2f}")
    lines.append("")
    lines.append(
        "params: "
        f"open_edge={open_edge} min_net={min_net} band=[{band_lo},{band_hi}] "
        f"sigma={sigma} slip={slip} max_hold_sec={max_hold_sec} max_trades={max_trades} "
        f"cooldown_sec={cooldown_sec} force_exit_remaining<={force_exit_rem} notional_usd={notional_usd}"
    )
    lines.append("")

    pos: OpenLeg | None = None
    trades = 0
    next_buy_el = -10**9

    def line_action(tag: str, r: Row, extra: str) -> str:
        imp = implied_up(r.btc, start_btc, sigma)
        gap = imp - r.up
        return (
            f"{tag:4s}  el={r.el:3d}  rem={r.rem:3d}  "
            f"pm_up={r.up:.4f}  pm_down={r.dn:.4f}  btc={r.btc:.2f}  "
            f"implied_up={imp:.4f}  (implied-up)={gap:+.4f}  |  {extra}"
        )

    for r in rows:
        if r.rem <= force_exit_rem and pos is not None:
            sp = sell_limit_proxy(r.up, slip)
            net = sp - pos.entry_buy
            lines.append(
                line_action(
                    "SELL",
                    r,
                    f"FORCE_EXIT  model_exit_px={sp:.4f}  entry_buy_model={pos.entry_buy:.4f}  "
                    f"net_vs_model={net:+.4f}  shares~{notional_usd / max(pos.entry_buy, 0.01):.4f}",
                )
            )
            pos = None
            next_buy_el = r.el + int(cooldown_sec) + 1
            continue

        if pos is not None:
            tp = should_take_profit(
                open_=pos, up_mid=r.up, slip=slip, min_net=min_net
            )
            elapsed_leg = float(r.el) - (
                pos.entry_mono
            )  # OpenLeg.entry_mono stores ENTRY ELAPSED for replay
            tstop = elapsed_leg >= max_hold_sec
            if tp or tstop:
                sp = sell_limit_proxy(r.up, slip)
                net = sp - pos.entry_buy
                why = "TAKE_PROFIT" if tp else "TIME_STOP"
                lines.append(
                    line_action(
                        "SELL",
                        r,
                        f"{why}  model_exit_px={sp:.4f}  entry_buy_model={pos.entry_buy:.4f}  "
                        f"net_vs_model={net:+.4f}  hold_sec~{elapsed_leg:.0f}",
                    )
                )
                pos = None
                next_buy_el = r.el + int(cooldown_sec) + 1
            continue

        # flat
        if trades >= max_trades:
            continue
        if r.el < next_buy_el:
            continue
        if not signal_buy_up(
            up_mid=r.up,
            btc=r.btc,
            start_btc=start_btc,
            sigma=sigma,
            open_edge=open_edge,
            band_lo=band_lo,
            band_hi=band_hi,
        ):
            continue

        eb = buy_limit_proxy(r.up, slip)
        est_sh = notional_usd / max(r.up, 0.01)
        lines.append(
            line_action(
                "BUY",
                r,
                f"open_UP  model_entry_buy={eb:.4f}  est_shares={est_sh:.4f}  (spend~${notional_usd:.2f})",
            )
        )
        pos = OpenLeg(entry_buy=eb, entry_mono=float(r.el), shares=est_sh)
        trades += 1

    lines.append("")
    lines.append(f"total_round_trips (buys): {trades}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True, help="public *_prices.csv for one 15m window")
    ap.add_argument("--open-edge", type=float, default=0.065)
    ap.add_argument("--min-net", type=float, default=0.065)
    ap.add_argument("--band-lo", type=float, default=0.32)
    ap.add_argument("--band-hi", type=float, default=0.68)
    ap.add_argument("--sigma", type=float, default=130.0)
    ap.add_argument("--slip", type=float, default=0.012)
    ap.add_argument("--max-hold-sec", type=float, default=135.0)
    ap.add_argument("--max-trades", type=int, default=6)
    ap.add_argument("--cooldown-sec", type=float, default=2.0)
    ap.add_argument("--force-exit-rem", type=int, default=20)
    ap.add_argument("--notional-usd", type=float, default=1.0)
    args = ap.parse_args()
    path = args.csv.resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    slug, rows = load_tape(path)
    lines = replay(
        rows,
        open_edge=args.open_edge,
        min_net=args.min_net,
        band_lo=args.band_lo,
        band_hi=args.band_hi,
        sigma=args.sigma,
        slip=args.slip,
        max_hold_sec=args.max_hold_sec,
        max_trades=args.max_trades,
        cooldown_sec=args.cooldown_sec,
        force_exit_rem=args.force_exit_rem,
        notional_usd=args.notional_usd,
    )
    print(f"slug: {slug}")
    print(f"tape: {path}")
    print()
    for ln in lines:
        print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
