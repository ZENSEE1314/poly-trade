# Security & Custody

## Threat model

Users hand you the ability to move (or at least trade) their money. The
realistic threats are:

1. **Server compromise** — DB dump, RCE in the API.
2. **Insider abuse** — admin pulls user keys from production.
3. **Supply-chain** — malicious npm/pip package signs/sends orders.
4. **Front-running** — observers learn predictions before users do.
5. **Runaway losses** — bug or model drift bleeds capital fast.

## Custody modes

| Mode           | What we store           | Powers granted            | Recommended |
| -------------- | ----------------------- | ------------------------- | ----------- |
| `api_key`      | Polymarket L2 creds     | Place orders only         | **Yes**     |
| `private_key`  | EOA private key (sealed)| Full control of funds     | Only if you own infra; disabled in prod by default (`ALLOW_PK_MODE=false`) |

## Encryption at rest

Envelope encryption (AES-256-GCM):

```
DEK = random(256)               # per-secret data key
ct  = AESGCM(DEK).encrypt(nonce_a, plaintext, aad=user_id)
wDEK= AESGCM(MK ).encrypt(nonce_b, DEK,       aad=user_id)
store { ct, nonce_a, wDEK, nonce_b }
```

- Master Key (MK) **must** come from a real KMS in production (AWS KMS, GCP
  KMS, Vault Transit). Never bake it into env in real deployments.
- `aad=user_id` cryptographically binds the ciphertext to the user — moving
  a row between users invalidates the tag.

## Defense in depth for trading

- `LIVE_TRADING=false` (process-wide) — hard kill switch.
- `paper_only=true` per user — second opt-in.
- `live_trading_acknowledged=true` per user — recorded consent.
- `GLOBAL_MAX_DAILY_USDC` — server-side cap that no UI can override.
- `MIN_EDGE` — refuse to trade below model-edge floor.
- Daily loss limit, daily trade count limit, per-trade stake cap.
- FOK orders only — never sit on the book where they could be picked off.

## Recommended deployment hardening

- TLS everywhere; mutual TLS between API ↔ worker.
- Read-only DB role for API; only worker has WRITE on `trades`.
- Separate IAM role for the KMS Decrypt call.
- Per-user rate limits at the API gateway.
- WAL-archived Postgres backups; never back up the master key with the DB.
- Run worker in an enclave / Nitro Enclave / GCP confidential VM if budget
  allows, especially when `private_key` mode is enabled.
- Prometheus alerts on: win_rate < 0.48 over 200 trades, daily_loss > 50%
  of cap, > N order errors per minute.
