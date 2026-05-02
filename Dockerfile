# KNG4 — PRST1 Polymarket BTC up/down scalp (default 5m+15m lanes, dry-run capable)
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prst1/ ./prst1/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default safe: log only. Override with -e POLY_DRY_RUN=false in compose/run.
ENV POLY_DRY_RUN=true

CMD ["python", "-m", "prst1"]
