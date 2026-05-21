# Switching the LLM backend to Ollama

The swarm now defaults to **Ollama**. Three deployment shapes are supported.

| Variable | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` \| `openai` \| `none` |
| `OLLAMA_HOST` | `http://localhost:11434` | URL of the Ollama API |
| `OLLAMA_MODEL` | `glm-5.1:cloud` | Any model tag your host has |
| `OLLAMA_API_KEY` | *(empty)* | Required for **Ollama Cloud** (`:cloud` tags) |
| `OLLAMA_KEEP_ALIVE` | `5m` | How long Ollama keeps the model resident |
| `OLLAMA_TIMEOUT` | `30` | Request timeout (s) |

## A) Ollama Cloud — recommended for Railway

Cloud models (tags ending in `:cloud`) run on Ollama's GPUs, so your tiny
Railway dyno doesn't need one. The swarm hits them over HTTPS like any API.

1. Sign up at https://ollama.com → create an API key.
2. In Railway, on the **api**, **worker**, and **beat** services set:
   ```
   LLM_PROVIDER=ollama
   OLLAMA_HOST=https://ollama.com
   OLLAMA_API_KEY=<your-key>
   OLLAMA_MODEL=glm-5.1:cloud
   ```
3. Redeploy. Watch logs for `LLM: ollama glm-5.1:cloud @ https://ollama.com`.

> ⚠️ If you see `Ollama model 'glm-5.1:cloud' not found at https://ollama.com`,
> the exact tag isn't on Cloud. Try `ollama list` in the dashboard or pick a
> known cloud model like `qwen3-coder:480b-cloud` or `gpt-oss:120b-cloud`.

## B) Self-hosted Ollama (Docker)

Run Ollama as a sidecar service:

```yaml
# infra/docker-compose.override.yml
services:
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["ollama:/root/.ollama"]
volumes:
  ollama: {}
```

Then in `.env`:
```
LLM_PROVIDER=ollama
OLLAMA_HOST=http://ollama:11434
OLLAMA_MODEL=llama3.1   # whatever you `ollama pull`'d
OLLAMA_API_KEY=
```

Pull the model the first time:
```bash
docker compose exec ollama ollama pull llama3.1
```

## C) Local dev on your machine

```bash
brew install ollama   # or download from ollama.com
ollama serve &
ollama pull llama3.1

# in .env
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

## Fallback behaviour

If Ollama is unreachable, returns 5xx, or sends non-JSON, **each persona
falls back to a deterministic heuristic** that mirrors its trading style.
The system never crashes due to LLM issues — at worst, the swarm degrades
to the rule-engine and your predictions keep flowing.

## Why native `/api/chat` (not the OpenAI shim)

Ollama's OpenAI-compatibility endpoint silently strips some parameters
(`format`, `keep_alive`) that we rely on for clean JSON output and warm-loaded
cloud models. Using `/api/chat` directly is one fewer translation layer
and gives much better reliability for short structured responses.
