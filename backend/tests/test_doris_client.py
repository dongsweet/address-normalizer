from __future__ import annotations

from app.adapters.doris_client import DorisClient, map_doris_row
from app.config import Settings


def test_map_doris_row_uses_standard_address_shape() -> None:
    candidate = map_doris_row(
        {
            "jxkid": "DORIS-001",
            "stand_address": "新疆乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
            "city": "乌鲁木齐市",
            "county": "沙依巴克区",
            "town": "友好街道",
            "community": "美美友好购物中心",
            "poi": "H&M",
            "road": "友好北路",
            "road_no": "689号",
        },
        table="ysk_datahub_address_standed",
    )

    assert candidate is not None
    assert candidate.source == "standard"
    assert candidate.candidate_id == "DORIS-001"
    assert candidate.name == "H&M"
    assert candidate.metadata["provider"] == "doris"
    assert candidate.metadata["road_no"] == "689号"


def test_doris_search_sql_uses_road_and_number_predicates() -> None:
    client = DorisClient(
        Settings(
            standard_address_source="doris",
            doris_enabled=True,
            doris_host="doris",
            doris_database="address_normalizer",
            doris_table="ysk_datahub_address_standed",
            doris_fetch_limit=20,
            candidate_limit=8,
        )
    )

    sql = client._build_search_sql(
        query="乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
        city="乌鲁木齐市",
        district="沙依巴克区",
        limit=8,
    )

    assert "FROM `address_normalizer`.`ysk_datahub_address_standed`" in sql
    assert "coalesce(`road`, '') = '友好北路'" in sql
    assert "coalesce(`road_no`, '') like '%689号%'" in sql
    assert "coalesce(`city`, '') = '乌鲁木齐市'" in sql
    assert "coalesce(`county`, '') = '沙依巴克区'" in sql
