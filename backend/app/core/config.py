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
    JWT_TTL_MIN: int = 60
    MASTER_KMS_KEY_B64: str = ""

    DATABASE_URL: str = "postgresql+psycopg://oracle:oracle@localhost:5432/oracle"
    REDIS_URL: str = "redis://localhost:6379/0"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = ""

    POLYMARKET_CLOB_HOST: str = "https://clob.polymarket.com"
    POLYMARKET_GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    POLYMARKET_CHAIN_ID: int = 137

    LIVE_TRADING: bool = False
    GLOBAL_MAX_DAILY_USDC: float = 100.0
    MIN_EDGE: float = 0.04


@lru_cache
def get_settings() -> Settings:
    return Settings()
