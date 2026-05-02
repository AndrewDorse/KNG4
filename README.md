# KNG4 — PRST1

Live engine for **PRST1** (Polymarket **BTC** Up/Down): by default **5m and 15m windows run in parallel** (separate Gamma slugs, separate state). Each lane keeps **at most one open UP position**, **`PRST1_NOTIONAL_USD` per entry** (default **$1**), and allows the next entry after TP or timeout until **`PRST1_MAX_TRADES_PER_WINDOW`** (default **10**) entries per window slug.

Tight-band **UP** scalp when Binance-implied fair exceeds the CLOB UP mid by `PRST1_OPEN_EDGE`, with take-profit / time-stop matching the research sim (`PALADIN/sim_pm_btc_scalp_no_settle.py`).

**Defaults:** `oe=0.065`, **`PRST1_MIN_NET=0.12` (12¢ TP gate)**, band `[0.32,0.68]`, `hold=135`, `σ=130`, `slip=0.008`, `cd=2`, max **10** trades/window **per lane**, **`$1`** notional. Set `PRST1_WINDOW_MINUTES=15` for 15m-only.

## CLOB & feeds (trade-related)

| Piece | Source |
|--------|--------|
| **Orders** | **`py_clob_client_v2` only** — `create_and_post_market_order` (FAK USDC buy), `create_and_post_order` (FAK sell). No v1 fallback; `requirements.txt` pins `py_clob_client_v2`. |
| **PM UP mid / bid / ask** | CLOB `get_order_book` → best bid/ask, midpoint for signals and TP check. |
| **PM market discovery** | Gamma `GET /markets?slug=btc-updown-{5\|15}m-{epoch}` (`prst1/gamma_market.py`). |
| **BTC spot (signal)** | Binance `GET /api/v3/ticker/price?symbol=BTCUSDT` (`PRST1_BTC_FEED_SYMBOL`). |
| **Window-open BTC** | Binance klines `startTime=slug_epoch`, interval `5m`/`15m` (`fetch_binance_window_open_btc`). |
| **Collateral pre-buy** | `wallet_balance_usdc()` must exceed `notional × 1.02` or buy is skipped. |
| **Exit size** | Sell size = `min(ledger_shares, token_balance_allowance_refreshed)` to avoid oversell. |

**KNG3** remains **SHAMAN v1** only; **KNG4** is this PRST1 image.

## Quick start (local)

```bash
cd KNG4
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
python -c "import py_clob_client_v2; print('v2 ok')"
copy .env.example .env    # edit keys
python -m prst1
```

Keep `POLY_DRY_RUN=true` until logs look correct.

## Docker (go live)

1. `copy .env.example .env` — set **`POLY_PRIVATE_KEY`**, **`POLY_FUNDER`**, relayer creds if used.  
2. Set **`POLY_DRY_RUN=false`** only when ready for real FAK orders.  
3. `docker compose --env-file .env build && docker compose --env-file .env up -d`  
4. `docker compose logs -f prst1` — confirm startup line includes **`CLOB=v2`**, **`TP_min_net=0.12`**, lane list.

## Environment

| Variable | Meaning |
|----------|---------|
| `POLY_PRIVATE_KEY` | Signer (required) |
| `POLY_FUNDER` | Polymarket proxy / funder `0x…` |
| `POLY_SIGNATURE_TYPE` | Usually `1` |
| `RELAYER_*` | Optional L2 API creds |
| `POLY_DRY_RUN` | `true` = no orders |
| `PRST1_MIN_NET` | TP threshold (default **0.12** = 12¢/share net after slip model) |
| `PRST1_*` | See `.env.example` |

## Disclaimer

Trading prediction markets is risky. This software is experimental. No warranty. You are responsible for keys, limits, and compliance.
