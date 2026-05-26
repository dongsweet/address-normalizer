from __future__ import annotations

import csv
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
                    AND timestamp < (%(end_date)s::date + interval '1 day')
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
                memory.normalized_address AS full_address,
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
                    similarity(COALESCE(memory.components->>'name', parent.components->>'name', ''), %(query)s)
                ) AS score,
                'business-confirmed memory' AS evidence,
                jsonb_build_object(
                    'anchor_type', memory.anchor_type,
                    'anchor_id', memory.anchor_id,
                    'anchor_source', memory.anchor_source,
                    'hit_count', memory.hit_count
                ) AS metadata
            FROM address_memory AS memory
            LEFT JOIN address_memory AS parent
                ON memory.anchor_type = 'memory'
                AND parent.id::text = memory.anchor_id
            WHERE
                memory.search_text %% %(query)s
                OR memory.normalized_address ILIKE %(like_query)s
                OR memory.raw_address_pattern ILIKE %(like_query)s
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
        anchor_type = payload.get("anchor_type")
        anchor_id = payload.get("anchor_id")
        anchor_source = payload.get("anchor_source")
        search_text = " ".join(
            part
            for part in [
                payload.get("raw_address"),
                payload.get("normalized_address"),
                components.get("name"),
                components.get("category"),
                components.get("province"),
                components.get("city"),
                components.get("district"),
                components.get("town"),
                components.get("address_detail"),
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
                    "raw_address": payload.get("raw_address"),
                    "normalized_address": payload.get("normalized_address"),
                    "components": Jsonb(components),
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "anchor_source": anchor_source,
                    "city": components.get("city"),
                    "district": components.get("district"),
                    "lon": lon,
                    "lat": lat,
                    "confirmed_by": payload.get("confirmed_by"),
                    "search_text": search_text,
                },
            ).fetchone()
            conn.commit()
        return int(row["id"])

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
