# BTC Oracle — MiroFish-Powered Polymarket Auto-Trader

A production-grade reference architecture for an AI that predicts BTC 5-minute
**Up/Down** outcomes on Polymarket and (optionally) executes trades 24/7 on behalf
of users who connect their own wallets.

> ⚠️ **READ THIS FIRST** ⚠️
>
> 1. Short-horizon BTC price prediction is *extremely close to a coin flip*.
>    Any system that promises consistent profit on 5-minute binary markets is
>    almost certainly lying. This codebase is engineered honestly: it ships with
>    **paper trading enabled by default** and refuses to place real orders until
>    the operator explicitly enables `LIVE_TRADING=true` per-user *and* the user
>    completes a risk acknowledgment.
> 2. Polymarket is **not available to U.S. persons**. You are responsible for
>    your own regulatory compliance.
> 3. Storing user private keys on your servers is a *huge* liability. This repo
>    uses envelope encryption (per-user DEK wrapped by a KMS master key) and
>    supports a "delegated API-key only" mode where you never see the EOA key.
>    Use the API-key mode in production whenever possible.
> 4. Nothing here is investment advice.

---

## What you get

```
btc-oracle/
├── backend/                FastAPI service (Python 3.11)
│   ├── app/
│   │   ├── ai/             Prediction engine (ML + MiroFish-style LLM swarm)
│   │   ├── api/            REST routes (auth, wallets, settings, trades)
│   │   ├── core/           Config, security, KMS, logging
│   │   ├── db/             SQLAlchemy models + Alembic
│   │   ├── services/       Polymarket client, market discovery, risk engine
│   │   └── workers/        Celery beat tasks: predict-loop, trade-loop, reconcile
│   └── tests/
├── frontend/               React + Vite + TypeScript
│   ├── src/pages/          Login, Dashboard, Wallet, Settings, History
│   └── src/components/     Charts, prediction tiles, risk sliders
├── infra/                  Docker Compose, Nginx, Postgres, Redis, Prometheus
└── docs/                   Architecture, security, deployment, threat model
```

## Architecture (one paragraph)

A FastAPI backend keeps a per-user `TradingProfile` (risk budget, max stake,
kill-switch). A Celery beat scheduler runs the **prediction loop** every 60s,
which (a) pulls live BTC OHLCV from Binance, (b) computes TA features and
asks a gradient-boosted classifier for `P(up)`, then (c) spawns a MiroFish-style
**LLM swarm** of 5 personas (Technical, Momentum, Sentiment, Contrarian,
Macro) that vote and adjust the probability. The result is published to Redis.
The **trade loop** wakes 10 seconds before each 5-minute Polymarket window
closes, checks each user's risk policy, sizes orders via Kelly-capped staking,
and submits market orders through `py-clob-client`. A reconciliation worker
fetches fills, marks resolutions, and updates PnL.

See [`docs/architecture.md`](docs/architecture.md) for details.

## Quick start (local dev, paper trading)

```bash
cp .env.example .env             # then edit OPENAI_API_KEY, JWT_SECRET, etc.
docker compose -f infra/docker-compose.yml up --build
# Backend: http://localhost:8000/docs
# Frontend: http://localhost:5173
```

Default credentials: register a new account on the login page.
Paper trading is on; flip `LIVE_TRADING=true` only after reading
[`docs/security.md`](docs/security.md) and [`docs/deployment.md`](docs/deployment.md).

## Credit

The LLM-swarm prediction engine is inspired by
[666ghj/MiroFish](https://github.com/666ghj/MiroFish) — a multi-agent
"swarm intelligence" framework that we adapt here from social simulation to
market microstructure forecasting.
