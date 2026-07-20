from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    spring_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of Spring chunk service.",
    )
    spring_chunks_path: str = Field(
        default="/api/v1/chunks/law-chunks",
        description="Spring endpoint path for paged law chunk retrieval.",
    )
    spring_precedent_chunks_path: str = Field(
        default="/api/v1/chunks/prec-chunks",
        description="Spring endpoint path for paged precedent chunk retrieval.",
    )
    default_top_k: int = Field(default=5, ge=1, le=20)
    default_candidate_size: int = Field(default=30, ge=20, le=500)
    request_timeout_sec: float = Field(default=120.0, gt=0)
    search_cache_ttl_sec: float = Field(default=300.0, ge=0)
    search_cache_max_entries: int = Field(default=256, ge=0, le=10_000)

    embedding_enabled: bool = Field(default=True)
    openai_api_key: Optional[str] = Field(default=None)
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_embedding_model: str = Field(default="text-embedding-3-small")
    openai_embedding_timeout_sec: float = Field(default=30.0, gt=0)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    ollama_max_context_chars: int = Field(default=3000, ge=1000, le=20_000)
    ollama_num_predict: int = Field(default=480, ge=64, le=1000)
    ollama_num_ctx: int = Field(default=4096, ge=2048, le=32_768)
    ollama_keep_alive: str = "30m"
    llm_answer_cache_ttl_sec: float = Field(default=3600.0, ge=0)
    llm_answer_cache_max_entries: int = Field(default=256, ge=0, le=10_000)

    # openai_model: str = "gpt-5.5"


    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RETRIEVAL_",
        extra="ignore",
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()
