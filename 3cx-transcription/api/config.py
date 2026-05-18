from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    APP_ENV: str = "production"
    APP_URL: str = "http://localhost"
    SECRET_KEY: str = "change-me"

    # Database
    DATABASE_URL: str
    POSTGRES_USER: str = "transcriptions"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "transcriptions"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Google Cloud
    GCP_PROJECT_ID: str = ""
    GCP_BUCKET_NAME: str = "cachiai-recordings"
    GCP_RECORDINGS_PREFIX: str = "recordings/"
    GCP_BUCKET_LOCATION: str = "EU"
    GCP_SERVICE_ACCOUNT_JSON: str = ""

    # Pub/Sub auth
    PUBSUB_SERVICE_ACCOUNT_EMAIL: str = ""
    WEBHOOK_SECRET: str = ""

    # AssemblyAI — env file uses 'assemblyapi', normalised here
    ASSEMBLYAI_API_KEY: str = ""
    assemblyapi: str = ""  # alias from .env file
    ASSEMBLYAI_MODEL: str = "best"
    ASSEMBLYAI_SPEAKER_DIARIZATION: bool = True
    ASSEMBLYAI_WEBHOOK_SECRET: str = ""
    ASSEMBLYAI_SPEAKERS_EXPECTED: int = 2  # set to 0 to let AssemblyAI auto-detect

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_SUMMARY_MODEL: str = "gpt-5.2"
    OPENAI_FALLBACK_MODELS: str = "gpt-5,gpt-5-mini,gpt-4o"
    # Speaker re-classification model — leave blank to use OPENAI_SUMMARY_MODEL
    OPENAI_SPEAKER_MODEL: str = ""
    SPEAKER_CONFIDENCE_THRESHOLD: float = 0.75

    # Email (Gmail SMTP)
    GMAIL_ADDRESS: str = ""
    GMAIL_APP_PASSWORD: str = ""
    EMAIL_FROM_NAME: str = "3CX Transcriptions"
    REPLY_TO_EMAIL: str = ""
    ADMIN_EMAIL: str = ""

    # Behaviour
    MAX_RETRIES: int = 4
    DELETE_TEMP_FILES: bool = True
    STORE_TRANSCRIPTS: bool = False
    DEBUG_MODE: bool = False

    @property
    def effective_assemblyai_key(self) -> str:
        """Normalise assemblyapi → ASSEMBLYAI_API_KEY."""
        return self.ASSEMBLYAI_API_KEY or self.assemblyapi

    @property
    def openai_fallback_list(self) -> List[str]:
        return [m.strip() for m in self.OPENAI_FALLBACK_MODELS.split(",") if m.strip()]

    @property
    def effective_speaker_model(self) -> str:
        """Speaker re-classification model — falls back to summary model if not set."""
        return self.OPENAI_SPEAKER_MODEL.strip() or self.OPENAI_SUMMARY_MODEL

    @property
    def temp_dir(self) -> str:
        return "/tmp/recordings"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
