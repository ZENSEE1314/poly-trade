"""Technical features for the 5-min Up/Down classifier."""
from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Given 1-minute OHLCV, output a dataframe of TA features (last row=now)."""
    f = pd.DataFrame(index=df.index)
    close = df["close"]
    high, low, vol = df["high"], df["low"], df["volume"]

    # Returns / momentum
    f["ret_1m"] = close.pct_change(1)
    f["ret_5m"] = close.pct_change(5)
    f["ret_15m"] = close.pct_change(15)
    f["ret_60m"] = close.pct_change(60)

    # RSI / MACD / EMAs
    f["rsi_14"] = RSIIndicator(close, window=14).rsi()
    macd = MACD(close)
    f["macd"] = macd.macd()
    f["macd_signal"] = macd.macd_signal()
    f["macd_diff"] = macd.macd_diff()
    f["ema_fast"] = EMAIndicator(close, window=9).ema_indicator()
    f["ema_slow"] = EMAIndicator(close, window=21).ema_indicator()
    f["ema_ratio"] = f["ema_fast"] / f["ema_slow"] - 1

    # Volatility
    f["atr_14"] = AverageTrueRange(high, low, close, window=14).average_true_range()
    bb = BollingerBands(close, window=20)
    f["bb_pctb"] = (close - bb.bollinger_lband()) / (
        bb.bollinger_hband() - bb.bollinger_lband() + 1e-9
    )

    # Volume
    f["vol_z"] = (vol - vol.rolling(30).mean()) / (vol.rolling(30).std() + 1e-9)

    # Microstructure-ish
    body = (close - df["open"]).abs()
    rng = (high - low).replace(0, np.nan)
    f["body_frac"] = (body / rng).fillna(0)
    f["upper_wick"] = ((high - np.maximum(close, df["open"])) / rng).fillna(0)
    f["lower_wick"] = ((np.minimum(close, df["open"]) - low) / rng).fillna(0)

    return f.replace([np.inf, -np.inf], np.nan).fillna(0)


FEATURE_COLS = [
    "ret_1m", "ret_5m", "ret_15m", "ret_60m",
    "rsi_14", "macd", "macd_signal", "macd_diff",
    "ema_ratio", "atr_14", "bb_pctb", "vol_z",
    "body_frac", "upper_wick", "lower_wick",
]
