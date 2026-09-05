"""Application settings. Everything comes from the environment; nothing is hardcoded."""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # --- app -------------------------------------------------------------
    APP_NAME: str = "Opportunity Intelligence System"
    ENV: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # --- security --------------------------------------------------------
    SECRET_KEY: str = Field(default="")
    SECRET_ENCRYPTION_KEY: str = Field(default="")
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 14
    COOKIE_SECURE: bool = False
    # A comma-separated string, not list[str]: pydantic-settings JSON-parses complex
    # env values before any validator runs, so a plain "a,b" would raise at startup.
    CORS_ORIGINS: str = "http://localhost:3000"
    RATE_LIMIT_PER_MINUTE: int = 240
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10

    # --- database / cache ------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://ois:ois@postgres:5432/ois"
    REDIS_URL: str = "redis://redis:6379/0"
    DB_ECHO: bool = False

    # --- bootstrap admin -------------------------------------------------
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = ""
    SEED_DEMO_DATA: bool = True

    # --- ingestion -------------------------------------------------------
    SOURCE_RUN_STALE_MINUTES: int = 60
    MAX_RECORDS_PER_RUN: int = 5000
    HTTP_TIMEOUT_SECONDS: float = 30.0
    HTTP_CACHE_TTL_SECONDS: int = 900
    ROBOTS_CACHE_TTL_SECONDS: int = 86400
    USER_AGENT: str = "OpportunityIntelligenceSystem/0.2 (research; +https://example.invalid/ois)"
    CONTACT_EMAIL: str = "ois-operator@example.invalid"

    # --- opportunity gate (see docs/scoring-methodology.md) --------------
    MIN_SIGNAL_TYPES: int = 3
    MIN_INDEPENDENT_SOURCES: int = 2
    MIN_CONFIDENCE: float = 0.50
    MAX_EVIDENCE_AGE_DAYS: int = 30
    MIN_RAW_SCORE: int = 30

    # --- AI --------------------------------------------------------------
    AI_PROVIDER: Literal["echo", "openai", "anthropic", "gemini", "local"] = "echo"
    AI_MODEL_SMALL: str = "small"
    AI_MODEL_LARGE: str = "large"
    AI_DAILY_BUDGET_USD: float = 2.0
    AI_MONTHLY_BUDGET_USD: float = 40.0
    AI_SHORTLIST_SIZE: int = 20
    MIN_CITATION_DENSITY: float = 0.9
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # --- notifications ---------------------------------------------------
    # Every one of these is read from the environment. A provider whose settings
    # are blank reports itself unconfigured and the product says so, rather than
    # implying a message was delivered when nothing left the machine.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    #: Shared secret echoed by Telegram in X-Telegram-Bot-Api-Secret-Token on
    #: every webhook call, set when the webhook is registered. It is the only
    #: thing that distinguishes a real update from anyone who can POST to the
    #: URL, so the endpoint refuses to run at all while this is blank: an open
    #: webhook would let a stranger bind their own chat to another account.
    #: This is a shared secret, not a signature — it proves the caller knows the
    #: secret, and nothing about the body.
    TELEGRAM_WEBHOOK_SECRET: str = ""
    #: How long a /link code stays usable. Short on purpose: the code is pasted
    #: into a chat, and a code that never expires is a password that never does.
    TELEGRAM_LINK_CODE_TTL_MINUTES: int = 15
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_STARTTLS: bool = True
    APP_BASE_URL: str = "http://localhost:3000"

    # --- scheduling (Celery beat; see docs/scheduling.md) ------------------
    #: Master switch for the *notification* schedule. Nothing in the API or the
    #: test suite starts a scheduler: beat is its own process, and this setting
    #: only decides which periodic entries that process is handed.
    #:
    #: False (the default) leaves the schedule exactly as it has always been —
    #: nightly collection plus the stale-run janitor — and no alert dispatch or
    #: digest generation happens unless an administrator presses the button.
    #:
    #: True replaces the bare collection entry with one ordered run:
    #: collect -> check conditions and dispatch alerts -> write digests. It
    #: replaces rather than adds, so collection is never scheduled twice, and
    #: ordering is enforced by the task rather than by hoping two cron times stay
    #: far enough apart.
    SCHEDULER_ENABLED: bool = False
    #: IANA timezone name for every crontab, and for deciding which local day a
    #: weekly digest belongs to. Stored timestamps stay UTC (`enable_utc` is
    #: never turned off); this only says which wall clock fires the job.
    SCHEDULE_TIMEZONE: str = "UTC"
    #: Local hour and minute of the nightly run — collection on its own when
    #: scheduling is off, the whole ordered run when it is on.
    COLLECT_HOUR: int = 3
    COLLECT_MINUTE: int = 0
    #: The janitor cadence, previously hardcoded at every 15 minutes.
    REAP_EVERY_MINUTES: int = 15
    #: How far back a scheduled monitoring run looks for change events. It must
    #: be at least the gap between two runs, so a run that failed, was killed or
    #: was skipped is picked up by the next one. Re-presenting an event that was
    #: already delivered costs nothing: `(user_id, dedupe_key)` is unique, so the
    #: repeat is suppressed by the database rather than by the code's memory.
    MONITOR_LOOKBACK_HOURS: int = 26
    #: How many of the most recent events in that window one run considers.
    MONITOR_EVENT_LIMIT: int = 500
    #: Day name in `SCHEDULE_TIMEZONE` — monday..sunday — on which the nightly
    #: run also writes weekly digests. Weekly digests are part of that ordered
    #: run rather than a separate cron entry, so they too are written only after
    #: the monitoring they summarise has committed.
    DIGEST_WEEKLY_DAY: str = "sunday"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_runtime(self) -> None:
        """Fail loudly in production rather than running with insecure defaults."""
        problems: list[str] = []
        if not self.SECRET_KEY:
            problems.append("SECRET_KEY is empty")
        if not self.SECRET_ENCRYPTION_KEY:
            problems.append("SECRET_ENCRYPTION_KEY is empty")
        if self.ENV == "prod":
            if len(self.SECRET_KEY) < 32:
                problems.append("SECRET_KEY must be at least 32 characters in production")
            if not self.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be true in production")
            if not self.ADMIN_PASSWORD:
                problems.append("ADMIN_PASSWORD must be set in production")
        if problems:
            raise RuntimeError(
                "Refusing to start with an insecure configuration: "
                + "; ".join(problems)
                + ". Copy environment.example to .env and fill in the values."
            )


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Dev/test convenience only: generate ephemeral secrets so the app can boot.
    if not s.SECRET_KEY and s.ENV != "prod":
        s.SECRET_KEY = secrets.token_urlsafe(48)
    if not s.SECRET_ENCRYPTION_KEY and s.ENV != "prod":
        from cryptography.fernet import Fernet

        s.SECRET_ENCRYPTION_KEY = Fernet.generate_key().decode()
    s.validate_runtime()
    return s


settings = get_settings()
