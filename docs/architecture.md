# Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser (React)                            │
│   Login · Dashboard · Wallet · Risk Settings · History · Live charts    │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ JWT
┌──────────────────────────▼──────────────────────────────────────────────┐
│                          FastAPI  (api/)                                │
│   /auth · /wallet · /profile · /predictions · /trades                   │
└─────┬─────────────────────────────────────────────────────┬─────────────┘
      │                                                     │
      │ SQLAlchemy                                          │ Redis pub/sub
      ▼                                                     ▼
┌─────────────┐                                       ┌────────────────┐
│ Postgres    │                                       │ Redis (Celery, │
│ users,      │                                       │  cache, locks) │
│ wallets*,   │                                       └────────────────┘
│ profiles,   │                                                ▲
│ predictions │                                                │
│ trades      │                                                │
└─────────────┘                                                │
   * encrypted blobs (envelope AES-256-GCM, master key in KMS) │
                                                               │
┌──────────────────────────────────────────────────────────────┴────────┐
│                          Celery beat + workers                        │
│                                                                       │
│  ┌──────────────────────────┐    ┌──────────────────────────────────┐ │
│  │ predict-every-minute     │    │ trade-tick (every 10s)           │ │
│  │  1. Fetch BTC 1m klines  │    │  1. If T-15..T-5s before close:  │ │
│  │  2. Build TA features    │    │     read cached forecast         │ │
│  │  3. XGBoost → ml_p_up    │    │  2. Resolve Polymarket market    │ │
│  │  4. LLM swarm vote       │    │  3. For each user:               │ │
│  │     (5 personas)         │    │       risk.decide() → stake/side │ │
│  │  5. Blend → p_up         │    │       paper_submit OR live_submit│ │
│  │  6. Cache + persist      │    │  4. Persist Trade row            │ │
│  └──────────────────────────┘    └──────────────────────────────────┘ │
│                                                                       │
│  ┌──────────────────────────┐                                         │
│  │ reconcile-open-trades    │                                         │
│  │  After window close,     │                                         │
│  │  fetch close price,      │                                         │
│  │  mark won/lost, set pnl  │                                         │
│  └──────────────────────────┘                                         │
└───────────────────────────────────────────────────────────────────────┘
```

## Why this design

1. **Stateless API + stateful workers**. Keeps the request path fast and the
   trading loop independent from web traffic spikes.
2. **One forecast = one row**. Every prediction is persisted with its full
   inputs (features) and outputs (per-persona votes). This makes the model
   auditable and enables offline backtesting.
3. **Server-authoritative risk**. `risk.decide()` runs in the worker, not the
   browser. A user cannot bypass limits by mutating their profile in flight —
   each tick re-reads the profile and applies the global hard cap.
4. **Idempotency**. A Redis SETNX lock keyed by `(window_ts, user_id)`
   guarantees at most one trade per user per market window even if multiple
   workers run.
5. **Paper-by-default**. `LIVE_TRADING=false` (env) + `paper_only=true` (per
   user) makes accidental real-money trades impossible without two explicit
   opt-ins.

## Failure modes & mitigations

| Failure                          | Mitigation                                  |
| -------------------------------- | ------------------------------------------- |
| Binance rate-limit               | 200-kline polls / minute; circuit-break     |
| LLM provider down                | Personas fall back to deterministic heuristics |
| Polymarket market not yet listed | Probe both slug schemas; skip if neither    |
| Worker crash mid-order           | FOK orders; idempotency lock expires in 600s|
| Bad model drift                  | Reconciliation logs win-rate → alerts < 50% |
| Compromised DB                   | Wallet secrets are envelope-encrypted; KMS  |
