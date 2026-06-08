from __future__ import annotations

import inspect

from app.db import Database


def test_memory_upsert_casts_nullable_coordinates() -> None:
    source = inspect.getsource(Database.upsert_memory)

    assert source.count("CAST(%(lon)s AS double precision)") >= 4
    assert source.count("CAST(%(lat)s AS double precision)") >= 4
