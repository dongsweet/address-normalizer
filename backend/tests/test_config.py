from __future__ import annotations

from app.config import Settings


def test_qwen_and_hive_configuration_flags() -> None:
    settings = Settings(
        qwen_base_url="http://qwen.internal/v1",
        qwen_model="qwen3",
        hive_enabled=True,
        hive_host="hive",
        hive_database="default",
        hive_table="ysk_datahub_address_standed",
    )

    assert settings.qwen_configured is True
    assert settings.hive_configured is True
