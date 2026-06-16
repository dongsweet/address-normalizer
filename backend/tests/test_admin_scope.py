from __future__ import annotations

from app.admin_scope import query_without_admin_scope, resolve_admin_hint, set_admin_divisions


def test_admin_scope_extracts_city_aliases_from_national_dictionary() -> None:
    suzhou = resolve_admin_hint("苏州华府写字楼")
    nanjing = resolve_admin_hint("南京市华府写字楼")

    assert suzhou.city == "苏州市"
    assert suzhou.district is None
    assert suzhou.stripped_text == "华府写字楼"
    assert nanjing.city == "南京市"
    assert nanjing.stripped_text == "华府写字楼"


def test_query_without_admin_scope_keeps_non_admin_address_anchor() -> None:
    assert query_without_admin_scope("江苏省南京市光明中路5981华府写字楼") == "光明中路5981华府写字楼"


def test_admin_scope_can_load_divisions_from_database_rows() -> None:
    set_admin_divisions(
        [
            {"level": 3, "province": "江苏省", "city": "南京市", "name": "玄武区"},
            {"level": 3, "province": "江苏省", "city": "苏州市", "name": "姑苏区"},
            {"level": 3, "province": "新疆维吾尔自治区", "city": "乌鲁木齐市", "name": "沙依巴克区"},
        ]
    )
    try:
        hint = resolve_admin_hint("南京玄武区华府写字楼")
        assert hint.province == "江苏省"
        assert hint.city == "南京市"
        assert hint.district == "玄武区"
        assert hint.stripped_text == "华府写字楼"
    finally:
        set_admin_divisions(None)
