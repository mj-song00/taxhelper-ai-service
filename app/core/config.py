from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    spring_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of Spring chunk service.",
    )
    spring_chunks_path: str = Field(
        default="/api/laws/chunks/law-chunks",
        description="Spring endpoint path for paged chunk retrieval.",
    )
    default_top_k: int = Field(default=5, ge=1, le=20)
    default_candidate_size: int = Field(default=100, ge=20, le=500)
    request_timeout_sec: float = Field(default=8.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RETRIEVAL_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
