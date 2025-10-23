from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv()


class Settings(BaseSettings):
    database_url: str = Field(
        "postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs",
        alias="DATABASE_URL",
    )
    environment: str = Field("development", alias="ENVIRONMENT")
    debug: bool = Field(False, alias="DEBUG")
    discovery_base_url: str = Field(
        "https://boards.greenhouse.io", alias="DISCOVERY_BASE_URL"
    )
    discovery_sitemap_url: str = Field(
        "https://boards.greenhouse.io/sitemap.xml", alias="DISCOVERY_SITEMAP_URL"
    )
    discovery_interval_hours: int = Field(3, alias="DISCOVERY_INTERVAL_HOURS")
    max_orgs_per_run: int = Field(100, alias="MAX_ORGS_PER_RUN")
    requests_per_minute: int = Field(60, alias="REQUESTS_PER_MINUTE")
    request_timeout_seconds: int = Field(5, alias="REQUEST_TIMEOUT_SECONDS")
    allowed_domains: str = Field("boards.greenhouse.io", alias="ALLOWED_DOMAINS")
    enable_greenhouse: bool = Field(True, alias="ENABLE_GREENHOUSE")
    github_max_age_days: int = Field(3, alias="GITHUB_MAX_AGE_DAYS")
    simplify_positions_urls: str = Field(
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json",
        alias="SIMPLIFY_POSITIONS_URLS",
    )
    new_grad_2026_urls: str = Field(
        "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/.github/scripts/listings.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/src/data/positions.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/data/positions.json",
        alias="NEW_GRAD_2026_URLS",
    )
    slack_bot_token: str | None = Field(None, alias="SLACK_BOT_TOKEN")
    slack_app_level_token: str | None = Field(None, alias="SLACK_APP_LEVEL_TOKEN")
    slack_signing_secret: str | None = Field(None, alias="SLACK_SIGNING_SECRET")
    slack_jobs_feed_channel: str | None = Field(None, alias="SLACK_JOBS_FEED_CHANNEL")
    slack_jobs_drafts_channel: str | None = Field(None, alias="SLACK_JOBS_DRAFTS_CHANNEL")
    digest_batch_size: int = Field(20, alias="DIGEST_BATCH_SIZE")
    scheduler_window_start_hour_pt: int = Field(7, alias="SCHEDULER_WINDOW_START_HOUR_PT")
    scheduler_window_end_hour_pt: int = Field(23, alias="SCHEDULER_WINDOW_END_HOUR_PT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def sqlalchemy_database_uri(self) -> str:
        return self.database_url

    @property
    def is_development(self) -> bool:
        return self.environment.lower() == "development"

    @property
    def allowed_domains_list(self) -> list[str]:
        return [domain.strip() for domain in self.allowed_domains.split(",") if domain.strip()]

    @property
    def simplify_positions_url_list(self) -> list[str]:
        return [
            url.strip()
            for url in self.simplify_positions_urls.split(",")
            if url.strip()
        ]

    @property
    def new_grad_positions_url_list(self) -> list[str]:
        return [
            url.strip()
            for url in self.new_grad_2026_urls.split(",")
            if url.strip()
        ]

    @property
    def github_max_age_delta(self):
        from datetime import timedelta

        return timedelta(days=self.github_max_age_days)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
