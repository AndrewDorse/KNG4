# KNG4 — PRST1

Live engine for **PRST1** (Polymarket **BTC 15m** Up/Down): tight-band **UP** scalp when Binance-implied fair value exceeds the CLOB UP midpoint by `PRST1_OPEN_EDGE`, with take-profit / time-stop from backtested sim semantics.

This repo is **standalone** from `kng_bot3` / KNG3. Strategy research lived under `kng_bot3` (`PALADIN/sim_pm_btc_scalp_no_settle.py`).

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
| `PRST1_*` | See `.env.example` |

## Disclaimer

Trading prediction markets is risky. This software is experimental. No warranty. You are responsible for keys, limits, and compliance.
