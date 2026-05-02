"""Polymarket CLOB — **py_clob_client_v2 only** (market FAK buys, FAK sells, book, balances).

PM **UP** mid: ``get_order_book`` via CLOB (best bid / best ask). BTC: public Binance REST (see
``fetch_binance_*``). No v1 fallback: install ``py_clob_client_v2`` per ``requirements.txt``."""

from __future__ import annotations

import logging
import threading
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any

import requests

from prst1.gamma_market import TokenMarket

LOGGER = logging.getLogger("prst1")

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

from py_clob_client_v2 import (  # noqa: E402  — v2 required for live trading
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    ClobClient,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)


def _parse_balance_allowance(resp: Any) -> float:
    if isinstance(resp, dict):
        raw = resp.get("balance")
    else:
        raw = getattr(resp, "balance", None) or resp
    if raw is None or raw == "":
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v > 1_000_000:
        return v / 1_000_000.0
    return v


def _clob_taker_size_shares(size: float) -> float:
    if size <= 0:
        return 0.0
    q = Decimal(str(float(size))).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    return float(f"{float(q):.4f}")


def _norm_tick(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    return s if s in {"0.1", "0.01", "0.001", "0.0001"} else None


class Prst1Clob:
    """Single-flight taker orders + v2-native market buy/sell (FAK), matching KNG3 patterns."""

    def __init__(
        self,
        *,
        private_key: str,
        funder: str,
        signature_type: int,
        relayer_api_key: str,
        relayer_secret: str,
        relayer_passphrase: str,
    ) -> None:
        self._signature_type = int(signature_type)
        self._buy = Side.BUY
        self._sell = Side.SELL
        self._taker_lock = threading.Lock()
        self.client = ClobClient(
            HOST,
            chain_id=CHAIN_ID,
            key=private_key,
            signature_type=signature_type,
            funder=funder,
        )
        if relayer_api_key:
            self.client.set_api_creds(
                ApiCreds(
                    api_key=relayer_api_key,
                    api_secret=relayer_secret or "",
                    api_passphrase=relayer_passphrase or "",
                )
            )
        else:
            creds = self.client.derive_api_key()
            if creds is None:
                creds = self.client.create_api_key(int(time.time() * 1000))
            self.client.set_api_creds(creds)
        try:
            self.client.update_balance_allowance(
                BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL,
                    signature_type=signature_type,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Collateral allowance sync: %s", exc)
        LOGGER.info("PRST1 CLOB: py_clob_client_v2 host=%s chain=%s", HOST, CHAIN_ID)

    def _book_opts(self, token: TokenMarket) -> PartialCreateOrderOptions | None:
        tid = token.token_id
        tick = None
        neg = None
        try:
            tick = _norm_tick(self.client.get_tick_size(tid))
        except Exception:
            tick = _norm_tick(token.minimum_tick_size)
        try:
            neg = bool(self.client.get_neg_risk(tid))
        except Exception:
            neg = token.neg_risk
        if tick is None and neg is None:
            LOGGER.warning(
                "PRST1 CLOB: missing tick_size/neg_risk for token %s…; market order options omitted",
                tid[:20],
            )
            return None
        return PartialCreateOrderOptions(
            tick_size=tick,
            neg_risk=bool(neg) if neg is not None else None,
        )

    def _create_and_post_market_order(
        self, margs: MarketOrderArgs, options: PartialCreateOrderOptions | None
    ) -> dict[str, Any]:
        create_and_post = getattr(self.client, "create_and_post_market_order", None)
        if not callable(create_and_post):
            raise RuntimeError("ClobClient.create_and_post_market_order missing (need py_clob_client_v2)")
        ot = margs.order_type or OrderType.FAK
        try:
            return create_and_post(margs, options=options, order_type=ot)
        except TypeError:
            return create_and_post(margs, options=options)

    def wallet_balance_usdc(self) -> float:
        try:
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL,
                    signature_type=self._signature_type,
                )
            )
            return _parse_balance_allowance(resp)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("wallet_balance_usdc: %s", exc)
            return 0.0

    def token_balance_allowance_refreshed(self, token_id: str) -> float:
        try:
            self.client.update_balance_allowance(
                BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                    signature_type=self._signature_type,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("update_balance_allowance conditional %s: %s", token_id[:16], exc)
        try:
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                    signature_type=self._signature_type,
                )
            )
            return _parse_balance_allowance(resp)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("token_balance: %s", exc)
            return 0.0

    def _normalize_side(self, entries: list[Any]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for e in entries or []:
            if isinstance(e, dict):
                p = str(e.get("price", ""))
                z = str(e.get("size", ""))
            else:
                p = str(getattr(e, "price", "") or "")
                z = str(getattr(e, "size", "") or "")
            if p and z:
                out.append({"price": p, "size": z})
        return out

    def get_order_book(self, token_id: str) -> dict[str, list[dict[str, str]]]:
        try:
            book = self.client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("get_order_book: %s", exc)
            return {"bids": [], "asks": []}
        if isinstance(book, dict):
            return {
                "bids": self._normalize_side(book.get("bids")),
                "asks": self._normalize_side(book.get("asks")),
            }
        return {
            "bids": self._normalize_side(getattr(book, "bids", None) or []),
            "asks": self._normalize_side(getattr(book, "asks", None) or []),
        }

    def get_midpoint(self, token_id: str) -> float | None:
        bb = self.get_best_bid(token_id)
        ba = self.get_best_ask(token_id)
        if bb is not None and ba is not None and bb > 0 and ba > 0:
            return (bb + ba) / 2.0
        if ba is not None and ba > 0:
            return float(ba)
        if bb is not None and bb > 0:
            return float(bb)
        return None

    def get_best_bid(self, token_id: str) -> float | None:
        b = self.get_order_book(token_id)
        bids = b.get("bids") or []
        if not bids:
            return None
        best = None
        for x in bids:
            p = float(x.get("price", 0))
            if p > 0 and (best is None or p > best):
                best = p
        return best

    def get_best_ask(self, token_id: str) -> float | None:
        b = self.get_order_book(token_id)
        asks = b.get("asks") or []
        if not asks:
            return None
        best = None
        for x in asks:
            p = float(x.get("price", 0))
            if p > 0 and (best is None or p < best):
                best = p
        return best

    def market_buy_usdc(self, token: TokenMarket, usdc: float) -> dict[str, Any]:
        u = float(usdc)
        if u <= 0:
            raise ValueError("usdc must be > 0")
        opts = self._book_opts(token)
        margs = MarketOrderArgs(
            token_id=token.token_id,
            amount=u,
            side=self._buy,
            price=0.0,
            order_type=OrderType.FAK,
        )
        with self._taker_lock:
            return self._create_and_post_market_order(margs, options=opts)

    def marketable_sell(self, token: TokenMarket, price: float, size: float) -> dict[str, Any]:
        """Aggressive sell (FAK); matches KNG3 ``place_marketable_sell`` (no book options on limit path)."""
        order = OrderArgs(
            token_id=token.token_id,
            price=round(float(price), 2),
            size=_clob_taker_size_shares(size),
            side=self._sell,
        )
        cap = getattr(self.client, "create_and_post_order", None)
        with self._taker_lock:
            if callable(cap):
                try:
                    return cap(
                        order_args=order,
                        options=None,
                        order_type=OrderType.FAK,
                        post_only=False,
                    )
                except TypeError:
                    return cap(order, None, OrderType.FAK)
            signed = self.client.create_order(order)
            return self.client.post_order(signed, OrderType.FAK)


_BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def _binance_interval_for_window(window_minutes: int) -> str:
    wm = int(window_minutes)
    if wm <= 5:
        return "5m"
    if wm <= 15:
        return "15m"
    return "15m"


def fetch_binance_btcusdt(timeout: float, *, symbol: str = "BTCUSDT") -> float | None:
    url = "https://api.binance.com/api/v3/ticker/price"
    try:
        r = requests.get(url, params={"symbol": symbol.upper()}, timeout=timeout)
        r.raise_for_status()
        return float(r.json()["price"])
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        LOGGER.warning("Binance BTC price: %s", exc)
        return None


def fetch_binance_window_open_btc(
    *,
    symbol: str,
    window_start_sec: int,
    window_minutes: int,
    timeout: float,
) -> float | None:
    """Candle **open** at ``window_start_sec`` (aligns PM slug epoch with Binance kline open)."""
    try:
        start_ms = int(window_start_sec) * 1000
        interval = _binance_interval_for_window(window_minutes)
        r = requests.get(
            _BINANCE_KLINES,
            params={
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": start_ms,
                "limit": 1,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        o = float(rows[0][1])
        return o if o > 0 else None
    except (requests.RequestException, IndexError, KeyError, ValueError, TypeError) as exc:
        LOGGER.warning("Binance kline open: %s", exc)
        return None
