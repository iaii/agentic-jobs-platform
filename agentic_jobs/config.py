from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv(override=True)


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
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/src/data/positions.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/data/positions.json",
        alias="SIMPLIFY_POSITIONS_URLS",
    )
    new_grad_2026_urls: str = Field(
        "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/.github/scripts/listings.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/src/data/positions.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/data/positions.json",
        alias="NEW_GRAD_2026_URLS",
    )
    slack_bot_token: str | None = Field(None, alias="SLACK_BOT_TOKEN")
    slack_app_level_token: str | None = Field(None, alias="SLACK_APP_LEVEL_TOKEN")
    slack_signing_secret: str | None = Field(None, alias="SLACK_SIGNING_SECRET")
    slack_jobs_feed_channel: str | None = Field(None, alias="SLACK_JOBS_FEED_CHANNEL")
    slack_jobs_drafts_channel: str | None = Field(None, alias="SLACK_JOBS_DRAFTS_CHANNEL")
    slack_jobs_tracker_channel: str | None = Field(None, alias="SLACK_JOBS_TRACKER_CHANNEL")
    slack_jobs_archive_channel: str | None = Field(None, alias="SLACK_JOBS_ARCHIVE_CHANNEL")
    llm_backend: str = Field("lmstudio", alias="LLM_BACKEND")
    llm_model_name: str = Field("local-model", alias="LLM_MODEL_NAME")
    llm_endpoint_url: str | None = Field("http://localhost:1234/v1/chat/completions", alias="LLM_ENDPOINT_URL")
    llm_timeout_seconds: int = Field(120, alias="LLM_TIMEOUT_SECONDS")
    llm_max_user_msg_chars: int = Field(12000, alias="LLM_MAX_USER_MSG_CHARS")
    llm_api_key: str | None = Field(None, alias="LLM_API_KEY")
    ollama_api_key: str | None = Field(None, alias="OLLAMA_API_KEY")
    digest_batch_size: int = Field(20, alias="DIGEST_BATCH_SIZE")
    tracker_rows_per_page: int = Field(25, alias="TRACKER_ROWS_PER_PAGE")
    tracker_max_pages: int = Field(4, alias="TRACKER_MAX_PAGES")
    scheduler_window_start_hour_pt: int = Field(7, alias="SCHEDULER_WINDOW_START_HOUR_PT")
    scheduler_window_end_hour_pt: int = Field(23, alias="SCHEDULER_WINDOW_END_HOUR_PT")
    scheduler_timezone: str = Field("America/Los_Angeles", alias="SCHEDULER_TIMEZONE")
    job_filter_config_path: str = Field("config/job_filters.yaml", alias="JOB_FILTER_CONFIG_PATH")
    universal_sites_config_path: str = Field("config/universal_sites.yaml", alias="UNIVERSAL_SITES_CONFIG_PATH")
    universal_max_age_days: int = Field(7, alias="UNIVERSAL_MAX_AGE_DAYS")
    autofill_enabled: bool = Field(False, alias="AUTOFILL_ENABLED")
    autofill_ws_port: int = Field(8765, alias="AUTOFILL_WS_PORT")
    autofill_max_concurrency: int = Field(3, alias="AUTOFILL_MAX_CONCURRENCY")
    autofill_ops_channel: str | None = Field(None, alias="AUTOFILL_OPS_CHANNEL")
    autofill_allowed_domains: str | None = Field(None, alias="AUTOFILL_ALLOWED_DOMAINS")
    autofill_allow_account_creation: bool = Field(False, alias="AUTOFILL_ALLOW_ACCOUNT_CREATION")
    autofill_assisted_upload: bool = Field(True, alias="AUTOFILL_ASSISTED_UPLOAD")
    autofill_automation_mode: bool = Field(False, alias="AUTOFILL_AUTOMATION_MODE")
    autofill_cl_pdf_enabled: bool = Field(True, alias="AUTOFILL_CL_PDF_ENABLED")
    autofill_fake_profile_path: str = Field("config/fake_profile.yaml", alias="AUTOFILL_FAKE_PROFILE_PATH")
    autofill_api_token: str | None = Field(None, alias="AUTOFILL_API_TOKEN")
    profile_fallback_name: str = Field("Candidate", alias="PROFILE_FALLBACK_NAME")

    # -------------------------
    # Vault / Embeddings
    # -------------------------
    vault_path: str = Field("", alias="VAULT_PATH")
    embedding_model_name: str = Field("nomic-embed-text-v1.5", alias="EMBEDDING_MODEL_NAME")
    embedding_endpoint_url: str = Field("http://localhost:1234/v1/embeddings", alias="EMBEDDING_ENDPOINT_URL")
    vault_link_depth: int = Field(1, alias="VAULT_LINK_DEPTH")
    vault_top_k: int = Field(5, alias="VAULT_TOP_K")
    vault_refresh_interval_hours: int = Field(12, alias="VAULT_REFRESH_INTERVAL_HOURS")
    embedding_timeout_seconds: int = Field(30, alias="EMBEDDING_TIMEOUT_SECONDS")

    # -------------------------
    # Multi-Agent Pipeline
    # -------------------------
    pipeline_pass_threshold: float = Field(7.0, alias="PIPELINE_PASS_THRESHOLD")
    pipeline_max_revisions: int = Field(3, alias="PIPELINE_MAX_REVISIONS")

    # -------------------------
    # Memory
    # -------------------------
    memory_assessment_interval_days: int = Field(3, alias="MEMORY_ASSESSMENT_INTERVAL_DAYS")
    job_cutoff_days: int = Field(30, alias="JOB_CUTOFF_DAYS")

    # -------------------------
    # Web Scraping
    # -------------------------
    scraper_rate_limit: int = Field(5, alias="SCRAPER_RATE_LIMIT")
    scraper_timeout_seconds: int = Field(10, alias="SCRAPER_TIMEOUT_SECONDS")
    company_cache_ttl_hours: int = Field(168, alias="COMPANY_CACHE_TTL_HOURS")
    company_research_vault_subdir: str = Field("Agentic Copilot/Company Research", alias="COMPANY_RESEARCH_VAULT_SUBDIR")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _validate_config_paths(self) -> "Settings":
        for env_var, path_str in (
            ("JOB_FILTER_CONFIG_PATH", self.job_filter_config_path),
            ("UNIVERSAL_SITES_CONFIG_PATH", self.universal_sites_config_path),
        ):
            if not Path(path_str).exists():
                raise ValueError(f"{env_var}={path_str!r} does not exist")
        return self

    @model_validator(mode="after")
    def _validate_scheduler_timezone(self) -> "Settings":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(self.scheduler_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            raise ValueError(f"SCHEDULER_TIMEZONE={self.scheduler_timezone!r} is not a valid IANA timezone")
        return self

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
    def autofill_allowed_domains_list(self) -> list[str]:
        if not self.autofill_allowed_domains:
            return []
        return [domain.strip() for domain in self.autofill_allowed_domains.split(",") if domain.strip()]

    @property
    def github_max_age_delta(self):
        from datetime import timedelta

        return timedelta(days=self.github_max_age_days)

    @property
    def universal_max_age_delta(self):
        from datetime import timedelta

        return timedelta(days=self.universal_max_age_days)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
