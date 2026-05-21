"""High-level prediction engine: market data → features → ML → swarm → output."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .features import FEATURE_COLS, build_features
from .market_data import fetch_klines
from .ml_model import BTCDirectionModel
from .swarm import Vote, run_swarm

log = logging.getLogger(__name__)


@dataclass
class Forecast:
    window_ts: int
    btc_price: float
    ml_p_up: float
    swarm_p_up: float
    p_up: float
    features: dict
    votes: list[Vote] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_ts": self.window_ts,
            "btc_price": self.btc_price,
            "ml_p_up": self.ml_p_up,
            "swarm_p_up": self.swarm_p_up,
            "p_up": self.p_up,
            "features": self.features,
            "votes": [v.__dict__ for v in self.votes],
        }


_model = BTCDirectionModel()


async def forecast_for_window(window_ts: int) -> Forecast:
    klines = await fetch_klines("1m", 200)
    feats_df = build_features(klines)
    feats = feats_df[FEATURE_COLS].iloc[-1].to_dict()
    btc_price = float(klines["close"].iloc[-1])

    ml_p = _model.predict_proba(klines)
    swarm_p, votes = await run_swarm(feats, btc_price, ml_p)

    # Final = blend of ML and swarm; ML weighted slightly higher (it's grounded
    # in price data, swarm adds context but is noisier).
    final_p = 0.55 * ml_p + 0.45 * swarm_p

    return Forecast(
        window_ts=window_ts,
        btc_price=btc_price,
        ml_p_up=ml_p,
        swarm_p_up=swarm_p,
        p_up=final_p,
        features=feats,
        votes=votes,
    )
