"""Thin wrapper around Polymarket Gamma + CLOB.

BTC 5-min Up/Down markets use a deterministic slug derived from the unix
window start (rounded to 300 seconds):

    slug = f"bitcoin-up-or-down-{window_ts}"   (modern)
    slug = f"btc-updown-5m-{window_ts}"        (legacy)

We resolve the active market by deterministic slug → fall back to Gamma search.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from ..core.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

WINDOW_SECS = 300


def current_window_ts(now: int | None = None) -> int:
    now = now or int(time.time())
    return now - (now % WINDOW_SECS)


def next_window_ts(now: int | None = None) -> int:
    return current_window_ts(now) + WINDOW_SECS


@dataclass
class MarketSnapshot:
    condition_id: str
    slug: str
    up_token_id: str
    down_token_id: str
    up_best_bid: float
    up_best_ask: float
    down_best_bid: float
    down_best_ask: float
    end_date_ts: int


class PolymarketClient:
    def __init__(self):
        self.gamma = settings.POLYMARKET_GAMMA_HOST
        self.clob = settings.POLYMARKET_CLOB_HOST

    async def find_btc_market(self, window_ts: int) -> MarketSnapshot | None:
        slugs = [
            f"bitcoin-up-or-down-{window_ts}",
            f"btc-updown-5m-{window_ts}",
        ]
        async with httpx.AsyncClient(timeout=8) as c:
            for slug in slugs:
                r = await c.get(f"{self.gamma}/markets", params={"slug": slug})
                if r.status_code != 200:
                    continue
                arr = r.json()
                if not arr:
                    continue
                m = arr[0] if isinstance(arr, list) else arr
                return await self._enrich(c, m)
        return None

    async def _enrich(self, c: httpx.AsyncClient, m: dict) -> MarketSnapshot:
        token_ids = m.get("clobTokenIds") or m.get("clob_token_ids")
        if isinstance(token_ids, str):
            import json
            token_ids = json.loads(token_ids)
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            import json
            outcomes = json.loads(outcomes)
        # Polymarket up/down markets list Up first by convention
        up_idx = 0 if "up" in str(outcomes[0]).lower() else 1
        down_idx = 1 - up_idx

        async def book(tid: str) -> tuple[float, float]:
            r = await c.get(f"{self.clob}/book", params={"token_id": tid})
            if r.status_code != 200:
                return (0.0, 1.0)
            b = r.json()
            bids = b.get("bids") or []
            asks = b.get("asks") or []
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0
            return best_bid, best_ask

        up_bb, up_ba = await book(token_ids[up_idx])
        dn_bb, dn_ba = await book(token_ids[down_idx])

        return MarketSnapshot(
            condition_id=m.get("conditionId") or m.get("condition_id", ""),
            slug=m.get("slug", ""),
            up_token_id=str(token_ids[up_idx]),
            down_token_id=str(token_ids[down_idx]),
            up_best_bid=up_bb,
            up_best_ask=up_ba,
            down_best_bid=dn_bb,
            down_best_ask=dn_ba,
            end_date_ts=int(m.get("endDateTs") or 0),
        )


# ─── Order placement: real vs paper ──────────────────────────────────────

@dataclass
class OrderRequest:
    token_id: str
    side: str         # "BUY" | "SELL"
    price: float      # 0..1
    size: float       # USDC notional


@dataclass
class OrderResult:
    success: bool
    order_id: str
    filled_size: float
    avg_price: float
    raw: dict


async def paper_submit(req: OrderRequest, market_ask: float) -> OrderResult:
    """Simulate a fill at the resting ask, assuming the order takes liquidity."""
    fill_price = min(req.price, market_ask)
    return OrderResult(
        success=True,
        order_id=f"paper-{int(time.time()*1000)}",
        filled_size=req.size,
        avg_price=fill_price,
        raw={"paper": True},
    )


async def live_submit(
    req: OrderRequest, eoa_private_key: str, funder_address: str | None = None
) -> OrderResult:
    """Real order via py-clob-client. Requires a live wallet.

    NOTE: keep imports local so the worker boots even when this dep is broken
    in a particular environment.
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL

    client = ClobClient(
        host=settings.POLYMARKET_CLOB_HOST,
        key=eoa_private_key,
        chain_id=settings.POLYMARKET_CHAIN_ID,
        funder=funder_address,
        signature_type=2 if funder_address else 0,
    )
    # L2 API creds — derive if not provided
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)

    args = OrderArgs(
        price=round(req.price, 3),
        size=round(req.size, 2),
        side=BUY if req.side == "BUY" else SELL,
        token_id=req.token_id,
    )
    signed = client.create_order(args)
    resp = client.post_order(signed, OrderType.FOK)  # fill-or-kill: never sit on the book
    success = bool(resp.get("success"))
    return OrderResult(
        success=success,
        order_id=str(resp.get("orderID", "")),
        filled_size=float(resp.get("makingAmount", 0)) if success else 0.0,
        avg_price=req.price,
        raw=resp,
    )
