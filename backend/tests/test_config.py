from __future__ import annotations

from app.config import Settings


def test_qwen_and_standard_source_configuration_flags() -> None:
    settings = Settings(
        qwen_base_url="http://qwen.internal/v1",
        qwen_model="qwen3",
        standard_address_source="hive",
        hive_enabled=True,
        hive_host="hive",
        hive_database="default",
        hive_table="ysk_datahub_address_standed",
    )

    assert settings.qwen_configured is True
    assert settings.hive_configured is True

    doris_settings = Settings(
        standard_address_source="doris",
        doris_enabled=True,
        doris_host="doris",
        doris_database="address_normalizer",
        doris_table="ysk_datahub_address_standed",
    )

    assert doris_settings.doris_configured is True
    assert doris_settings.hive_configured is False
