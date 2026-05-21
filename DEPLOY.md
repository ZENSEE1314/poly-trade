# 🚀 Railway Deploy — Step-by-Step

Your URL `https://poly-trade-production-07d8.up.railway.app` returned **404
"Application not found"** because the service wasn't fully wired up yet.
Follow these steps in order — should take ~10 minutes.

> ⚠️ **NEVER paste real secrets into git.** All values below go into
> Railway's **Variables** UI (or its CLI), never into a committed file.

---

## Step 1 — Confirm the API service is using the right config

In your Railway project → click the existing `poly-trade` service:

1. **Settings → Source** → confirm:
   - Repository: `ZENSEE1314/poly-trade`
   - Branch: `main`
   - Root Directory: `/` (leave blank or `/`)
2. **Settings → Build** → **Builder**: `Dockerfile`
   - Dockerfile Path: `backend/Dockerfile`
3. **Settings → Deploy** → **Custom Start Command**:
   ```
   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```
4. **Settings → Networking** → click **Generate Domain** if you haven't
   (this is what gave you `poly-trade-production-07d8.up.railway.app`).

Trigger a **Redeploy**.

---

## Step 2 — Add Postgres and Redis

In the project canvas:

- Click **+ Create** → **Database** → **Add PostgreSQL**
- Click **+ Create** → **Database** → **Add Redis**

Wait ~30 seconds for them to provision.

---

## Step 3 — Set environment variables on the API service

On the `poly-trade` (api) service → **Variables** tab → **+ New Variable**.
Add these **one by one** (or use the Raw Editor):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `JWT_SECRET` | **Generate**: `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `JWT_TTL_MIN` | `60` |
| `MASTER_KMS_KEY_B64` | **Generate**: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` |
| `DATABASE_URL` | Click **Add Reference** → `Postgres` → `DATABASE_URL` |
| `REDIS_URL` | Click **Add Reference** → `Redis` → `REDIS_URL` |
| `FRONTEND_ORIGIN` | (leave blank for now — we'll fill after Step 6) |
| `OPENAI_API_KEY` | your key, or leave blank (swarm runs on heuristics without it) |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `POLYMARKET_CLOB_HOST` | `https://clob.polymarket.com` |
| `POLYMARKET_GAMMA_HOST` | `https://gamma-api.polymarket.com` |
| `POLYMARKET_CHAIN_ID` | `137` |
| `LIVE_TRADING` | `false` |
| `GLOBAL_MAX_DAILY_USDC` | `100` |
| `MIN_EDGE` | `0.04` |

Save → service will redeploy automatically.

Test:
```
curl https://poly-trade-production-07d8.up.railway.app/healthz
# → {"ok":true,"env":"production","live_trading":false,"version":"0.1.0"}
```

If you still get 404, check **Deployments → View Logs** on the service.

---

## Step 4 — Add the Celery worker service

In the project canvas: **+ Create** → **GitHub Repo** → `poly-trade`.
Name it `worker`. Then in **Settings**:

- **Source → Root Directory**: `/`
- **Build → Builder**: `Dockerfile`
- **Build → Dockerfile Path**: `backend/Dockerfile`
- **Deploy → Custom Start Command**:
  ```
  celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --concurrency=2
  ```
- **Networking**: do NOT generate a domain (worker has no HTTP)

**Variables**: click the **⋯** menu → **Copy from another service** → `api`.
This copies all 15 vars at once.

---

## Step 5 — Add the Celery beat service (scheduler)

Repeat Step 4 but name it `beat` and set start command:
```
celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
```

> ⚠️ Exactly **one** beat instance. Never scale this service above 1 replica
> or you'll get duplicate scheduled tasks.

---

## Step 6 — Add the frontend service

**+ Create** → **GitHub Repo** → `poly-trade`. Name it `frontend`. In Settings:

- **Source → Root Directory**: `/`
- **Build → Dockerfile Path**: `frontend/Dockerfile.prod`
- **Build → Build Arguments**: add `VITE_API_BASE` = `https://poly-trade-production-07d8.up.railway.app`
- **Networking → Generate Domain**

Once it's deployed, copy the new frontend domain (e.g.
`frontend-production-xxxx.up.railway.app`) and:

- Go back to the `api` service → Variables → set
  `FRONTEND_ORIGIN=https://frontend-production-xxxx.up.railway.app`
- The api will auto-redeploy with proper CORS.

---

## Step 7 — Try it

1. Visit your frontend URL.
2. Click **Register** → create an account with a real email and a 10+ char password.
3. You should land on the Dashboard. **Predictions start arriving within 60 seconds** (the beat scheduler runs `predict-every-minute`).
4. Go to **Risk & Auto-Trade** → toggle **Auto-trade** on → leave **Paper trading** ON.
5. Watch History tab — paper trades will appear right before each 5-min Polymarket window closes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Application not found` 404 | Service has no public domain. Networking → Generate Domain. |
| `502 Bad Gateway` | Container isn't listening on `$PORT`. Check Deploy → Logs. |
| Healthcheck failing | Make sure start command uses `${PORT}` not `8000`. |
| `relation "users" does not exist` | DATABASE_URL not set or wrong. Tables auto-create at boot. Redeploy. |
| `Connection refused: redis` | REDIS_URL missing on worker/beat. Copy from api. |
| Frontend loads but login fails | `VITE_API_BASE` wasn't set at build time, or CORS — set `FRONTEND_ORIGIN` on api. |
| `LIVE_TRADING=true` but still paper | User profile has `paper_only=true`. Toggle off in Settings UI (requires linked wallet). |
