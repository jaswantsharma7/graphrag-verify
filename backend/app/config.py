"""Environment-backed runtime configuration."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


# Runtime configuration and bounded resource limits.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore",
        case_sensitive=False, hide_input_in_errors=True,
    )

    app_mode: Literal["demo", "live"] = "demo"
    data_dir: Path = ROOT / "data"
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = Field(default=20, ge=1, le=200)
    max_document_chars: int = Field(default=500000, ge=100, le=10000000)
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=1024)
    max_chunks_per_document: int = Field(default=1000, ge=1, le=10000)
    max_context_chars: int = Field(default=24000, ge=1000, le=200000)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = Field(default="", repr=False, exclude=True)
    neo4j_database: str = "neo4j"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field(default="", repr=False, exclude=True)
    extraction_model: str = "gpt-4o-mini"
    generation_model: str = "gpt-4o-mini"
    evaluation_model: str = "gpt-4o-mini"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = Field(default="", repr=False, exclude=True)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=32, le=4096)
    request_timeout_seconds: float = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=2, ge=0, le=3)
    provider_concurrency: int = Field(default=4, ge=1, le=8)
    max_graph_nodes: int = Field(default=200, ge=20, le=1000)
    max_graph_edges: int = Field(default=400, ge=20, le=2000)

    @field_validator("data_dir", mode="after")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("api_key", mode="before")
    @classmethod
    def optional_key(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None

    @model_validator(mode="after")
    def coherent_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self

    def validate_live(self) -> None:
        """Validate only when initializing so health can report a degraded setup."""
        from urllib.parse import urlsplit

        for value in (self.llm_base_url, self.embedding_base_url):
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Live provider base URLs must be HTTP(S) URLs")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Provider URLs must not contain credentials or query strings")
        parsed = urlsplit(self.neo4j_uri)
        if parsed.scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}:
            raise ValueError("NEO4J_URI must use a supported Bolt or Neo4j scheme")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("NEO4J_URI must specify a host without embedded credentials")
        if not self.neo4j_username or not self.neo4j_password or not self.neo4j_database:
            raise ValueError("Live mode requires Neo4j username, password, and database")
        if not all((self.extraction_model, self.generation_model, self.evaluation_model, self.embedding_model)):
            raise ValueError("Live mode requires extraction, generation, evaluation, and embedding models")
