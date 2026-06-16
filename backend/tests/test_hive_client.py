from __future__ import annotations

from app.adapters.hive_client import HiveClient, map_hive_row
from app.config import Settings


def test_map_hive_row_uses_expected_field_fallbacks() -> None:
    candidate = map_hive_row(
        {
            "jxkid": "HIVE-001",
            "stand_address": "新疆乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
            "city": "乌鲁木齐市",
            "county": "沙依巴克区",
            "town": "友好街道",
            "community": "美美友好购物中心",
            "poi": "",
            "road": "友好北路",
            "building": "18栋",
            "unit": "1单元",
            "floor": "2楼",
            "room": "8号",
        },
        table="ysk_datahub_address_standed",
    )

    assert candidate is not None
    assert candidate.source == "standard"
    assert candidate.candidate_id == "HIVE-001"
    assert candidate.name == "美美友好购物中心"
    assert candidate.city == "乌鲁木齐市"
    assert candidate.district == "沙依巴克区"
    assert candidate.score == 0
    assert candidate.metadata["table"] == "ysk_datahub_address_standed"
    assert candidate.metadata["building"] == "18栋"


def test_hive_search_sql_escapes_user_input() -> None:
    client = HiveClient(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_database="default",
            hive_table="ysk_datahub_address_standed",
            hive_fetch_limit=20,
            candidate_limit=8,
        )
    )

    sql = client._build_search_sql(query="友好%'路", city="乌鲁木齐市", district="沙依巴克区", limit=8)

    assert "FROM `default`.`ysk_datahub_address_standed`" in sql
    assert "like '%友好%''路%'" in sql
    assert "coalesce(`county`, '') = '沙依巴克区'" in sql
    assert "limit 20" in sql.lower()


def test_hive_search_sql_uses_road_and_number_predicates() -> None:
    client = HiveClient(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_database="default",
            hive_table="ysk_datahub_address_standed",
            hive_fetch_limit=20,
            candidate_limit=8,
        )
    )

    sql = client._build_search_sql(
        query="乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
        city="乌鲁木齐市",
        district="沙依巴克区",
        limit=8,
    )

    assert "coalesce(`road`, '') = '友好北路'" in sql
    assert "coalesce(`road_no`, '') like '%689号%'" in sql
    assert "coalesce(`road_no`, '') like '%689%'" in sql
    assert "coalesce(`city`, '') = '乌鲁木齐市'" in sql
    assert "coalesce(`county`, '') = '沙依巴克区'" in sql
    assert "%乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M%" not in sql


def test_hive_search_sql_does_not_fallback_to_default_city_in_auto_mode() -> None:
    client = HiveClient(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_database="default",
            hive_table="ysk_datahub_address_standed",
            recall_scope_mode="auto",
            default_city="乌鲁木齐市",
        )
    )

    sql = client._build_search_sql(
        query="高新乡红山西大道1532号德汇家园6栋6单元18楼3115室",
        city=None,
        district=None,
        limit=8,
    )

    assert "coalesce(`city`, '') = '乌鲁木齐市'" not in sql
    assert "红山西大道" in sql
    assert "1532号" in sql
