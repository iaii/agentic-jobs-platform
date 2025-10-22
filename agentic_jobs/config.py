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


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
