# Deployment

## Local dev (paper-trade only)

```bash
cd btc-oracle
cp .env.example .env
# Edit OPENAI_API_KEY (optional — heuristic fallback works without it)
docker compose -f infra/docker-compose.yml up --build
```

- API:        http://localhost:8000/docs
- Frontend:   http://localhost:5173
- Predictions begin flowing within ~60s.
- `LIVE_TRADING=false` means no real orders are ever submitted.

## Production (managed)

1. **Provision**:
   - Postgres (RDS / Cloud SQL), Redis (ElastiCache / Memorystore).
   - KMS key for `MASTER_KMS_KEY_B64` (replace `EnvMasterKey` with
     `KmsMasterKey` that calls `boto3.client('kms').decrypt`).
   - Container registry + orchestrator (ECS / GKE / Fly).

2. **Secrets**:
   - `JWT_SECRET` from secret manager (rotate quarterly).
   - DB password injected at boot.
   - Polymarket API creds: per-user only, never service-wide.

3. **Train model**:
   ```bash
   docker run --rm -v $(pwd)/data:/data btc-oracle-api \
     python -m scripts.train_model
   ```
   This writes `/data/btc_5m_xgb.json` which the workers will pick up.

4. **Flip live trading**:
   - Set `LIVE_TRADING=true` on the worker container only (not the API).
   - Each user must (a) link a wallet, (b) toggle `paper_only=false`
     after acknowledging risk, (c) enable `auto_trade_enabled`.

5. **Observe**:
   - Scrape `/metrics` (add prometheus-fastapi-instrumentator).
   - Pipe Celery logs to your aggregator with structured logging.
   - Page on: API 5xx > 1%, worker task failure > 5%, reconcile lag > 3 min.

## Backtesting

The `predictions` table stores p_up + features + timestamp for every window;
reconciliation fills `trades.pnl_usdc`. Run:

```sql
SELECT date_trunc('day', created_at) d,
       count(*) FILTER (WHERE status='won')::float / count(*) AS win_rate,
       sum(pnl_usdc) AS pnl
FROM trades WHERE is_paper GROUP BY 1 ORDER BY 1;
```

If paper win-rate stays below ~52% for several thousand trades, the model is
no better than a coin flip after fees — do NOT switch to live trading.
