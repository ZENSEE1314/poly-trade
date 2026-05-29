"""Pull recent BTC OHLCV — Kraken primary, Coinbase spot fallback.

Binance is blocked (HTTP 451) from Railway EU servers for legal reasons,
so all klines now come from Kraken's public OHLC endpoint.
"""
from __future__ import annotations

import time as _time

import httpx
import pandas as pd

KRAKEN_OHLC    = "https://api.kraken.com/0/public/OHLC"
KRAKEN_TICKER  = "https://api.kraken.com/0/public/Ticker"
COINBASE_TICKER = "https://api.coinbase.com/v2/prices/BTC-USD/spot"

# Kraken interval in minutes → param value
_INTERVAL_MAP = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


async def fetch_klines(interval: str = "1m", limit: int = 200) -> pd.DataFrame:
    """Fetch BTC/USD OHLCV candles from Kraken.

    Kraken caps at 720 rows per call; if limit > 720 we page backwards
    to collect more history (used by the retrainer).
    """
    kraken_interval = _INTERVAL_MAP.get(interval, 1)
    # `since` must be a Unix timestamp — request enough history for `limit` candles
    since = int(_time.time()) - limit * kraken_interval * 60 - 60  # extra 1-min buffer

    all_rows: list = []
    async with httpx.AsyncClient(timeout=15, verify=False) as c:
        # Kraken returns max 720 rows; page if needed
        remaining = limit
        fetch_since = since
        while remaining > 0:
            r = await c.get(KRAKEN_OHLC, params={
                "pair": "XBTUSD",
                "interval": kraken_interval,
                "since": fetch_since,
            })
            r.raise_for_status()
            data = r.json()
            if data.get("error"):
                raise ValueError(f"Kraken error: {data['error']}")

            result = data.get("result", {})
            pair_data = (
                result.get("XXBTZUSD")
                or result.get("XBTUSD")
                or next((v for k, v in result.items() if k != "last"), None)
            )
            if not pair_data:
                break

            all_rows.extend(pair_data)
            remaining -= len(pair_data)

            # Kraken `last` = timestamp of last candle; use for next page
            last_ts = result.get("last")
            if not last_ts or len(pair_data) < 720:
                break   # no more pages
            fetch_since = last_ts

    if not all_rows:
        raise ValueError("Kraken returned no OHLC data")

    df = pd.DataFrame(
        all_rows,
        columns=["open_time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    # Kraken open_time is Unix seconds → convert to pandas UTC datetime
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="s", utc=True)
    df = (
        df.drop_duplicates("open_time")
          .sort_values("open_time")
          .tail(limit)
          .reset_index(drop=True)
    )
    return df


async def spot_price() -> float:
    """Current BTC/USD spot price — Kraken primary, Coinbase fallback."""
    try:
        async with httpx.AsyncClient(timeout=5, verify=False) as c:
            r = await c.get(KRAKEN_TICKER, params={"pair": "XBTUSD"})
            r.raise_for_status()
            info = r.json()["result"]["XXBTZUSD"]
            return float(info["c"][0])
    except Exception:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(COINBASE_TICKER)
            r.raise_for_status()
            return float(r.json()["data"]["amount"])
