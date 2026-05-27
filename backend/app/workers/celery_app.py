from celery import Celery
from celery.schedules import crontab

from ..core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "btc_oracle",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    task_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    beat_schedule={
        # Predict every minute. The trade task internally syncs to the 5-min
        # boundary so it only fires once per window.
        "predict-every-minute": {
            "task": "app.workers.tasks.run_prediction_cycle",
            "schedule": 60.0,
        },
        # Trade scheduler ticks every 10s — submits orders ~10s before close.
        "trade-tick": {
            "task": "app.workers.tasks.trade_tick",
            "schedule": 10.0,
        },
        # Reconcile filled / resolved markets every minute
        "reconcile": {
            "task": "app.workers.tasks.reconcile_open_trades",
            "schedule": 60.0,
        },
        # Simulated paper trade every 5-min window — bypasses real market / edge checks
        "paper-demo-tick": {
            "task": "app.workers.tasks.paper_demo_tick",
            "schedule": 300.0,
        },
        # Retrain XGBoost every 6 hours on fresh Binance klines
        "retrain-model": {
            "task": "app.workers.tasks.retrain_model",
            "schedule": 21600.0,   # 6 hours
        },
    },
)
