"""Pull recent BTC OHLCV from Binance (no API key required for public klines)."""
from __future__ import annotations

import httpx
import pandas as pd

BINANCE = "https://api.binance.com/api/v3/klines"
COINBASE_TICKER = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


async def fetch_klines(interval: str = "1m", limit: int = 200) -> pd.DataFrame:
    params = {"symbol": "BTCUSDT", "interval": interval, "limit": limit}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(BINANCE, params=params)
        r.raise_for_status()
        rows = r.json()
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbb", "tbq", "ignore",
        ],
    )
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


async def spot_price() -> float:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(COINBASE_TICKER)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])
