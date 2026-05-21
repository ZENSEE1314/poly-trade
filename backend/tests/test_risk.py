from app.services.risk import kelly_fraction, decide
from app.models import TradingProfile


def test_kelly_basic():
    # 60% chance to win at price 0.5 → b=1 → f* = 0.2
    assert round(kelly_fraction(0.6, 0.5), 3) == 0.2
    # Negative edge → 0
    assert kelly_fraction(0.4, 0.5) == 0.0


def test_decide_skips_low_edge():
    class FakeDb:
        def execute(self, *_a, **_k):
            class _R:
                def scalars(self_): return self_
                def all(self_): return []
            return _R()

    p = TradingProfile(
        user_id=1, auto_trade_enabled=True, risk_level=20,
        max_stake_usdc=5, daily_loss_limit_usdc=10, daily_max_trades=20,
        min_confidence=0.55, max_price=0.95, side_filter="both",
    )
    d = decide(FakeDb(), p, p_up=0.51, up_ask=0.50, down_ask=0.50)
    assert not d.should_trade
