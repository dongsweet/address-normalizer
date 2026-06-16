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
        return {"doris": 9, "hive": 7, "qwen": 4}.get(provider, 0)


class FakeStandardClient:
    provider = "doris"
    enabled = True
    table_name = "ysk_datahub_address_standed"

    def check_connection(self) -> bool:
        return True


def test_config_status_reports_standard_source_fields(monkeypatch) -> None:
    import app.main as main

    monkeypatch.setattr(main, "db", FakeDb())
    monkeypatch.setattr(main, "standard_client", FakeStandardClient())
    monkeypatch.setattr(main.settings, "standard_address_source", "doris")

    status = config_status()

    assert status.standard == "connected"
    assert status.standard_source == "doris"
    assert status.standard_table == "ysk_datahub_address_standed"
    assert status.standard_calls_today == 9
    assert status.hive == "disabled"
    assert status.recall_scope_mode == main.settings.recall_scope_mode
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


def test_auto_persist_rejects_name_only_standard_hit() -> None:
    result = NormalizedAddress(
        input="华府写字楼",
        cleaned_input="华府写字楼",
        normalized_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
        output_line="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
        components={
            "name": "华府写字楼",
            "city": "苏州市",
            "district": "吴中区",
            "town": "南湖镇",
        },
        anchor_type="standard",
        anchor_id="SYN-test100k-000036",
        source="standard",
        confidence=0.99,
        match_level="standard",
    )

    assert _should_auto_persist(result) is False


def test_auto_persist_accepts_road_number_standard_hit() -> None:
    result = NormalizedAddress(
        input="江苏省南京市光明中路5981华府写字楼",
        cleaned_input="江苏省南京市光明中路5981华府写字楼",
        normalized_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
        output_line="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
        components={
            "name": "华府写字楼",
            "city": "南京市",
            "district": "玄武区",
            "town": "金桥街道",
        },
        anchor_type="standard",
        anchor_id="SYN-test100k-000888",
        source="standard",
        confidence=0.99,
        match_level="standard",
    )

    assert _should_auto_persist(result) is True
