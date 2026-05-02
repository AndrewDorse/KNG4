# KNG4 — PRST1

Live engine for **PRST1** (Polymarket **BTC** Up/Down): by default **5m and 15m windows run in parallel** (separate Gamma slugs, separate state). Each lane keeps **at most one open UP position**, **`PRST1_NOTIONAL_USD` per entry** (default **$1**), and allows the next entry after TP or timeout until **`PRST1_MAX_TRADES_PER_WINDOW`** (default **10**) entries per window slug.

Tight-band **UP** scalp when Binance-implied fair exceeds the CLOB UP mid by `PRST1_OPEN_EDGE`, with take-profit / time-stop matching the research sim.

**Default parameters** match the **1000-window sweep rank #1** profile: `oe=0.065`, `mn=0.065`, band `[0.32,0.68]`, `hold=135`, `σ=130`, `slip=0.008`, `cd=2`, `max=10` trades/window **per lane**, `$1` notional (`PRST1_NOTIONAL_USD=1`). Set `PRST1_WINDOW_MINUTES=15` for 15m-only.

This repo is **standalone**: **KNG3** ships **SHAMAN v1** only; **KNG4** is the Docker home for **PRST1** (price-difference / tight-band UP scalp). Strategy research lives under `kng_bot3` (`PALADIN/sim_pm_btc_scalp_no_settle.py`).

## Quick start (local)

```bash
cd KNG4
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env    # edit keys
python -m prst1
```

Keep `POLY_DRY_RUN=true` until you validate logs against a paper checklist.

## Docker

Copy secrets first: `cp .env.example .env` (Windows: `copy`), then:

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
```

Override dry-run for **live** only after you accept risk (still pass `--env-file .env` with `POLY_DRY_RUN=false` inside).

## Environment

| Variable | Meaning |
|----------|---------|
| `POLY_PRIVATE_KEY` | Signer (required) |
| `POLY_FUNDER` | Polymarket proxy / funder `0x…` |
| `POLY_SIGNATURE_TYPE` | Usually `1` |
| `RELAYER_*` | Optional L2 API creds (same as main bot) |
| `POLY_DRY_RUN` | `true` = no orders, log only |
| `PRST1_*` | See `.env.example` (includes `PRST1_NEW_ORDER_CUTOFF_SECONDS`, `PRST1_BTC_FEED_SYMBOL`) |

## Disclaimer

Trading prediction markets is risky. This software is experimental. No warranty. You are responsible for keys, limits, and compliance.
