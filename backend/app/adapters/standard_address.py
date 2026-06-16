from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.admin_scope import query_without_admin_scope
from app.schemas import AddressCandidate


STANDARD_SEARCH_COLUMNS = (
    "stand_address",
    "src_address",
    "poi",
    "community",
    "road",
    "xxdz",
)

STANDARD_SELECT_COLUMNS = [
    "jxkid",
    "cjd",
    "rjxksj",
    "xxdz",
    "row_num_id",
    "src_address",
    "stand_address",
    "city",
    "county",
    "develop_area",
    "town",
    "community",
    "village_group",
    "bus_area",
    "road",
    "sub_road",
    "road_no",
    "subroad_no",
    "poi",
    "building",
    "unit",
    "floor",
    "room",
    "part_path",
]


@dataclass(frozen=True)
class StandardSearchParts:
    road: str | None = None
    road_no: str | None = None
    name: str | None = None


def build_standard_search_sql(
    *,
    database: str,
    table: str,
    query: str,
    city: str | None,
    district: str | None,
    default_city: str | None,
    fetch_limit: int,
) -> str:
    database = safe_identifier(database)
    table = safe_identifier(table)
    stripped_query = query_without_admin_scope(query) or query
    parts = _extract_search_parts(stripped_query)
    where_clauses = _structured_where_clauses(parts)
    if not where_clauses:
        where_clauses = [_full_text_where_clause(stripped_query)]

    target_city = city or default_city
    if target_city:
        city_literal = string_literal(target_city)
        where_clauses.append("coalesce(`city`, '') = '{city}'".format(city=city_literal))
    if district:
        district_literal = string_literal(district)
        where_clauses.append("coalesce(`county`, '') = '{district}'".format(district=district_literal))

    select_sql = ", ".join(f"`{column}`" for column in STANDARD_SELECT_COLUMNS)
    return f"""
            SELECT {select_sql}
            FROM `{database}`.`{table}`
            WHERE {' AND '.join(where_clauses)}
            LIMIT {fetch_limit}
        """


def map_standard_row(row: dict[str, Any], *, table: str, provider: str) -> AddressCandidate | None:
    candidate_id = _clean_text(row.get("jxkid")) or _clean_text(row.get("row_num_id"))
    full_address = _clean_text(row.get("stand_address"))
    if not candidate_id or not full_address:
        return None

    name = _clean_text(row.get("poi")) or _clean_text(row.get("community")) or _clean_text(row.get("road"))
    metadata = {
        key: value
        for key, value in {
            "provider": provider,
            "table": table,
            "cjd": _clean_text(row.get("cjd")),
            "rjxksj": _clean_text(row.get("rjxksj")),
            "row_num_id": _clean_text(row.get("row_num_id")),
            "src_address": _clean_text(row.get("src_address")),
            "xxdz": _clean_text(row.get("xxdz")),
            "develop_area": _clean_text(row.get("develop_area")),
            "community": _clean_text(row.get("community")),
            "village_group": _clean_text(row.get("village_group")),
            "bus_area": _clean_text(row.get("bus_area")),
            "road": _clean_text(row.get("road")),
            "sub_road": _clean_text(row.get("sub_road")),
            "road_no": _clean_text(row.get("road_no")),
            "subroad_no": _clean_text(row.get("subroad_no")),
            "poi": _clean_text(row.get("poi")),
            "building": _clean_text(row.get("building")),
            "unit": _clean_text(row.get("unit")),
            "floor": _clean_text(row.get("floor")),
            "room": _clean_text(row.get("room")),
            "part_path": _clean_text(row.get("part_path")),
        }.items()
        if value not in (None, "")
    }
    return AddressCandidate(
        source="standard",
        candidate_id=candidate_id,
        name=name,
        full_address=full_address,
        city=_clean_text(row.get("city")),
        district=_clean_text(row.get("county")),
        town=_clean_text(row.get("town")),
        score=0.0,
        evidence=f"{provider} standard-address table",
        metadata=metadata,
    )


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Unsafe standard-address identifier: {value}")
    return value


_ROAD_RE = re.compile(
    r"(?P<road>[\u4e00-\u9fffa-zA-Z0-9]{2,40}(?:大道|公路|快速路|路|街|道|巷|弄))"
    r"(?P<road_no>[0-9]{1,8}(?:号|號)?)?"
)
_ADMIN_SUFFIXES = ("自治州", "地区", "街道", "省", "市", "区", "县", "旗", "镇", "乡")
_LATIN_STORE_RE = re.compile(r"[A-Za-z][A-Za-z0-9&+.'-]*$")


def _extract_search_parts(query: str) -> StandardSearchParts:
    compact = _compact_text(query_without_admin_scope(query))
    if not compact:
        return StandardSearchParts()

    road_match = None
    for match in _ROAD_RE.finditer(compact):
        road = _trim_location_prefix(match.group("road"))
        if len(road) >= 2:
            road_match = (match, road)

    if road_match:
        match, road = road_match
        road_no = _normalize_road_no(match.group("road_no"))
        tail = compact[match.end() :]
        name = _clean_name_hint(tail)
        return StandardSearchParts(road=road, road_no=road_no, name=name)

    return StandardSearchParts(name=_clean_name_hint(compact))


def _structured_where_clauses(parts: StandardSearchParts) -> list[str]:
    clauses: list[str] = []
    if parts.road and parts.road_no:
        clauses.append(_road_where_clause(parts.road))
        clauses.append(_road_no_where_clause(parts.road_no))
        return clauses
    if parts.road and parts.name:
        clauses.append(_road_where_clause(parts.road))
        clauses.append(_contains_any_clause(["poi", "community", "stand_address", "src_address", "xxdz"], [parts.name]))
        return clauses
    return []


def _full_text_where_clause(query: str) -> str:
    like_query = like_literal(query)
    predicates = [f"coalesce(`{column}`, '') like '{like_query}'" for column in STANDARD_SEARCH_COLUMNS]
    return f"({' OR '.join(predicates)})"


def _road_where_clause(road: str) -> str:
    literal = string_literal(road)
    like_value = like_literal(road)
    return (
        "("
        f"coalesce(`road`, '') = '{literal}' OR "
        f"coalesce(`road`, '') like '{like_value}' OR "
        f"coalesce(`stand_address`, '') like '{like_value}' OR "
        f"coalesce(`src_address`, '') like '{like_value}' OR "
        f"coalesce(`xxdz`, '') like '{like_value}'"
        ")"
    )


def _road_no_where_clause(road_no: str) -> str:
    terms = [road_no]
    digits = re.sub(r"\D", "", road_no)
    if digits and digits != road_no:
        terms.append(digits)
    return _contains_any_clause(["road_no", "subroad_no", "stand_address", "src_address", "xxdz", "part_path"], terms)


def _contains_any_clause(columns: list[str], terms: list[str]) -> str:
    predicates: list[str] = []
    for term in terms:
        if not term:
            continue
        literal = like_literal(term)
        predicates.extend(f"coalesce(`{column}`, '') like '{literal}'" for column in columns)
    return f"({' OR '.join(predicates)})" if predicates else "(1 = 1)"


def _trim_location_prefix(value: str) -> str:
    text = value
    for suffix in _ADMIN_SUFFIXES:
        index = text.rfind(suffix)
        if 0 <= index < len(text) - len(suffix):
            text = text[index + len(suffix) :]
    return text


def _normalize_road_no(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("號", "号")
    if text.isdigit():
        return f"{text}号"
    return text


def _clean_name_hint(value: str | None) -> str | None:
    text = _compact_text(value)
    if not text:
        return None
    text = _LATIN_STORE_RE.sub("", text)
    text = re.sub(r"(放前台|放门口|送前台|送门口)$", "", text)
    text = text.strip("-_/\\|:：#")
    return text if len(text) >= 4 else None


def _compact_text(value: str | None) -> str:
    return re.sub(r"[\s,，。;；]+", "", value or "")


def _clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def string_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def like_literal(value: str) -> str:
    escaped = string_literal(value)
    return f"%{escaped}%"
