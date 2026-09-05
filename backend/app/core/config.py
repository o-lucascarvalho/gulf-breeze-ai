from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    app_name: str = Field(default="Gulf Breeze Air Backend", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="gulf_breeze_air", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_echo_sql: bool = Field(default=False, alias="POSTGRES_ECHO_SQL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    openai_agent_model: str = Field(default="gpt-4o-mini", alias="OPENAI_AGENT_MODEL")
    embedding_dimension: int = Field(default=1536, alias="EMBEDDING_DIMENSION")
    embedding_batch_size: int = Field(default=100, alias="EMBEDDING_BATCH_SIZE")
    embedding_max_retries: int = Field(default=3, alias="EMBEDDING_MAX_RETRIES")
    embedding_timeout: float = Field(default=30.0, alias="EMBEDDING_TIMEOUT")
    semantic_max_top_k: int = Field(default=50, alias="SEMANTIC_MAX_TOP_K")
    agent_max_tool_rounds: int = Field(default=8, alias="AGENT_MAX_TOOL_ROUNDS")
    agent_max_history_messages: int = Field(default=20, alias="AGENT_MAX_HISTORY_MESSAGES")

    openai_project_id: str = Field(default="", alias="OPENAI_PROJECT_ID")
    openai_realtime_model: str = Field(default="gpt-4o-realtime-preview", alias="OPENAI_REALTIME_MODEL")
    openai_realtime_voice: str = Field(default="alloy", alias="OPENAI_REALTIME_VOICE")

    voice_agent_enabled: bool = Field(default=False, alias="VOICE_AGENT_ENABLED")
    voice_agent_api_base_url: str = Field(default="http://localhost:8000", alias="VOICE_AGENT_API_BASE_URL")
    voice_agent_api_timeout_seconds: float = Field(default=12.0, alias="VOICE_AGENT_API_TIMEOUT_SECONDS")
    voice_realtime_timeout_seconds: float = Field(default=10.0, alias="VOICE_REALTIME_TIMEOUT_SECONDS")
    voice_sideband_retry_delays_ms: str = Field(default="200,400,800,1200", alias="VOICE_SIDEBAND_RETRY_DELAYS_MS")
    voice_greeting_enabled: bool = Field(default=True, alias="VOICE_GREETING_ENABLED")
    voice_greeting_text: str = Field(
        default="Welcome to Gulf Breeze Air. How can I help you today?",
        alias="VOICE_GREETING_TEXT",
    )
    public_phone_enabled: bool = Field(default=False, alias="PUBLIC_PHONE_ENABLED")
    public_phone_number: str = Field(default="+19714586849", alias="PUBLIC_PHONE_NUMBER")

    weather_timeout_seconds: float = Field(default=3.0, alias="WEATHER_TIMEOUT_SECONDS")
    weather_cache_ttl_seconds: int = Field(default=2700, alias="WEATHER_CACHE_TTL_SECONDS")

    twilio_phone_number: str = Field(default="", alias="TWILIO_PHONE_NUMBER")
    twilio_account_sid: str = Field(default="", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(default="", alias="TWILIO_AUTH_TOKEN")
    twilio_sip_trunk_sid: str = Field(default="", alias="TWILIO_SIP_TRUNK_SID")

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        return (
            "postgresql+psycopg://"
            f"{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
