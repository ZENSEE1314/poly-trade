import asyncio
from app.ai.swarm import run_swarm


def test_swarm_runs_without_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    # Reset cached settings + LLM singleton so the env change takes effect.
    from app.core import config as cfg
    cfg.get_settings.cache_clear()
    from app.ai import llm as llm_mod
    llm_mod._singleton = None

    feats = {
        "ret_1m": 0.001, "ret_5m": 0.003, "ret_15m": 0.002, "ret_60m": 0.001,
        "rsi_14": 58, "macd": 0.1, "macd_signal": 0.05, "macd_diff": 0.05,
        "ema_ratio": 0.0008, "atr_14": 25, "bb_pctb": 0.6, "vol_z": 0.5,
        "body_frac": 0.4, "upper_wick": 0.2, "lower_wick": 0.1,
    }
    p, votes = asyncio.run(run_swarm(feats, btc_price=68000, ml_p_up=0.55))
    assert 0 < p < 1
    assert len(votes) == 5
