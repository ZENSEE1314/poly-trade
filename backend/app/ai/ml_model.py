"""Gradient-boosted classifier for P(BTC close in 5min > current price).

Provides:
  - train(df) -> persists model.json
  - predict_proba(features_row) -> float in [0,1]

The model file ships pre-stubbed; the trainer script can be run on historical
1-min Binance klines to fit a real model. If no model file is present we
fall back to a calibrated logistic on momentum features so the system still
produces reasonable probabilities out-of-the-box.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .features import FEATURE_COLS, build_features

MODEL_PATH = Path(os.getenv("BTC_MODEL_PATH", "/data/btc_5m_xgb.json"))


class BTCDirectionModel:
    def __init__(self):
        self.booster: xgb.Booster | None = None
        if MODEL_PATH.exists():
            try:
                self.booster = xgb.Booster()
                self.booster.load_model(str(MODEL_PATH))
            except Exception:
                self.booster = None

    # ────────────────────────────────────────────────────────────
    # Training
    # ────────────────────────────────────────────────────────────
    def train(self, klines_1m: pd.DataFrame) -> dict:
        """klines_1m must have columns: open, high, low, close, volume."""
        feats = build_features(klines_1m)
        # Label: did price 5 minutes from now exceed price now?
        future = klines_1m["close"].shift(-5)
        y = (future > klines_1m["close"]).astype(int)

        df = feats[FEATURE_COLS].copy()
        df["y"] = y
        df = df.dropna()
        X, y = df[FEATURE_COLS].values, df["y"].values

        n = len(X)
        cut = int(n * 0.8)
        dtrain = xgb.DMatrix(X[:cut], label=y[:cut])
        dval = xgb.DMatrix(X[cut:], label=y[cut:])

        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 4,
            "eta": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 8,
        }
        self.booster = xgb.train(
            params, dtrain, num_boost_round=600,
            evals=[(dval, "val")], early_stopping_rounds=40, verbose_eval=False,
        )
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(MODEL_PATH))
        preds = self.booster.predict(dval)
        acc = float(((preds > 0.5) == y[cut:]).mean())
        return {"val_accuracy": acc, "n_train": cut, "n_val": n - cut}

    # ────────────────────────────────────────────────────────────
    # Inference
    # ────────────────────────────────────────────────────────────
    def predict_proba(self, klines_1m: pd.DataFrame) -> float:
        feats = build_features(klines_1m)
        row = feats[FEATURE_COLS].iloc[[-1]]
        if self.booster is not None:
            p = float(self.booster.predict(xgb.DMatrix(row.values))[0])
            return float(np.clip(p, 0.01, 0.99))
        return self._fallback(row.iloc[0])

    @staticmethod
    def _fallback(row: pd.Series) -> float:
        """Hand-calibrated logistic on a few momentum features.
        Acts as a sane default when no trained model is loaded."""
        z = (
            5.0 * row["ret_5m"]
            + 2.0 * row["ret_15m"]
            + 0.8 * row["macd_diff"]
            + 0.04 * (row["rsi_14"] - 50)
            + 0.5 * row["ema_ratio"]
        )
        return 1.0 / (1.0 + math.exp(-z))
