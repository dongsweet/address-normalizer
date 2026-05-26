#!/usr/bin/env python3
"""Fetch a small public POI candidate table from Overture Maps.

The output is meant for address-normalization demos: it is a public POI
candidate library, not an authoritative government standard-address table.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb


DEFAULT_RELEASE = "2026-05-20.0"
DEFAULT_BBOX = (87.35, 43.65, 88.05, 44.05)


@dataclass(frozen=True)
class PoiRow:
    source_id: str
    name: str
    freeform_address: str
    locality_raw: str
    region_raw: str
    category: str
    lon: float
    lat: float
    confidence: float
    province: str
    city: str
    district: str
    town: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        default=DEFAULT_BBOX,
        help="Bounding box to sample. Default is central Urumqi.",
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--output",
        default="data/public_poi/urumqi_overture_poi_sample.csv",
    )
    parser.add_argument("--id-prefix", default="OVT_URC")
    return parser.parse_args()


def has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def compact_parts(parts: Iterable[str]) -> str:
    result = ""
    for raw_part in parts:
        part = (raw_part or "").strip()
        if not part:
            continue
        if part in result:
            continue
        if result and part.startswith(result):
            result = part
            continue
        if result.endswith(part):
            continue
        result += part
    return result


def clean_freeform_address(address: str, components: Iterable[str]) -> str:
    cleaned = (address or "").strip()
    cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
    for component in components:
        component = (component or "").strip()
        if component:
            cleaned = cleaned.replace(component, "")
    cleaned = re.sub(r"\s*[,，]\s*", "，", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" ，,")
    return cleaned.strip()


def chinese_primary_name(name: str) -> str:
    name = (name or "").strip()
    match = re.match(r"^([\u4e00-\u9fff]+(?:省|市|区|县|州|旗|盟|乡|镇|街道|团|自治区)?)", name)
    if match:
        return match.group(1)
    if has_cjk(name):
        return "".join(re.findall(r"[\u4e00-\u9fff]+", name))
    return name


def make_full_address(row: PoiRow) -> str:
    prefix = compact_parts([row.province, row.city, row.district, row.town])
    address = clean_freeform_address(
        row.freeform_address,
        [row.province, row.city, row.district, row.town],
    )
    name = (row.name or "").strip()

    body = compact_parts([prefix, address])
    if name and name not in body:
        body = compact_parts([body, name])
    return body


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET s3_region='us-west-2';")
    return con


def fetch_rows(
    con: duckdb.DuckDBPyConnection,
    release: str,
    bbox: tuple[float, float, float, float],
    limit: int,
) -> list[PoiRow]:
    min_lon, min_lat, max_lon, max_lat = bbox
    places_path = (
        f"s3://overturemaps-us-west-2/release/{release}/"
        "theme=places/type=place/*"
    )
    divisions_path = (
        f"s3://overturemaps-us-west-2/release/{release}/"
        "theme=divisions/type=division_area/*"
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE candidate_places AS
        SELECT
            id AS source_id,
            names.primary AS name,
            addresses[1].freeform AS freeform_address,
            addresses[1].locality AS locality_raw,
            addresses[1].region AS region_raw,
            categories.primary AS category,
            ST_X(geometry) AS lon,
            ST_Y(geometry) AS lat,
            confidence,
            geometry
        FROM read_parquet(?)
        WHERE bbox.xmin BETWEEN ? AND ?
          AND bbox.ymin BETWEEN ? AND ?
          AND ST_X(geometry) BETWEEN ? AND ?
          AND ST_Y(geometry) BETWEEN ? AND ?
          AND len(addresses) > 0
          AND addresses[1].country = 'CN'
          AND names.primary IS NOT NULL
        LIMIT ?
        """,
        [
            places_path,
            min_lon,
            max_lon,
            min_lat,
            max_lat,
            min_lon,
            max_lon,
            min_lat,
            max_lat,
            limit * 6,
        ],
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE candidate_divisions AS
        SELECT
            names.primary AS name,
            subtype,
            geometry,
            bbox
        FROM read_parquet(?)
        WHERE country = 'CN'
          AND bbox.xmin <= ?
          AND bbox.xmax >= ?
          AND bbox.ymin <= ?
          AND bbox.ymax >= ?
          AND subtype IN ('region', 'county', 'localadmin', 'locality')
        """,
        [divisions_path, max_lon, min_lon, max_lat, min_lat],
    )

    rows = con.execute(
        """
        SELECT
            p.source_id,
            p.name,
            p.freeform_address,
            p.locality_raw,
            p.region_raw,
            p.category,
            p.lon,
            p.lat,
            p.confidence,
            (
                SELECT d.name
                FROM candidate_divisions d
                WHERE d.subtype = 'region'
                  AND d.bbox.xmin <= p.lon AND d.bbox.xmax >= p.lon
                  AND d.bbox.ymin <= p.lat AND d.bbox.ymax >= p.lat
                  AND ST_Contains(d.geometry, p.geometry)
                ORDER BY ST_Area(d.geometry) ASC
                LIMIT 1
            ) AS province,
            (
                SELECT d.name
                FROM candidate_divisions d
                WHERE d.subtype = 'county'
                  AND d.name LIKE '%市%'
                  AND d.bbox.xmin <= p.lon AND d.bbox.xmax >= p.lon
                  AND d.bbox.ymin <= p.lat AND d.bbox.ymax >= p.lat
                  AND ST_Contains(d.geometry, p.geometry)
                ORDER BY ST_Area(d.geometry) ASC
                LIMIT 1
            ) AS city,
            (
                SELECT d.name
                FROM candidate_divisions d
                WHERE d.subtype = 'localadmin'
                  AND d.bbox.xmin <= p.lon AND d.bbox.xmax >= p.lon
                  AND d.bbox.ymin <= p.lat AND d.bbox.ymax >= p.lat
                  AND ST_Contains(d.geometry, p.geometry)
                ORDER BY ST_Area(d.geometry) ASC
                LIMIT 1
            ) AS district,
            (
                SELECT d.name
                FROM candidate_divisions d
                WHERE d.subtype = 'locality'
                  AND d.bbox.xmin <= p.lon AND d.bbox.xmax >= p.lon
                  AND d.bbox.ymin <= p.lat AND d.bbox.ymax >= p.lat
                  AND ST_Contains(d.geometry, p.geometry)
                ORDER BY ST_Area(d.geometry) ASC
                LIMIT 1
            ) AS town
        FROM candidate_places p
        ORDER BY p.confidence DESC, p.source_id
        """
    ).fetchall()

    parsed: list[PoiRow] = []
    seen_source_ids: set[str] = set()
    for row in rows:
        poi = PoiRow(*["" if value is None else value for value in row])
        poi = PoiRow(
            source_id=poi.source_id,
            name=poi.name,
            freeform_address=poi.freeform_address,
            locality_raw=poi.locality_raw,
            region_raw=poi.region_raw,
            category=poi.category,
            lon=poi.lon,
            lat=poi.lat,
            confidence=poi.confidence,
            province=chinese_primary_name(poi.province),
            city=chinese_primary_name(poi.city),
            district=chinese_primary_name(poi.district),
            town=chinese_primary_name(poi.town),
        )
        if poi.source_id in seen_source_ids:
            continue
        if not (has_cjk(poi.name) or has_cjk(poi.freeform_address)):
            continue
        parsed.append(poi)
        seen_source_ids.add(poi.source_id)
        if len(parsed) >= limit:
            break
    return parsed


def write_csv(rows: list[PoiRow], output: Path, release: str, id_prefix: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "standard_id",
        "source",
        "source_release",
        "source_id",
        "name",
        "category",
        "province",
        "city",
        "district",
        "town",
        "freeform_address",
        "clean_address",
        "locality_raw",
        "region_raw",
        "full_address",
        "lon",
        "lat",
        "confidence",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            writer.writerow(
                {
                    "standard_id": f"{id_prefix}_{idx:06d}",
                    "source": "Overture Maps Places",
                    "source_release": release,
                    "source_id": row.source_id,
                    "name": row.name,
                    "category": row.category,
                    "province": row.province,
                    "city": row.city,
                    "district": row.district,
                    "town": row.town,
                    "freeform_address": row.freeform_address,
                    "clean_address": clean_freeform_address(
                        row.freeform_address,
                        [row.province, row.city, row.district, row.town],
                    ),
                    "locality_raw": row.locality_raw,
                    "region_raw": row.region_raw,
                    "full_address": make_full_address(row),
                    "lon": f"{row.lon:.8f}",
                    "lat": f"{row.lat:.8f}",
                    "confidence": f"{row.confidence:.6f}",
                }
            )


def main() -> None:
    args = parse_args()
    con = connect()
    rows = fetch_rows(con, args.release, tuple(args.bbox), args.limit)
    output = Path(args.output)
    write_csv(rows, output, args.release, args.id_prefix)
    print(f"Wrote {len(rows)} POI rows to {output}")


if __name__ == "__main__":
    main()
