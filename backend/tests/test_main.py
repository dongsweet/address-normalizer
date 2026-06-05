from __future__ import annotations

from app.main import _should_auto_persist, config_status
from app.schemas import NormalizedAddress


class FakeDb:
    def status(self) -> dict[str, int]:
        return {
            "poi_rows": 12,
            "memory_rows": 3,
            "memory_alias_rows": 2,
            "memory_detail_rows": 1,
        }

    def get_api_call_count(self, provider: str, start_date: str, end_date: str) -> int:
        return {"hive": 7, "qwen": 4}.get(provider, 0)


def test_config_status_reports_hive_fields(monkeypatch) -> None:
    import app.main as main

    monkeypatch.setattr(main, "db", FakeDb())
    monkeypatch.setattr(main.settings, "hive_enabled", True)
    monkeypatch.setattr(main.settings, "hive_host", "hive")
    monkeypatch.setattr(main.settings, "hive_database", "default")
    monkeypatch.setattr(main.settings, "hive_table", "ysk_datahub_address_standed")

    status = config_status()

    assert status.hive == "configured"
    assert status.hive_table == "ysk_datahub_address_standed"
    assert status.hive_calls_today == 7
    assert status.qwen_calls_today == 4


def test_auto_persist_prefers_standard_hits() -> None:
    result = NormalizedAddress(
        input="光明路北小区18栋1单元2层8号",
        cleaned_input="光明路北小区18栋1单元2层8号",
        normalized_address="新疆维吾尔自治区乌鲁木齐市天山区光明路北小区18栋-1单元-2楼-8号",
        output_line="新疆维吾尔自治区乌鲁木齐市天山区光明路北小区18栋-1单元-2楼-8号",
        components={
            "name": "光明路北小区",
            "city": "乌鲁木齐市",
            "district": "天山区",
            "building": "18栋",
            "unit": "1单元",
            "floor": "2楼",
            "room": "8号",
            "address_detail": "18栋-1单元-2楼-8号",
        },
        anchor_type="standard",
        anchor_id="HIVE-005",
        source="standard",
        confidence=0.97,
        match_level="standard",
    )

    assert _should_auto_persist(result) is True
