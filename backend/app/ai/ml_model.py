"""XGBoost classifier for P(BTC close in 5 min > current price).

Self-learning loop:
  - train(klines_1m)  → fits model, saves to file + Redis, returns metadata
  - predict_proba()   → hot-reloads from disk if a newer model was saved
  - get_meta()        → reads training metadata from Redis (for API / dashboard)

Storage priority:
  1. BTC_MODEL_PATH env var (default /data/btc_5m_xgb.json — Railway volume)
  2. /tmp/btc_5m_xgb.json (ephemeral fallback)
  3. Redis key btc_oracle:ml_model_b64 (base64 blob — survives pod restarts
     even without a persistent volume)
"""
from __future__ import annotations

import base64
import json as _json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .features import FEATURE_COLS, build_features

# ── Storage paths ────────────────────────────────────────────────────────────
_MODEL_PATHS = [
    Path(os.getenv("BTC_MODEL_PATH", "/data/btc_5m_xgb.json")),
    Path("/tmp/btc_5m_xgb.json"),
]

ML_META_KEY  = "btc_oracle:ml_meta"
ML_MODEL_KEY = "btc_oracle:ml_model_b64"


def _get_redis():
    """Lazy Redis client — keeps ml_model importable even before settings load."""
    import redis as _r
    from ..core.config import get_settings
    return _r.from_url(get_settings().REDIS_URL, decode_responses=False)


def _model_to_bytes(booster: xgb.Booster) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        booster.save_model(tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Model class ──────────────────────────────────────────────────────────────

class BTCDirectionModel:
    def __init__(self):
        self.booster: xgb.Booster | None = None
        self._loaded_at: float = 0.0   # epoch-seconds of last load
        self._load_best_available()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load_best_available(self) -> None:
        """Try file paths, then Redis blob."""
        for path in _MODEL_PATHS:
            if path.exists():
                try:
                    b = xgb.Booster()
                    b.load_model(str(path))
                    self.booster = b
                    self._loaded_at = path.stat().st_mtime
                    return
                except Exception:
                    continue
        self._load_from_redis()

    def _load_from_redis(self) -> None:
        try:
            r = _get_redis()
            b64 = r.get(ML_MODEL_KEY.encode())
            if b64:
                raw = base64.b64decode(b64)
                b = xgb.Booster()
                b.load_model(bytearray(raw))
                self.booster = b
                self._loaded_at = time.time()
        except Exception:
            pass

    def _maybe_reload(self) -> None:
        """Hot-reload when a newer model file exists (written by retrain task)."""
        for path in _MODEL_PATHS:
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                    if mtime > self._loaded_at + 1:   # 1-second grace
                        b = xgb.Booster()
                        b.load_model(str(path))
                        self.booster = b
                        self._loaded_at = mtime
                except Exception:
                    pass
                return   # stop at the first writable path

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, klines_1m: pd.DataFrame) -> dict:
        """Retrain on real BTC klines. Label = price higher in exactly 5 min?

        Returns metadata dict with accuracy, sample counts, top features.
        Saves model to file (best available path) AND to Redis so it survives
        ephemeral filesystems.
        """
        feats  = build_features(klines_1m)
        future = klines_1m["close"].shift(-5)
        y      = (future > klines_1m["close"]).astype(int)

        df = feats[FEATURE_COLS].copy()
        df["y"] = y
        df = df.dropna()

        X, y_arr = df[FEATURE_COLS].values, df["y"].values
        n   = len(X)
        cut = int(n * 0.8)

        dtrain = xgb.DMatrix(X[:cut], label=y_arr[:cut], feature_names=FEATURE_COLS)
        dval   = xgb.DMatrix(X[cut:],  label=y_arr[cut:],  feature_names=FEATURE_COLS)

        params = {
            "objective":        "binary:logistic",
            "eval_metric":      "logloss",
            "max_depth":        4,
            "eta":              0.05,
            "subsample":        0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 8,
            "seed":             42,
        }
        self.booster = xgb.train(
            params, dtrain, num_boost_round=600,
            evals=[(dval, "val")], early_stopping_rounds=40, verbose_eval=False,
        )

        # Save to best writable path
        saved_path: str | None = None
        for path in _MODEL_PATHS:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.booster.save_model(str(path))
                self._loaded_at = path.stat().st_mtime
                saved_path = str(path)
                break
            except Exception:
                continue

        # Metrics
        preds = self.booster.predict(dval)
        acc   = float(((preds > 0.5) == y_arr[cut:]).mean())

        scores   = self.booster.get_score(importance_type="gain")
        top_feats = sorted(scores.items(), key=lambda kv: -kv[1])[:8]
        max_score = top_feats[0][1] if top_feats else 1.0

        meta = {
            "val_accuracy":  round(acc, 4),
            "n_train":       int(cut),
            "n_val":         int(n - cut),
            "n_total":       int(n),
            "trained_at":    datetime.now(timezone.utc).isoformat(),
            "saved_path":    saved_path,
            "top_features":  [
                {"feature": k, "importance": round(v, 2),
                 "pct": round(v / max_score * 100, 1)}
                for k, v in top_feats
            ],
        }

        # Persist to Redis so all processes + pod restarts have access
        try:
            r = _get_redis()
            r.set(ML_META_KEY.encode(),  _json.dumps(meta).encode())
            model_bytes = _model_to_bytes(self.booster)
            r.set(ML_MODEL_KEY.encode(), base64.b64encode(model_bytes))
        except Exception:
            pass

        return meta

    # ── Inference ────────────────────────────────────────────────────────────

    def predict_proba(self, klines_1m: pd.DataFrame) -> float:
        """Returns P(BTC up in next 5 min). Hot-reloads model if retrain ran."""
        self._maybe_reload()
        feats = build_features(klines_1m)
        row   = feats[FEATURE_COLS].iloc[[-1]]
        if self.booster is not None:
            dm = xgb.DMatrix(row.values, feature_names=FEATURE_COLS)
            p  = float(self.booster.predict(dm)[0])
            return float(np.clip(p, 0.01, 0.99))
        return self._fallback(row.iloc[0])

    @staticmethod
    def _fallback(row: pd.Series) -> float:
        """Hand-calibrated logistic — used when no trained model exists yet.

        Design: blend momentum (trusted in neutral RSI) with mean-reversion at
        extremes.  Raw macd_diff is in dollar terms ($100-500 for BTC), so we
        use only its sign — otherwise it completely dominates the formula and
        the model reduces to a pure MACD follower with no real signal.
        """
        rsi = float(row["rsi_14"])

        # 0 at RSI=50 (neutral), 1 at RSI=70/30 (extreme)
        rsi_extreme = min(1.0, abs(rsi - 50) / 20)
        rsi_neutral = 1.0 - rsi_extreme

        # MACD direction only — sign tells us trend direction without dollar scale
        raw_macd = row["macd_diff"]
        macd_sign = math.copysign(0.2, raw_macd) if raw_macd != 0 else 0.0

        # ema_ratio is tiny (~0.001–0.005) — scale up to be meaningful
        ema_contribution = 200.0 * float(row["ema_ratio"])

        z = (
            4.0 * row["ret_5m"]  * rsi_neutral   # momentum, suppressed at extremes
            + 2.0 * row["ret_15m"] * rsi_neutral  # 15-min confirmation
            + macd_sign                            # ±0.2 directional bonus from MACD
            - 0.04 * (rsi - 50) * rsi_extreme     # mean-revert: fade overbought/oversold
            + ema_contribution                     # slow trend bias from EMA crossover
        )
        return 1.0 / (1.0 + math.exp(-z))

    # ── Metadata ─────────────────────────────────────────────────────────────

    @classmethod
    def get_meta(cls) -> dict | None:
        """Read last training metadata from Redis. None if never trained."""
        try:
            r = _get_redis()
            raw = r.get(ML_META_KEY.encode())
            return _json.loads(raw) if raw else None
        except Exception:
            return None
