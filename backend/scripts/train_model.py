"""One-shot trainer. Fetches ~30 days of 1-min BTC klines and trains XGBoost.

    python -m scripts.train_model
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pandas as pd

from app.ai.ml_model import BTCDirectionModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

URL = "https://api.binance.com/api/v3/klines"


async def fetch_history(days: int = 30) -> pd.DataFrame:
    end = pd.Timestamp.utcnow().floor("min")
    start = end - pd.Timedelta(days=days)
    cur = start
    all_rows = []
    async with httpx.AsyncClient(timeout=30) as c:
        while cur < end:
            r = await c.get(URL, params={
                "symbol": "BTCUSDT", "interval": "1m",
                "startTime": int(cur.timestamp() * 1000), "limit": 1000,
            })
            r.raise_for_status()
            chunk = r.json()
            if not chunk:
                break
            all_rows.extend(chunk)
            cur = pd.Timestamp(chunk[-1][6], unit="ms", tz="UTC")
            log.info("fetched up to %s (%d rows)", cur, len(all_rows))
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbb", "tbq", "ignore",
    ])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


def main():
    df = asyncio.run(fetch_history(days=30))
    log.info("dataset: %d rows", len(df))
    m = BTCDirectionModel()
    metrics = m.train(df)
    log.info("done: %s", metrics)


if __name__ == "__main__":
    main()
