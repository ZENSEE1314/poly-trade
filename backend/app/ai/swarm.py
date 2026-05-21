"""MiroFish-style multi-agent prediction swarm.

Inspired by https://github.com/666ghj/MiroFish — a "swarm intelligence" engine
where many LLM agents with distinct personas observe the same situation and
vote. We adapt the idea from social simulation to short-horizon market
forecasting:

    Personas:
      1. The Tape Reader  — pure technicals, momentum & RSI
      2. The Contrarian   — fades extremes & overbought conditions
      3. The Microstructure Quant — order-flow / wick / volume cues
      4. The Macro Macro  — slow-trend bias from 60-min returns
      5. The Sentiment Bot — news/social tone (stubbed unless feed provided)

    Aggregation:
      - Each persona returns {vote ∈ {up, down, neutral}, confidence ∈ [0,1]}
      - Final swarm_p_up = soft-Bayesian update on the ML prior using votes

LLM backend is selected via LLM_PROVIDER (ollama | openai | none). If unavailable
the personas fall back to a deterministic rule-engine that mirrors their styles.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass

from .llm import get_llm

log = logging.getLogger(__name__)


PERSONAS = [
    {
        "name": "TapeReader",
        "system": (
            "You are a disciplined intraday technical trader. You look only at "
            "OHLC, RSI, MACD and short-term returns. You speak in terse JSON."
        ),
    },
    {
        "name": "Contrarian",
        "system": (
            "You are a mean-reversion specialist. You FADE strong moves and look "
            "for exhaustion. You respond only in JSON."
        ),
    },
    {
        "name": "MicroQuant",
        "system": (
            "You are a market microstructure quant. Wicks, body fractions, "
            "volume z-scores and Bollinger %B drive your view. JSON only."
        ),
    },
    {
        "name": "MacroBias",
        "system": (
            "You are a swing trader who weights slower trend (60-min returns, "
            "EMA ratio) and lets short-term noise dominate less. JSON only."
        ),
    },
    {
        "name": "SentimentBot",
        "system": (
            "You estimate near-term BTC sentiment. With no live news feed you "
            "are CAUTIOUS and default to neutral with low confidence. JSON only."
        ),
    },
]

VOTE_SCHEMA_HINT = (
    'Return JSON exactly like: {"vote":"up|down|neutral","confidence":0.0-1.0,'
    '"reason":"<<= 20 words"}'
)


@dataclass
class Vote:
    persona: str
    vote: str          # "up" | "down" | "neutral"
    confidence: float  # 0..1
    reason: str = ""


# ───────────────────────── deterministic fallback ─────────────────────────

def _heuristic_vote(persona: str, feats: dict) -> Vote:
    """Mirror each persona's style with simple rules — used when no LLM."""
    ret_5 = feats.get("ret_5m", 0)
    ret_15 = feats.get("ret_15m", 0)
    ret_60 = feats.get("ret_60m", 0)
    rsi = feats.get("rsi_14", 50)
    macd_d = feats.get("macd_diff", 0)
    bbp = feats.get("bb_pctb", 0.5)
    vol_z = feats.get("vol_z", 0)
    ema_r = feats.get("ema_ratio", 0)

    if persona == "TapeReader":
        score = 4 * ret_5 + 0.6 * macd_d + 0.03 * (rsi - 50)
    elif persona == "Contrarian":
        score = -3 * ret_5 - 0.05 * (rsi - 50) - 2 * (bbp - 0.5)
    elif persona == "MicroQuant":
        score = (
            -1.0 * feats.get("upper_wick", 0)
            + 1.0 * feats.get("lower_wick", 0)
            + 0.3 * vol_z * (1 if ret_5 >= 0 else -1)
        )
    elif persona == "MacroBias":
        score = 3 * ret_60 + 2 * ret_15 + 1.5 * ema_r
    else:  # SentimentBot
        return Vote("SentimentBot", "neutral", 0.15, "no live feed")

    if score > 0.0015:
        return Vote(persona, "up", min(0.9, abs(score) * 80), "score>0")
    if score < -0.0015:
        return Vote(persona, "down", min(0.9, abs(score) * 80), "score<0")
    return Vote(persona, "neutral", 0.3, "weak signal")


# ───────────────────────── LLM-backed personas ─────────────────────────

async def _ask_persona(persona: dict, snapshot: str, features: dict) -> Vote:
    llm = get_llm()
    if llm is None:
        return _heuristic_vote(persona["name"], features)

    system = persona["system"] + "\n" + VOTE_SCHEMA_HINT
    try:
        data = await llm.chat_json(system, snapshot, max_tokens=120)
        return Vote(
            persona=persona["name"],
            vote=str(data.get("vote", "neutral")).lower(),
            confidence=float(max(0.0, min(1.0, data.get("confidence", 0.3)))),
            reason=str(data.get("reason", ""))[:120],
        )
    except Exception as e:
        log.warning("persona %s failed (%s) — using heuristic", persona["name"], e)
        return _heuristic_vote(persona["name"], features)


# ───────────────────────── orchestration ─────────────────────────

async def run_swarm(features: dict, btc_price: float, ml_p_up: float) -> tuple[float, list[Vote]]:
    """Returns (swarm_p_up, votes)."""
    snapshot = json.dumps({
        "now_btc_usd": btc_price,
        "ml_prior_p_up": round(ml_p_up, 4),
        "features": {k: round(float(v), 6) for k, v in features.items()},
        "horizon": "5 minutes",
    })

    votes = await asyncio.gather(*[_ask_persona(p, snapshot, features) for p in PERSONAS])

    # Bayesian-ish aggregation: start from ML prior, nudge by weighted votes.
    log_odds = _logit(ml_p_up)
    for v in votes:
        delta = 1.2 * v.confidence  # max +/-1.2 log-odds per persona
        if v.vote == "up":
            log_odds += delta
        elif v.vote == "down":
            log_odds -= delta
    # damp the swarm so it can't completely override a strong ML signal
    swarm_p = _sigmoid(0.6 * log_odds)
    return float(swarm_p), list(votes)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
