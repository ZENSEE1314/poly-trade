"""Provider-agnostic LLM client used by the agent swarm.

Supports:
  - Ollama        (LLM_PROVIDER=ollama)   ← default
  - OpenAI        (LLM_PROVIDER=openai)
  - None          (LLM_PROVIDER=none)     → personas fall back to heuristics

Ollama is invoked via its native /api/chat endpoint (no OpenAI compat shim)
so that 'cloud' models like `glm-5.1:cloud` route correctly when
OLLAMA_HOST=https://ollama.com and OLLAMA_API_KEY=<your-cloud-key> are set.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

from ..core.config import get_settings

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


class LLM:
    async def chat_json(self, system: str, user: str, *, max_tokens: int = 200) -> dict:
        raise NotImplementedError


# ───────────────────────── Ollama ─────────────────────────

class OllamaLLM(LLM):
    def __init__(self, host: str, model: str, api_key: str, timeout: float, keep_alive: str):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.keep_alive = keep_alive

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, LLMUnavailable)),
        wait=wait_exponential_jitter(initial=0.5, max=4),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat_json(self, system: str, user: str, *, max_tokens: int = 200) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0.4,
                "num_predict": max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.host}/api/chat", json=payload, headers=headers)
            if r.status_code >= 500:
                raise LLMUnavailable(f"ollama {r.status_code}: {r.text[:200]}")
            if r.status_code == 404:
                # Model not pulled / not on cloud — fail loudly, don't retry.
                raise RuntimeError(
                    f"Ollama model '{self.model}' not found at {self.host}. "
                    f"Pull it (ollama pull {self.model}) or set OLLAMA_MODEL "
                    "to one you have."
                )
            r.raise_for_status()
            data = r.json()

        content = (data.get("message") or {}).get("content", "").strip()
        if not content:
            raise LLMUnavailable("empty ollama response")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # The model ignored format=json. Try to salvage a JSON object.
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise LLMUnavailable(f"non-JSON content from ollama: {content[:120]!r}")


# ───────────────────────── OpenAI ─────────────────────────

class OpenAILLM(LLM):
    def __init__(self, api_key: str, model: str, base_url: str):
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model

    async def chat_json(self, system: str, user: str, *, max_tokens: int = 200) -> dict:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)


# ───────────────────────── factory ─────────────────────────

_singleton: LLM | None = None


def get_llm() -> LLM | None:
    """Returns an LLM instance or None if the provider is 'none' / misconfigured."""
    global _singleton
    if _singleton is not None:
        return _singleton

    s = get_settings()
    p = s.LLM_PROVIDER.lower().strip()
    if p == "none":
        log.info("LLM provider disabled (LLM_PROVIDER=none) — swarm using heuristics")
        return None
    if p == "ollama":
        _singleton = OllamaLLM(
            host=s.OLLAMA_HOST,
            model=s.OLLAMA_MODEL,
            api_key=s.OLLAMA_API_KEY,
            timeout=s.OLLAMA_TIMEOUT,
            keep_alive=s.OLLAMA_KEEP_ALIVE,
        )
        log.info("LLM: ollama %s @ %s", s.OLLAMA_MODEL, s.OLLAMA_HOST)
        return _singleton
    if p == "openai":
        if not s.OPENAI_API_KEY:
            log.warning("LLM_PROVIDER=openai but OPENAI_API_KEY is empty — heuristics")
            return None
        _singleton = OpenAILLM(s.OPENAI_API_KEY, s.OPENAI_MODEL, s.OPENAI_BASE_URL)
        log.info("LLM: openai %s", s.OPENAI_MODEL)
        return _singleton
    log.warning("unknown LLM_PROVIDER=%r — heuristics", p)
    return None
