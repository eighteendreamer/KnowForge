import re
from pathlib import Path
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]

# Confidence save_chunks assigns to a tag the model produced without rule agreement. The auto-approve gate cannot
# sit above it: Qdrant payloads, filters.tags and search responses publish only approved tags, so a higher gate
# switches the whole tag feature off without raising anything.
LLM_ONLY_CONFIDENCE = 0.7


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOFORGE_", env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: SecretStr
    test_database_url: SecretStr | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_prefix: str = "knowforge:"
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: SecretStr
    jwt_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    model_api_base_url: str = ""
    model_api_key: SecretStr = SecretStr("")
    model_rerank_path: str = Field(default="/rerank", pattern=r"^(?:/[A-Za-z0-9_-]+)+$|^$")
    model_timeout_seconds: float = Field(default=30, gt=0, le=120)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_max_concurrency: int = Field(default=4, ge=1, le=32)
    model_requests_per_minute: int = Field(default=60, ge=1)
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dimension: int = Field(default=4096, ge=1)
    rerank_model: str = "Qwen/Qwen3-Reranker-4B"
    llm_model: str = "Qwen/Qwen3-14B"
    vl_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    rerank_enabled: bool = True
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = "tech_knowledge_base"
    storage_path: Path = ROOT / "data" / "storage"
    upload_max_bytes: int = 50 * 1024 * 1024
    pdf_max_pages: int = 1000
    pdf_max_pixels: int = 20_000_000
    tokenizer_path: Path = ROOT / "data" / "tokenizers" / "qwen3-embedding.json"
    embedding_max_tokens: int = 8192
    embedding_batch_size: int = 8
    chunk_size: int = 600
    chunk_overlap: int = 80
    cache_ttl_seconds: int = 300
    # The upper bound is the invariant itself: a gate above LLM_ONLY_CONFIDENCE would leave every AI tag pending.
    tag_auto_approve_confidence: float = Field(default=LLM_ONLY_CONFIDENCE, ge=0, le=LLM_ONLY_CONFIDENCE)
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout: int = Field(default=5, ge=1, le=120)
    metrics_token: SecretStr = SecretStr("")
    # 支付回调与跳转的公网基址。厂商（微信 Native、支付宝 page_pay、Stripe Checkout）都要一个服务端
    # 自己拼不出来的绝对地址，所以它必须是配置，不能让前端用 window.location.origin 兜——门户和管理端同源时
    # 那个值可能是 127.0.0.1:5173。
    site_url: str = ""
    # 加密渠道凭据的主密钥。它和被加密的凭据不能同处一地：主密钥进库等于没有加密。
    payment_master_key: SecretStr = SecretStr("")
    configuration_id: UUID | None = None
    index_fingerprint: str | None = None

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        return value

    @field_validator("model_api_base_url")
    @classmethod
    def validate_model_url(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError("Remote model API must use HTTPS")
        return value.rstrip("/")

    @field_validator("index_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("index_fingerprint must be a SHA-256 hex digest")
        return value

    @field_validator("qdrant_collection")
    @classmethod
    def validate_collection_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
            raise ValueError("qdrant_collection must be 1-100 letters, digits, underscore or hyphen")
        return value

    @field_validator("site_url")
    @classmethod
    def validate_site_url(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("site_url must be an absolute http(s) URL")
        return value.rstrip("/")

    @field_validator("payment_master_key")
    @classmethod
    def validate_payment_master_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
            raise ValueError("payment_master_key must be 32 random bytes as 64 hex characters")
        return value

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.embedding_max_tokens < self.chunk_size:
            raise ValueError("embedding_max_tokens must cover chunk_size")
        return self

    @property
    def models_configured(self) -> bool:
        return bool(self.model_api_base_url and self.model_api_key.get_secret_value())

    @property
    def payment_master_key_bytes(self) -> bytes | None:
        raw = self.payment_master_key.get_secret_value()
        return bytes.fromhex(raw) if raw else None
