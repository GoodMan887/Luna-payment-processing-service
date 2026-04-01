from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict
)
from functools import lru_cache
from pydantic import (
    PostgresDsn,
    RabbitmqDsn
)
from typing import (
    Literal,
    Union
)


class Settings(BaseSettings):
    app_name: str = "Async Payment Processing Service"
    environment: Literal["development", "staging", "production"]
    debug: bool
    database_pool_size: int = 20
    database_url: Union[PostgresDsn, str]
    rabbitmq_url: Union[RabbitmqDsn, str]
    x_api_key: str
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    cors_origins: list[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = Settings()
