# Deploying to Railway

This repo deploys as **5 Railway services** in one project:

| Service     | Source dir   | Config file                       | Notes                          |
| ----------- | ------------ | --------------------------------- | ------------------------------ |
| `api`       | `backend/`   | `railway.json` (root)             | Public, FastAPI on `$PORT`     |
| `worker`    | `backend/`   | `backend/railway.worker.json`     | Celery worker (no public port) |
| `beat`      | `backend/`   | `backend/railway.beat.json`       | Celery beat scheduler          |
| `frontend`  | `frontend/`  | `frontend/railway.json`           | Nginx serving the React build  |
| `postgres`  | —            | Railway template                  | Add from "+ New" → Database    |
| `redis`     | —            | Railway template                  | Add from "+ New" → Database    |

---

## One-time setup

### 1. Create the project

```bash
# install CLI (https://docs.railway.com/guides/cli)
npm i -g @railway/cli
railway login
railway init   # pick "Empty project", name it poly-trade
```

### 2. Add data services

In the Railway dashboard for the `poly-trade` project:

- Click **+ New → Database → Add PostgreSQL**.
- Click **+ New → Database → Add Redis**.

Railway exposes:
- `${{ Postgres.DATABASE_URL }}`  (use as `DATABASE_URL`)
- `${{ Redis.REDIS_URL }}`        (use as `REDIS_URL`)

### 3. Add the API service

```bash
cd btc-oracle
railway link                # link this dir to the project
railway service             # create new → name it "api"
# Push code (uses railway.json at repo root):
railway up
```

Then in dashboard → `api` → **Variables** set:

```
APP_ENV=production
JWT_SECRET=<output of: python -c "import secrets;print(secrets.token_urlsafe(64))">
JWT_TTL_MIN=60
MASTER_KMS_KEY_B64=<output of: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())">
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
FRONTEND_ORIGIN=https://<your-frontend>.up.railway.app
OPENAI_API_KEY=sk-...                # optional; falls back to heuristics
OPENAI_MODEL=gpt-4o-mini
POLYMARKET_CLOB_HOST=https://clob.polymarket.com
POLYMARKET_GAMMA_HOST=https://gamma-api.polymarket.com
POLYMARKET_CHAIN_ID=137
LIVE_TRADING=false                   # KEEP FALSE until you've paper-validated
GLOBAL_MAX_DAILY_USDC=100
MIN_EDGE=0.04
```

Under **Settings → Networking** → **Generate Domain**.

### 4. Add the worker service

```bash
railway service          # create new → name it "worker"
# Tell Railway to use the worker config:
railway up --service worker -c backend/railway.worker.json
```

Copy all variables from `api` → `worker` (Railway dashboard → Variables → "Copy from another service").

### 5. Add the beat service

```bash
railway service          # create new → name it "beat"
railway up --service beat -c backend/railway.beat.json
```

Copy variables from `api` to `beat` too.

> Why 3 backend services and not one? Celery beat MUST run as exactly one
> process (otherwise schedules duplicate). Workers can scale to N. The web
> API is autoscaled separately. Splitting them is the Railway-idiomatic way.

### 6. Add the frontend

```bash
railway service          # create new → name it "frontend"
railway up --service frontend -c frontend/railway.json
```

In dashboard → `frontend` → **Settings → Build → Build Args**:

```
VITE_API_BASE=https://<your-api-service>.up.railway.app
```

Trigger a redeploy. Generate a domain.

Finally, copy the frontend domain back to the `api` service's
`FRONTEND_ORIGIN` variable so CORS is correct.

---

## After deploy

- Visit the frontend URL → register an account.
- API docs: `https://<api>.up.railway.app/docs`.
- Predictions start streaming within ~60s (visible on Dashboard).
- **`LIVE_TRADING=false`** — paper trading only by default.
  Flip to `true` *only* on the `worker` service after you've validated
  paper-mode win-rate over ≥1,000 trades.

## Costs (rough)

- Postgres + Redis: ~$5/mo each on Hobby
- 3 backend services @ 0.5 vCPU / 512 MB: ~$5/mo each
- Frontend nginx: ~$1/mo
- **Total ≈ $20-30/mo** for the always-on stack. Scale workers if needed.
