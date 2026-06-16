from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Address Normalizer"
    environment: str = "dev"
    database_url: str = "postgresql://address:address@localhost:5432/address_normalizer"
    cors_origins: str = "http://localhost:5173"

    auto_init_db: bool = True
    auto_seed_public_poi: bool = True
    public_poi_csv: Path = Path("data/public_poi/urumqi_overture_poi_sample.csv")

    qwen_base_url: str | None = None
    qwen_api_key: str | None = None
    qwen_model: str = "qwen3.6-27b"
    qwen_timeout_seconds: float = 20.0
    cleaning_repair_enabled: bool = True
    cleaning_repair_min_score: float = 0.86

    standard_address_source: Literal["hive", "doris"] = "hive"

    hive_enabled: bool = False
    hive_host: str | None = None
    hive_port: int = 10000
    hive_database: str = "default"
    hive_table: str = "ysk_datahub_address_standed"
    hive_username: str | None = None
    hive_password: str | None = None
    hive_auth_mechanism: str = "PLAIN"
    hive_query_timeout_seconds: float = 8.0
    hive_fetch_limit: int = 20

    doris_enabled: bool = False
    doris_host: str | None = None
    doris_port: int = 9030
    doris_database: str = "address_normalizer"
    doris_table: str = "ysk_datahub_address_standed"
    doris_username: str = "root"
    doris_password: str | None = None
    doris_query_timeout_seconds: float = 8.0
    doris_fetch_limit: int = 20

    mgeo_enabled: bool = False
    mgeo_url: str | None = None
    mgeo_timeout_seconds: float = 10.0

    qwen_daily_quota: int | None = None
    qwen_monthly_quota: int | None = None

    recall_scope_mode: Literal["fixed", "auto", "off"] = "auto"
    default_city: str | None = None
    candidate_limit: int = 8
    max_batch_concurrency: int = 4

    fast_path_enabled: bool = True
    fast_path_score: float = 0.95
    fast_path_gap: float = 0.12
    memory_fast_path_score: float = 0.94
    standard_fast_path_score: float = 0.96
    auto_memory_min_confidence: float = 0.9

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def qwen_configured(self) -> bool:
        return bool(self.qwen_base_url and self.qwen_model)

    @property
    def hive_configured(self) -> bool:
        return bool(
            self.standard_address_source == "hive"
            and self.hive_enabled
            and self.hive_host
            and self.hive_table
            and self.hive_database
        )

    @property
    def doris_configured(self) -> bool:
        return bool(
            self.standard_address_source == "doris"
            and self.doris_enabled
            and self.doris_host
            and self.doris_table
            and self.doris_database
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
