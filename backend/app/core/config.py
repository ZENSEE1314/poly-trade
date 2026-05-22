from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALG: str = "HS256"
    JWT_TTL_MIN: int = 10080  # 7 days
    MASTER_KMS_KEY_B64: str = ""

    DATABASE_URL: str = "postgresql+psycopg://oracle:oracle@localhost:5432/oracle"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ─── LLM provider ─────────────────────────────────────────────────
    # "ollama" | "openai" | "none"
    # "none" disables LLM calls entirely — swarm runs on deterministic heuristics.
    LLM_PROVIDER: str = "ollama"

    # Ollama (default). Works against a local Ollama server, Ollama Cloud,
    # or any self-hosted Ollama instance. Communication uses Ollama's native
    # /api/chat endpoint (no OpenAI compatibility layer required).
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "glm-5.1:cloud"
    OLLAMA_API_KEY: str = ""           # required for Ollama Cloud (:cloud tags)
    OLLAMA_KEEP_ALIVE: str = "5m"      # how long ollama keeps the model loaded
    OLLAMA_TIMEOUT: float = 30.0

    # OpenAI (optional fallback / alternative)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = ""

    POLYMARKET_CLOB_HOST: str = "https://clob.polymarket.com"
    POLYMARKET_GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    POLYMARKET_CHAIN_ID: int = 137

    LIVE_TRADING: bool = False
    ALLOW_PK_MODE: bool = False
    GLOBAL_MAX_DAILY_USDC: float = 100.0
    MIN_EDGE: float = 0.04


@lru_cache
def get_settings() -> Settings:
    return Settings()
