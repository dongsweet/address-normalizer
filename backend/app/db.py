from __future__ import annotations

import csv
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas import AddressCandidate


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS poi_catalog (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'public_snapshot',
    provider TEXT NOT NULL,
    provider_poi_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    town TEXT,
    address TEXT,
    clean_address TEXT,
    full_address TEXT NOT NULL,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geom GEOGRAPHY(POINT, 4326),
    confidence DOUBLE PRECISION,
    source_release TEXT,
    source_license TEXT,
    license_scope TEXT,
    search_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_poi_id)
);

CREATE INDEX IF NOT EXISTS idx_poi_catalog_geom ON poi_catalog USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_poi_catalog_search_trgm ON poi_catalog USING GIN (search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_poi_catalog_full_address_trgm ON poi_catalog USING GIN (full_address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_poi_catalog_city_district ON poi_catalog (city, district);

CREATE TABLE IF NOT EXISTS standard_address (
    id BIGSERIAL PRIMARY KEY,
    standard_address_id TEXT UNIQUE NOT NULL,
    province TEXT,
    city TEXT,
    district TEXT,
    town TEXT,
    road TEXT,
    road_no TEXT,
    community TEXT,
    building TEXT,
    unit_no TEXT,
    room_no TEXT,
    full_address TEXT NOT NULL,
    adcode TEXT,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geom GEOGRAPHY(POINT, 4326),
    source TEXT,
    version TEXT,
    search_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_standard_address_geom ON standard_address USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_standard_address_search_trgm ON standard_address USING GIN (search_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS address_memory (
    id BIGSERIAL PRIMARY KEY,
    raw_address_pattern TEXT NOT NULL,
    normalized_address TEXT NOT NULL,
    components JSONB NOT NULL DEFAULT '{}'::jsonb,
    anchor_type TEXT NOT NULL DEFAULT 'business_memory',
    anchor_id TEXT,
    anchor_source TEXT,
    city TEXT,
    district TEXT,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geom GEOGRAPHY(POINT, 4326),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.9,
    confirmed_by TEXT,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_text TEXT NOT NULL DEFAULT '',
    UNIQUE (raw_address_pattern, normalized_address)
);

CREATE INDEX IF NOT EXISTS idx_address_memory_geom ON address_memory USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_address_memory_search_trgm ON address_memory USING GIN (search_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS address_memory_alias (
    id BIGSERIAL PRIMARY KEY,
    memory_id BIGINT NOT NULL REFERENCES address_memory(id) ON DELETE CASCADE,
    alias_text TEXT NOT NULL,
    alias_kind TEXT NOT NULL DEFAULT 'observed',
    city TEXT,
    district TEXT,
    confirmed_by TEXT,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (memory_id, alias_text)
);

CREATE INDEX IF NOT EXISTS idx_address_memory_alias_memory_id ON address_memory_alias (memory_id);
CREATE INDEX IF NOT EXISTS idx_address_memory_alias_text_trgm ON address_memory_alias USING GIN (alias_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_memory_alias_text_trgm ON address_memory_alias USING gist (alias_text gist_trgm_ops);

INSERT INTO address_memory_alias (
    memory_id, alias_text, alias_kind, city, district, confirmed_by
)
SELECT memory.id, aliases.alias_text, 'backfill', memory.city, memory.district, memory.confirmed_by
FROM address_memory AS memory
CROSS JOIN LATERAL (
    VALUES
        (NULLIF(BTRIM(memory.raw_address_pattern), '')),
        (NULLIF(BTRIM(memory.normalized_address), '')),
        (NULLIF(BTRIM(memory.components->>'name'), ''))
) AS aliases(alias_text)
WHERE aliases.alias_text IS NOT NULL
ON CONFLICT (memory_id, alias_text) DO NOTHING;

CREATE TABLE IF NOT EXISTS address_detail_memory (
    id BIGSERIAL PRIMARY KEY,
    memory_id BIGINT NOT NULL REFERENCES address_memory(id) ON DELETE CASCADE,
    raw_address_pattern TEXT NOT NULL,
    raw_detail TEXT,
    normalized_detail TEXT NOT NULL,
    components JSONB NOT NULL DEFAULT '{}'::jsonb,
    anchor_type TEXT,
    anchor_id TEXT,
    anchor_source TEXT,
    city TEXT,
    district TEXT,
    confirmed_by TEXT,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (memory_id, raw_address_pattern, normalized_detail)
);

CREATE INDEX IF NOT EXISTS idx_address_detail_memory_id ON address_detail_memory (memory_id);
CREATE INDEX IF NOT EXISTS idx_address_detail_memory_detail_trgm ON address_detail_memory USING GIN (normalized_detail gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_detail_normalized_trgm ON address_detail_memory USING gist (normalized_detail gist_trgm_ops);

CREATE TABLE IF NOT EXISTS normalization_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS normalization_result (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID REFERENCES normalization_job(id) ON DELETE CASCADE,
    raw_address TEXT NOT NULL,
    normalized_address TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_call_log (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    call_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_query TEXT,
    response_status TEXT,
    http_status INTEGER,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    result_count INTEGER,
    error_message TEXT,
    tokens_used INTEGER,
    cost_cents INTEGER DEFAULT 0,
    job_id UUID,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_api_call_log_provider_ts ON api_call_log (provider, timestamp);
CREATE INDEX IF NOT EXISTS idx_api_call_log_date ON api_call_log (((timestamp AT TIME ZONE 'UTC')::date));
"""


class Database:
    def __init__(self, url: str) -> None:
        self.url = url

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            yield conn

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.execute(SCHEMA_SQL)
            conn.commit()

    def seed_public_poi(self, csv_path: Path) -> int:
        if not csv_path.exists():
            return 0
        count = 0
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file, self.connection() as conn:
            reader = csv.DictReader(file)
            for row in reader:
                lon = _float_or_none(row.get("lon"))
                lat = _float_or_none(row.get("lat"))
                search_text = " ".join(
                    part
                    for part in [
                        row.get("name"),
                        row.get("province"),
                        row.get("city"),
                        row.get("district"),
                        row.get("town"),
                        row.get("clean_address"),
                        row.get("full_address"),
                    ]
                    if part
                )
                conn.execute(
                    """
                    INSERT INTO poi_catalog (
                        source_type, provider, provider_poi_id, name, category,
                        province, city, district, town, address, clean_address,
                        full_address, lon, lat, geom, confidence, source_release,
                        source_license, license_scope, search_text
                    )
                    VALUES (
                        'public_snapshot', 'overture', %(source_id)s, %(name)s, %(category)s,
                        %(province)s, %(city)s, %(district)s, %(town)s, %(address)s, %(clean_address)s,
                        %(full_address)s, %(lon)s, %(lat)s,
                        CASE
                            WHEN %(lon)s IS NULL OR %(lat)s IS NULL THEN NULL
                            ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                        END,
                        %(confidence)s, %(source_release)s, 'Overture Maps data license',
                        'public snapshot for demo and prototyping', %(search_text)s
                    )
                    ON CONFLICT (provider, provider_poi_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        category = EXCLUDED.category,
                        province = EXCLUDED.province,
                        city = EXCLUDED.city,
                        district = EXCLUDED.district,
                        town = EXCLUDED.town,
                        address = EXCLUDED.address,
                        clean_address = EXCLUDED.clean_address,
                        full_address = EXCLUDED.full_address,
                        lon = EXCLUDED.lon,
                        lat = EXCLUDED.lat,
                        geom = EXCLUDED.geom,
                        confidence = EXCLUDED.confidence,
                        source_release = EXCLUDED.source_release,
                        search_text = EXCLUDED.search_text,
                        updated_at = now()
                    """,
                    {
                        "source_id": row.get("source_id") or row.get("standard_id"),
                        "name": row.get("name") or "",
                        "category": row.get("category"),
                        "province": row.get("province"),
                        "city": row.get("city"),
                        "district": row.get("district"),
                        "town": row.get("town"),
                        "address": row.get("freeform_address"),
                        "clean_address": row.get("clean_address"),
                        "full_address": row.get("full_address") or row.get("name") or "",
                        "lon": lon,
                        "lat": lat,
                        "confidence": _float_or_none(row.get("confidence")),
                        "source_release": row.get("source_release"),
                        "search_text": search_text,
                    },
                )
                count += 1
            conn.commit()
        return count

    def status(self) -> dict[str, int]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM poi_catalog)::int AS poi_rows,
                    (SELECT count(*) FROM address_memory)::int AS memory_rows,
                    (SELECT count(*) FROM address_memory_alias)::int AS memory_alias_rows,
                    (SELECT count(*) FROM address_detail_memory)::int AS memory_detail_rows,
                    (SELECT count(*) FROM standard_address)::int AS standard_rows
                """
            ).fetchone()
        return dict(row or {})

    def log_api_call(
        self,
        *,
        provider: str,
        call_type: str,
        request_query: str | None = None,
        response_status: str = "success",
        http_status: int | None = None,
        lat: float | None = None,
        lon: float | None = None,
        result_count: int | None = None,
        error_message: str | None = None,
        tokens_used: int | None = None,
        job_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """记录一次 API 调用。非阻塞，失败不抛出异常。"""
        try:
            with self.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO api_call_log (
                        provider, call_type, request_query, response_status, http_status,
                        lat, lon, result_count, error_message, tokens_used, job_id, metadata
                    )
                    VALUES (
                        %(provider)s, %(call_type)s, %(request_query)s, %(response_status)s, %(http_status)s,
                        %(lat)s, %(lon)s, %(result_count)s, %(error_message)s, %(tokens_used)s, %(job_id)s, %(metadata)s
                    )
                    """,
                    {
                        "provider": provider,
                        "call_type": call_type,
                        "request_query": _truncate(request_query, 200),
                        "response_status": response_status,
                        "http_status": http_status,
                        "lat": lat,
                        "lon": lon,
                        "result_count": result_count,
                        "error_message": _truncate(error_message, 500),
                        "tokens_used": tokens_used,
                        "job_id": job_id,
                        "metadata": Jsonb(metadata or {}),
                    },
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            return

    def get_api_usage_summary(self, provider: str, start_date: str, end_date: str) -> dict:
        """按天统计 API 使用情况。"""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    (timestamp AT TIME ZONE 'UTC')::date::text AS date,
                    count(*)::int AS calls,
                    count(*) FILTER (WHERE response_status IS DISTINCT FROM 'success')::int AS errors,
                    coalesce(sum(result_count), 0)::int AS result_count
                FROM api_call_log
                WHERE provider = %(provider)s
                    AND timestamp >= %(start_date)s::date
                    AND timestamp < (%(end_date)s::date + interval '1 day')
                GROUP BY 1
                ORDER BY 1
                """,
                {"provider": provider, "start_date": start_date, "end_date": end_date},
            ).fetchall()
        daily = [dict(row) for row in rows]
        return {
            "provider": provider,
            "start_date": start_date,
            "end_date": end_date,
            "total_calls": sum(row["calls"] for row in daily),
            "daily": daily,
        }

    def get_api_call_count(self, provider: str, start_date: str, end_date: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT count(*)::int AS calls
                FROM api_call_log
                WHERE provider = %(provider)s
                    AND timestamp >= %(start_date)s::date
                    AND timestamp < (%(end_date)s::timestamptz + interval '1 day')
                    AND response_status != 'quota_exceeded'
                """,
                {"provider": provider, "start_date": start_date, "end_date": end_date},
            ).fetchone()
        return int((row or {}).get("calls", 0))

    def create_job(self, input_count: int) -> str:
        with self.connection() as conn:
            row = conn.execute(
                "INSERT INTO normalization_job (input_count) VALUES (%s) RETURNING id",
                (input_count,),
            ).fetchone()
            conn.commit()
        return str(row["id"])

    def save_result(self, job_id: str, raw_address: str, normalized_address: str, source: str, confidence: float, payload: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO normalization_result (job_id, raw_address, normalized_address, source, confidence, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_id, raw_address, normalized_address, source, confidence, Jsonb(payload)),
            )
            conn.commit()

    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        rows = self._search(
            """
            SELECT
                'memory' AS source,
                memory.id::text AS candidate_id,
                COALESCE(NULLIF(memory.components->>'name', ''), NULLIF(parent.components->>'name', '')) AS name,
                CASE
                    WHEN
                        NULLIF(memory.components->>'address_detail', '') IS NOT NULL
                        AND COALESCE(NULLIF(memory.components->>'name', ''), NULLIF(parent.components->>'name', '')) IS NOT NULL
                        AND POSITION(COALESCE(NULLIF(memory.components->>'name', ''), NULLIF(parent.components->>'name', '')) IN memory.normalized_address) > 0
                    THEN LEFT(
                        memory.normalized_address,
                        POSITION(COALESCE(NULLIF(memory.components->>'name', ''), NULLIF(parent.components->>'name', '')) IN memory.normalized_address)
                            + LENGTH(COALESCE(NULLIF(memory.components->>'name', ''), NULLIF(parent.components->>'name', '')))
                            - 1
                    )
                    WHEN
                        NULLIF(memory.components->>'address_detail', '') IS NOT NULL
                        AND RIGHT(memory.normalized_address, LENGTH(memory.components->>'address_detail')) = memory.components->>'address_detail'
                    THEN LEFT(memory.normalized_address, LENGTH(memory.normalized_address) - LENGTH(memory.components->>'address_detail'))
                    ELSE memory.normalized_address
                END AS full_address,
                COALESCE(NULLIF(memory.components->>'province', ''), NULLIF(parent.components->>'province', '')) AS province,
                COALESCE(NULLIF(memory.components->>'city', ''), NULLIF(parent.components->>'city', '')) AS city,
                COALESCE(NULLIF(memory.components->>'district', ''), NULLIF(parent.components->>'district', '')) AS district,
                COALESCE(NULLIF(memory.components->>'town', ''), NULLIF(parent.components->>'town', '')) AS town,
                COALESCE(NULLIF(memory.components->>'category', ''), NULLIF(parent.components->>'category', '')) AS category,
                memory.lon,
                memory.lat,
                GREATEST(
                    similarity(memory.search_text, %(query)s),
                    similarity(memory.normalized_address, %(query)s),
                    similarity(memory.raw_address_pattern, %(query)s),
                    similarity(COALESCE(memory.components->>'name', parent.components->>'name', ''), %(query)s),
                    CASE WHEN memory.raw_address_pattern = %(query)s THEN 0.96 ELSE 0 END,
                    CASE WHEN POSITION(memory.raw_address_pattern IN %(query)s) > 0 THEN 0.86 ELSE 0 END,
                    COALESCE(alias_match.score, 0)
                ) AS score,
                CASE
                    WHEN alias_match.alias_text IS NOT NULL THEN 'business-confirmed memory alias'
                    ELSE 'business-confirmed memory'
                END AS evidence,
                jsonb_build_object(
                    'anchor_type', memory.anchor_type,
                    'anchor_id', memory.anchor_id,
                    'anchor_source', memory.anchor_source,
                    'stored_normalized_address', memory.normalized_address,
                    'hit_count', memory.hit_count,
                    'matched_alias', alias_match.alias_text,
                    'alias_kind', alias_match.alias_kind,
                    'alias_hit_count', alias_match.hit_count
                ) AS metadata
            FROM address_memory AS memory
            LEFT JOIN address_memory AS parent
                ON memory.anchor_type = 'memory'
                AND parent.id::text = memory.anchor_id
            LEFT JOIN LATERAL (
                SELECT
                    alias.alias_text,
                    alias.alias_kind,
                    alias.hit_count,
                    GREATEST(
                        similarity(alias.alias_text, %(query)s),
                        CASE WHEN alias.alias_text = %(query)s THEN 0.98 ELSE 0 END,
                        CASE WHEN POSITION(alias.alias_text IN %(query)s) > 0 THEN 0.92 ELSE 0 END,
                        CASE WHEN alias.alias_text ILIKE %(like_query)s THEN 0.88 ELSE 0 END
                    ) AS score
                FROM address_memory_alias AS alias
                WHERE
                    alias.memory_id = memory.id
                    AND (
                        alias.alias_text %% %(query)s
                        OR alias.alias_text ILIKE %(like_query)s
                        OR POSITION(alias.alias_text IN %(query)s) > 0
                    )
                ORDER BY score DESC, alias.hit_count DESC
                LIMIT 1
            ) AS alias_match ON TRUE
            WHERE
                memory.search_text %% %(query)s
                OR memory.normalized_address ILIKE %(like_query)s
                OR memory.raw_address_pattern ILIKE %(like_query)s
                OR POSITION(memory.raw_address_pattern IN %(query)s) > 0
                OR alias_match.alias_text IS NOT NULL
            ORDER BY score DESC, memory.hit_count DESC
            LIMIT %(limit)s
            """,
            query,
            limit,
        )
        return [AddressCandidate(**row) for row in rows]

    def search_standard(self, query: str, limit: int) -> list[AddressCandidate]:
        rows = self._search(
            """
            SELECT
                'standard' AS source,
                standard_address_id AS candidate_id,
                community AS name,
                full_address,
                province,
                city,
                district,
                town,
                NULL::text AS category,
                lon,
                lat,
                GREATEST(similarity(search_text, %(query)s), similarity(full_address, %(query)s)) AS score,
                'standard-address table' AS evidence,
                jsonb_build_object('road', road, 'road_no', road_no, 'building', building, 'unit', unit_no, 'room', room_no) AS metadata
            FROM standard_address
            WHERE search_text %% %(query)s OR full_address ILIKE %(like_query)s
            ORDER BY score DESC
            LIMIT %(limit)s
            """,
            query,
            limit,
        )
        return [AddressCandidate(**row) for row in rows]

    def search_poi(self, query: str, city: str | None, limit: int) -> list[AddressCandidate]:
        rows = self._search(
            """
            SELECT
                'poi' AS source,
                id::text AS candidate_id,
                name,
                full_address,
                province,
                city,
                district,
                town,
                category,
                lon,
                lat,
                GREATEST(
                    similarity(search_text, %(query)s),
                    similarity(name, %(query)s),
                    similarity(full_address, %(query)s),
                    similarity(coalesce(clean_address, ''), %(query)s)
                ) AS score,
                provider || ':' || provider_poi_id AS evidence,
                jsonb_build_object(
                    'provider', provider,
                    'provider_poi_id', provider_poi_id,
                    'source_type', source_type,
                    'confidence', confidence,
                    'source_release', source_release
                ) AS metadata
            FROM poi_catalog
            WHERE
                (CAST(%(city)s AS text) IS NULL OR city = CAST(%(city)s AS text) OR city IS NULL)
                AND (
                    search_text %% %(query)s
                    OR name %% %(query)s
                    OR full_address ILIKE %(like_query)s
                )
            ORDER BY score DESC, confidence DESC NULLS LAST
            LIMIT %(limit)s
            """,
            query,
            limit,
            extra={"city": city},
        )
        return [AddressCandidate(**row) for row in rows]

    def upsert_memory(self, payload: dict[str, Any]) -> int:
        lon = _float_or_none(payload.get("lon"))
        lat = _float_or_none(payload.get("lat"))
        components = payload.get("components") or {}
        detail_components = _memory_detail_components(components)
        anchor_components = _memory_anchor_components(components)
        normalized_address = _memory_anchor_address(payload.get("normalized_address"), components)
        raw_address = _memory_anchor_pattern(payload.get("raw_address"), components, normalized_address)
        anchor_type = payload.get("anchor_type")
        anchor_id = payload.get("anchor_id")
        anchor_source = payload.get("anchor_source")
        search_text = " ".join(
            part
            for part in [
                raw_address,
                normalized_address,
                anchor_components.get("name"),
                anchor_components.get("category"),
                anchor_components.get("province"),
                anchor_components.get("city"),
                anchor_components.get("district"),
                anchor_components.get("town"),
            ]
            if part
        )
        with self.connection() as conn:
            anchor_type, anchor_id, anchor_source = self._resolve_memory_anchor(
                conn,
                anchor_type,
                anchor_id,
                anchor_source,
            )
            row = conn.execute(
                """
                SELECT id
                FROM address_memory
                WHERE normalized_address = %(normalized_address)s
                ORDER BY hit_count DESC, id
                LIMIT 1
                """,
                {"normalized_address": normalized_address},
            ).fetchone()
            if row:
                row = conn.execute(
                    """
                    UPDATE address_memory
                    SET
                        raw_address_pattern = CASE
                            WHEN length(%(raw_address)s) < length(raw_address_pattern) THEN %(raw_address)s
                            ELSE raw_address_pattern
                        END,
                        hit_count = address_memory.hit_count + 1,
                        last_seen_at = now(),
                        components = %(components)s,
                        anchor_type = %(anchor_type)s,
                        anchor_id = %(anchor_id)s,
                        anchor_source = %(anchor_source)s,
                        city = %(city)s,
                        district = %(district)s,
                        lon = %(lon)s,
                        lat = %(lat)s,
                        geom = CASE
                            WHEN %(lon)s IS NULL OR %(lat)s IS NULL THEN NULL
                            ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                        END,
                        search_text = %(search_text)s
                    WHERE id = %(id)s
                    RETURNING id
                    """,
                    {
                        "id": row["id"],
                        "raw_address": raw_address,
                        "components": Jsonb(anchor_components),
                        "anchor_type": anchor_type,
                        "anchor_id": anchor_id,
                        "anchor_source": anchor_source,
                        "city": anchor_components.get("city"),
                        "district": anchor_components.get("district"),
                        "lon": lon,
                        "lat": lat,
                        "search_text": search_text,
                    },
                ).fetchone()
            else:
                row = conn.execute(
                """
                INSERT INTO address_memory (
                    raw_address_pattern, normalized_address, components,
                    anchor_type, anchor_id, anchor_source, city, district,
                    lon, lat, geom, confirmed_by, search_text
                )
                VALUES (
                    %(raw_address)s, %(normalized_address)s, %(components)s,
                    %(anchor_type)s, %(anchor_id)s, %(anchor_source)s, %(city)s, %(district)s,
                    %(lon)s, %(lat)s,
                    CASE
                        WHEN %(lon)s IS NULL OR %(lat)s IS NULL THEN NULL
                        ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                    END,
                    %(confirmed_by)s, %(search_text)s
                )
                ON CONFLICT (raw_address_pattern, normalized_address) DO UPDATE SET
                    hit_count = address_memory.hit_count + 1,
                    last_seen_at = now(),
                    components = EXCLUDED.components,
                    anchor_type = EXCLUDED.anchor_type,
                    anchor_id = EXCLUDED.anchor_id,
                    anchor_source = EXCLUDED.anchor_source,
                    city = EXCLUDED.city,
                    district = EXCLUDED.district,
                    lon = EXCLUDED.lon,
                    lat = EXCLUDED.lat,
                    geom = EXCLUDED.geom,
                    search_text = EXCLUDED.search_text
                RETURNING id
                """,
                {
                    "raw_address": raw_address,
                    "normalized_address": normalized_address,
                    "components": Jsonb(anchor_components),
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "anchor_source": anchor_source,
                    "city": anchor_components.get("city"),
                    "district": anchor_components.get("district"),
                    "lon": lon,
                    "lat": lat,
                    "confirmed_by": payload.get("confirmed_by"),
                    "search_text": search_text,
                },
                ).fetchone()
            memory_id = int(row["id"])
            self._upsert_memory_aliases(
                conn,
                memory_id=memory_id,
                aliases=_memory_alias_entries(raw_address, normalized_address, anchor_components),
                components=anchor_components,
                confirmed_by=payload.get("confirmed_by"),
            )
            if detail_components:
                self._upsert_detail_memory(
                    conn,
                    memory_id=memory_id,
                    payload=payload,
                    detail_components=detail_components,
                    anchor_type=anchor_type,
                    anchor_id=anchor_id,
                    anchor_source=anchor_source,
                )
            conn.commit()
        return memory_id

    def _upsert_memory_aliases(
        self,
        conn: psycopg.Connection[Any],
        *,
        memory_id: int,
        aliases: list[tuple[str, str]],
        components: dict[str, Any],
        confirmed_by: Any,
    ) -> None:
        for alias_text, alias_kind in aliases:
            conn.execute(
                """
                INSERT INTO address_memory_alias (
                    memory_id, alias_text, alias_kind, city, district, confirmed_by
                )
                VALUES (
                    %(memory_id)s, %(alias_text)s, %(alias_kind)s, %(city)s, %(district)s, %(confirmed_by)s
                )
                ON CONFLICT (memory_id, alias_text) DO UPDATE SET
                    alias_kind = CASE
                        WHEN address_memory_alias.alias_kind = 'observed' THEN address_memory_alias.alias_kind
                        ELSE EXCLUDED.alias_kind
                    END,
                    hit_count = address_memory_alias.hit_count + 1,
                    last_seen_at = now(),
                    city = EXCLUDED.city,
                    district = EXCLUDED.district,
                    confirmed_by = EXCLUDED.confirmed_by
                """,
                {
                    "memory_id": memory_id,
                    "alias_text": alias_text,
                    "alias_kind": alias_kind,
                    "city": components.get("city"),
                    "district": components.get("district"),
                    "confirmed_by": confirmed_by,
                },
            )

    def _upsert_detail_memory(
        self,
        conn: psycopg.Connection[Any],
        *,
        memory_id: int,
        payload: dict[str, Any],
        detail_components: dict[str, Any],
        anchor_type: Any,
        anchor_id: Any,
        anchor_source: Any,
    ) -> None:
        raw_address = str(payload.get("raw_address") or "")
        conn.execute(
            """
            INSERT INTO address_detail_memory (
                memory_id, raw_address_pattern, raw_detail, normalized_detail,
                components, anchor_type, anchor_id, anchor_source,
                city, district, confirmed_by
            )
            VALUES (
                %(memory_id)s, %(raw_address)s, %(raw_detail)s, %(normalized_detail)s,
                %(components)s, %(anchor_type)s, %(anchor_id)s, %(anchor_source)s,
                %(city)s, %(district)s, %(confirmed_by)s
            )
            ON CONFLICT (memory_id, raw_address_pattern, normalized_detail) DO UPDATE SET
                hit_count = address_detail_memory.hit_count + 1,
                last_seen_at = now(),
                components = EXCLUDED.components,
                anchor_type = EXCLUDED.anchor_type,
                anchor_id = EXCLUDED.anchor_id,
                anchor_source = EXCLUDED.anchor_source,
                city = EXCLUDED.city,
                district = EXCLUDED.district,
                confirmed_by = EXCLUDED.confirmed_by
            """,
            {
                "memory_id": memory_id,
                "raw_address": raw_address,
                "raw_detail": _memory_raw_detail(raw_address, payload.get("components") or {}),
                "normalized_detail": detail_components["address_detail"],
                "components": Jsonb(detail_components),
                "anchor_type": anchor_type,
                "anchor_id": anchor_id,
                "anchor_source": anchor_source,
                "city": (payload.get("components") or {}).get("city"),
                "district": (payload.get("components") or {}).get("district"),
                "confirmed_by": payload.get("confirmed_by"),
            },
        )

    def _resolve_memory_anchor(
        self,
        conn: psycopg.Connection[Any],
        anchor_type: Any,
        anchor_id: Any,
        anchor_source: Any,
    ) -> tuple[Any, Any, Any]:
        for _ in range(5):
            if anchor_type != "memory" or not anchor_id or not str(anchor_id).isdigit():
                break
            row = conn.execute(
                """
                SELECT anchor_type, anchor_id, anchor_source
                FROM address_memory
                WHERE id = %(anchor_id)s
                """,
                {"anchor_id": anchor_id},
            ).fetchone()
            if not row:
                break
            next_type = row.get("anchor_type")
            next_id = row.get("anchor_id")
            next_source = row.get("anchor_source")
            if not next_type or (next_type == anchor_type and next_id == anchor_id):
                break
            anchor_type = next_type
            anchor_id = next_id
            anchor_source = next_source or next_type
        return anchor_type, anchor_id, anchor_source

    def _search(self, sql: str, query: str, limit: int, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "like_query": f"%{query}%",
            "limit": limit,
        }
        if extra:
            params.update(extra)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


DETAIL_COMPONENT_KEYS = {"building", "unit", "floor", "room", "address_detail"}


def _memory_anchor_components(components: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in components.items() if key not in DETAIL_COMPONENT_KEYS}


def _memory_detail_components(components: dict[str, Any]) -> dict[str, Any] | None:
    if not components.get("address_detail"):
        return None
    detail = {key: components.get(key) for key in DETAIL_COMPONENT_KEYS if components.get(key)}
    return detail or None


def _memory_anchor_address(normalized_address: Any, components: dict[str, Any]) -> str:
    address = str(normalized_address or "")
    if not components.get("address_detail"):
        return address

    name = _text_or_none(components.get("name"))
    if name and name in address:
        return address[: address.find(name) + len(name)]

    detail = _text_or_none(components.get("address_detail"))
    if detail and address.endswith(detail):
        return address[: -len(detail)].rstrip("-")
    return address


def _memory_anchor_pattern(raw_address: Any, components: dict[str, Any], fallback: str) -> str:
    raw = str(raw_address or "").strip()
    if not components.get("address_detail"):
        return raw or fallback

    input_anchor = _text_or_none(components.get("input_anchor"))
    if input_anchor:
        return input_anchor

    name = _text_or_none(components.get("name"))
    if name and name in raw:
        return raw[: raw.find(name) + len(name)]
    raw_anchor = _strip_raw_detail(raw, components)
    if raw_anchor:
        return raw_anchor
    return fallback or raw


def _memory_raw_detail(raw_address: str, components: dict[str, Any]) -> str | None:
    input_anchor = _text_or_none(components.get("input_anchor"))
    if input_anchor and input_anchor in raw_address:
        detail = raw_address[raw_address.find(input_anchor) + len(input_anchor) :].strip()
        return detail or None

    name = _text_or_none(components.get("name"))
    if name and name in raw_address:
        detail = raw_address[raw_address.find(name) + len(name) :].strip()
        return detail or None

    detail = _text_or_none(components.get("address_detail"))
    if not detail:
        return None
    compact_detail = re.sub(r"[-\s]", "", detail)
    compact_raw = re.sub(r"[-\s]", "", raw_address)
    return raw_address if compact_detail and compact_detail in compact_raw else None


def _strip_raw_detail(raw_address: str, components: dict[str, Any]) -> str | None:
    detail = _text_or_none(components.get("address_detail"))
    if not raw_address or not detail:
        return None
    if raw_address.endswith(detail):
        anchor = raw_address[: -len(detail)].rstrip("-_/\\|:：#")
        return anchor or None

    parts = [
        _text_or_none(components.get("building")),
        _text_or_none(components.get("unit")),
        _text_or_none(components.get("floor")),
        _text_or_none(components.get("room")),
    ]
    suffix = "".join(part for part in parts if part)
    if suffix and raw_address.endswith(suffix):
        anchor = raw_address[: -len(suffix)].rstrip("-_/\\|:：#")
        return anchor or None
    return None


def _memory_alias_entries(raw_address: str, normalized_address: str, components: dict[str, Any]) -> list[tuple[str, str]]:
    entries = [
        (raw_address, "observed"),
        (_text_or_none(components.get("name")), "name"),
        (normalized_address, "normalized"),
    ]
    aliases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value, alias_kind in entries:
        alias_text = _clean_alias_text(value)
        if not alias_text or alias_text in seen:
            continue
        seen.add(alias_text)
        aliases.append((alias_text, alias_kind))
    return aliases


def _clean_alias_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("－", "-").replace("—", "-").replace("～", "-")
    text = re.sub(r"[\s,，。;；]+", "", text).strip("-_/\\|:：#")
    if len(text) < 2:
        return None
    return text


def _text_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]
