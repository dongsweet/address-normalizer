from __future__ import annotations

import inspect

from app.db import AUTO_MEMORY_ALIAS_CLEANUP_SQL, Database, _memory_alias_entries


def test_memory_upsert_casts_nullable_coordinates() -> None:
    source = inspect.getsource(Database.upsert_memory)

    assert source.count("CAST(%(lon)s AS double precision)") >= 4
    assert source.count("CAST(%(lat)s AS double precision)") >= 4


def test_auto_memory_alias_cleanup_targets_auto_generic_aliases() -> None:
    assert "COALESCE(alias.confirmed_by, memory.confirmed_by) = 'auto'" in AUTO_MEMORY_ALIAS_CLEANUP_SQL
    assert "alias.alias_kind IN ('observed', 'name', 'backfill')" in AUTO_MEMORY_ALIAS_CLEANUP_SQL
    assert "BTRIM(alias.alias_text) <> BTRIM(memory.normalized_address)" in AUTO_MEMORY_ALIAS_CLEANUP_SQL
    assert "BTRIM(alias.alias_text) !~ '[0-9]'" in AUTO_MEMORY_ALIAS_CLEANUP_SQL


def test_auto_memory_aliases_skip_generic_name_only_aliases() -> None:
    aliases = _memory_alias_entries(
        "华府写字楼",
        "江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
        {"name": "华府写字楼", "city": "苏州市", "district": "吴中区"},
        confirmed_by="auto",
    )

    assert ("华府写字楼", "observed") not in aliases
    assert ("华府写字楼", "name") not in aliases
    assert aliases == [("江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼", "normalized")]


def test_auto_memory_aliases_keep_structural_observed_alias() -> None:
    aliases = _memory_alias_entries(
        "江苏省南京市光明中路5981华府写字楼",
        "江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
        {"name": "华府写字楼", "city": "南京市", "district": "玄武区"},
        confirmed_by="auto",
    )

    assert ("江苏省南京市光明中路5981华府写字楼", "observed") in aliases
    assert ("华府写字楼", "name") not in aliases


def test_search_memory_backfills_scope_from_full_address() -> None:
    db = Database("postgresql://unused")
    db._search = lambda sql, query, limit, extra=None: [  # type: ignore[method-assign]
        {
            "source": "memory",
            "candidate_id": "M-1",
            "name": "华府写字楼",
            "full_address": "江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
            "province": None,
            "city": None,
            "district": None,
            "town": None,
            "category": None,
            "lon": None,
            "lat": None,
            "score": 0.96,
            "evidence": "business-confirmed memory",
            "metadata": {},
        }
    ]

    candidates = db.search_memory("江苏华府写字楼", 5)

    assert candidates[0].province == "江苏省"
    assert candidates[0].city == "南京市"
    assert candidates[0].district == "玄武区"
