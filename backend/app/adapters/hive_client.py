from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.schemas import AddressCandidate

try:
    from impala.dbapi import connect as hive_connect
except ImportError:  # pragma: no cover - exercised in integration environments
    hive_connect = None

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)

HIVE_SEARCH_COLUMNS = (
    "stand_address",
    "src_address",
    "poi",
    "community",
    "road",
    "xxdz",
)


@dataclass(frozen=True)
class HiveSearchParts:
    road: str | None = None
    road_no: str | None = None
    name: str | None = None


class HiveClient:
    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db

    @property
    def enabled(self) -> bool:
        return self.settings.hive_configured

    def check_connection(self) -> bool:
        if not self.enabled or hive_connect is None:
            return False
        try:
            connection = hive_connect(**self._connection_kwargs())
            try:
                cursor = connection.cursor()
                cursor.execute("SHOW TABLES")
                cursor.fetchall()
            finally:
                connection.close()
        except Exception:  # noqa: BLE001
            return False
        return True

    async def search(self, query: str, city: str | None, district: str | None = None, limit: int = 8) -> list[AddressCandidate]:
        query = query.strip()
        if not self.enabled or not query:
            return []
        return await asyncio.to_thread(self._search_blocking, query, city, district, limit)

    def _search_blocking(self, query: str, city: str | None, district: str | None, limit: int) -> list[AddressCandidate]:
        if hive_connect is None:
            raise RuntimeError("Hive client dependency is missing: install impyla")

        sql = self._build_search_sql(query=query, city=city, district=district, limit=limit)
        http_status: int | None = None
        try:
            connection = hive_connect(**self._connection_kwargs())
            try:
                cursor = connection.cursor()
                cursor.execute(sql)
                columns = [column[0] for column in cursor.description or []]
                rows = cursor.fetchall()
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001
            self._log_api_call(
                provider="hive",
                call_type="candidate_search",
                request_query=query,
                response_status="error",
                http_status=http_status,
                error_message=str(exc),
                metadata={
                    "city": city or self.settings.default_city,
                    "district": district,
                    "table": self.settings.hive_table,
                },
            )
            raise

        mapped = [map_hive_row(dict(zip(columns, row, strict=False)), table=self.settings.hive_table) for row in rows]
        candidates = [candidate for candidate in mapped if candidate is not None]
        self._log_api_call(
            provider="hive",
            call_type="candidate_search",
            request_query=query,
            response_status="success",
            result_count=len(candidates),
            metadata={
                "city": city or self.settings.default_city,
                "district": district,
                "table": self.settings.hive_table,
            },
        )
        return candidates

    def _connection_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.settings.hive_host,
            "port": self.settings.hive_port,
            "database": self.settings.hive_database,
            "auth_mechanism": self.settings.hive_auth_mechanism,
            "timeout": self.settings.hive_query_timeout_seconds,
        }
        if self.settings.hive_username:
            kwargs["user"] = self.settings.hive_username
        if self.settings.hive_password:
            kwargs["password"] = self.settings.hive_password
        return kwargs

    def _build_search_sql(self, *, query: str, city: str | None, district: str | None, limit: int) -> str:
        database = _safe_identifier(self.settings.hive_database)
        table = _safe_identifier(self.settings.hive_table)
        parts = _extract_search_parts(query)
        where_clauses = _structured_where_clauses(parts)
        if not where_clauses:
            where_clauses = [_full_text_where_clause(query)]
        target_city = city or self.settings.default_city
        if target_city:
            city_literal = _string_literal(target_city)
            where_clauses.append("coalesce(`city`, '') = '{city}'".format(city=city_literal))
        if district:
            district_literal = _string_literal(district)
            where_clauses.append("coalesce(`county`, '') = '{district}'".format(district=district_literal))

        fetch_limit = max(limit, min(self.settings.hive_fetch_limit, limit * 3))
        select_columns = [
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
        select_sql = ", ".join(f"`{column}`" for column in select_columns)
        return f"""
            SELECT {select_sql}
            FROM `{database}`.`{table}`
            WHERE {' AND '.join(where_clauses)}
            LIMIT {fetch_limit}
        """

    def _log_api_call(self, **kwargs: object) -> None:
        if not self.db:
            return
        try:
            self.db.log_api_call(**kwargs)
        except Exception:  # noqa: BLE001
            return


def map_hive_row(row: dict[str, Any], *, table: str) -> AddressCandidate | None:
    candidate_id = _clean_text(row.get("jxkid")) or _clean_text(row.get("row_num_id"))
    full_address = _clean_text(row.get("stand_address"))
    if not candidate_id or not full_address:
        return None

    name = (
        _clean_text(row.get("poi"))
        or _clean_text(row.get("community"))
        or _clean_text(row.get("road"))
    )
    metadata = {
        key: value
        for key, value in {
            "provider": "hive",
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
        evidence="hive standard-address table",
        metadata=metadata,
    )


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Unsafe Hive identifier: {value}")
    return value


_ROAD_RE = re.compile(
    r"(?P<road>[\u4e00-\u9fffa-zA-Z0-9]{2,40}(?:大道|公路|快速路|路|街|道|巷|弄))"
    r"(?P<road_no>[0-9]{1,8}(?:号|號)?)?"
)
_ADMIN_SUFFIXES = ("自治州", "地区", "街道", "省", "市", "区", "县", "旗", "镇", "乡")
_LATIN_STORE_RE = re.compile(r"[A-Za-z][A-Za-z0-9&+.'-]*$")


def _extract_search_parts(query: str) -> HiveSearchParts:
    compact = _compact_text(query)
    if not compact:
        return HiveSearchParts()

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
        return HiveSearchParts(road=road, road_no=road_no, name=name)

    return HiveSearchParts(name=_clean_name_hint(compact))


def _structured_where_clauses(parts: HiveSearchParts) -> list[str]:
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
    like_query = _like_literal(query)
    predicates = [f"coalesce(`{column}`, '') like '{like_query}'" for column in HIVE_SEARCH_COLUMNS]
    return f"({' OR '.join(predicates)})"


def _road_where_clause(road: str) -> str:
    literal = _string_literal(road)
    like_literal = _like_literal(road)
    return (
        "("
        f"coalesce(`road`, '') = '{literal}' OR "
        f"coalesce(`road`, '') like '{like_literal}' OR "
        f"coalesce(`stand_address`, '') like '{like_literal}' OR "
        f"coalesce(`src_address`, '') like '{like_literal}' OR "
        f"coalesce(`xxdz`, '') like '{like_literal}'"
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
        literal = _like_literal(term)
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


def _string_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _like_literal(value: str) -> str:
    escaped = _string_literal(value)
    return f"%{escaped}%"
