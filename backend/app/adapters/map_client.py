from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from app.config import Settings
from app.schemas import AddressCandidate

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)


class MapClient:
    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db

    async def search_many(self, queries: list[str], city: str | None, limit: int = 8) -> list[AddressCandidate]:
        candidates: list[AddressCandidate] = []
        seen: set[tuple[str, str]] = set()
        per_query_limit = max(3, min(limit, 5))
        for query in _unique_queries(queries)[:3]:
            for candidate in await self.search(query, city, per_query_limit):
                key = (candidate.metadata.get("provider") or candidate.source, candidate.candidate_id)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
                if len(candidates) >= limit * 2:
                    return candidates
        return candidates

    async def search(self, query: str, city: str | None, limit: int = 5) -> list[AddressCandidate]:
        query = query.strip()
        if not self.settings.map_configured or not query:
            return []
        if self.settings.map_provider == "amap":
            if self._quota_exceeded("amap", self.settings.map_api_daily_quota, self.settings.map_api_monthly_quota):
                logger.warning("amap api quota exceeded, skipping place search")
                self._log_api_call(
                    provider="amap",
                    call_type="place_search",
                    request_query=query,
                    response_status="quota_exceeded",
                    metadata={"city": city or self.settings.default_city},
                )
                return []
            return await self._search_amap(query, city, limit)
        return []

    async def _search_amap(self, query: str, city: str | None, limit: int) -> list[AddressCandidate]:
        params = {
            "key": self.settings.amap_key,
            "keywords": query,
            "region": city or self.settings.default_city,
            "city_limit": "true",
            "page_size": str(min(limit, 10)),
        }
        http_status: int | None = None
        try:
            async with httpx.AsyncClient(timeout=self.settings.map_api_timeout_seconds) as client:
                response = await client.get("https://restapi.amap.com/v5/place/text", params=params)
                http_status = response.status_code
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            self._log_api_call(
                provider="amap",
                call_type="place_search",
                request_query=query,
                response_status="error",
                http_status=http_status,
                error_message=str(exc),
                metadata={"city": city or self.settings.default_city},
            )
            raise

        if str(payload.get("status", "1")) != "1":
            info = payload.get("info") or "unknown error"
            infocode = payload.get("infocode") or "unknown"
            self._log_api_call(
                provider="amap",
                call_type="place_search",
                request_query=query,
                response_status="error",
                http_status=http_status,
                error_message=f"{info} ({infocode})",
                metadata={"city": city or self.settings.default_city, "infocode": infocode},
            )
            raise RuntimeError(f"amap place search failed: {info} ({infocode})")

        pois = payload.get("pois") or []
        candidates: list[AddressCandidate] = []
        for item in pois[:limit]:
            location = (item.get("location") or "").split(",")
            lon = _to_float(location[0]) if len(location) == 2 else None
            lat = _to_float(location[1]) if len(location) == 2 else None
            address = item.get("address") or ""
            name = item.get("name") or ""
            full_address = "".join(
                part
                for part in [
                    item.get("pname"),
                    item.get("cityname"),
                    item.get("adname"),
                    address,
                    name if name not in address else "",
                ]
                if part and part != "[]"
            )
            candidates.append(
                AddressCandidate(
                    source="map_api",
                    candidate_id=item.get("id") or name or full_address,
                    name=name,
                    full_address=full_address or name,
                    province=item.get("pname"),
                    city=item.get("cityname"),
                    district=item.get("adname"),
                    category=item.get("type"),
                    lon=lon,
                    lat=lat,
                    score=0.62,
                    evidence="amap realtime place search (transient)",
                    metadata={
                        "provider": "amap",
                        "provider_poi_id": item.get("id"),
                        "adcode": item.get("adcode"),
                        "citycode": item.get("citycode"),
                        "typecode": item.get("typecode"),
                        "usage": "transient_candidate_only",
                    },
                )
            )
        first = candidates[0] if candidates else None
        self._log_api_call(
            provider="amap",
            call_type="place_search",
            request_query=query,
            response_status="success",
            http_status=http_status,
            lat=first.lat if first else None,
            lon=first.lon if first else None,
            result_count=len(pois),
            metadata={"city": city or self.settings.default_city},
        )
        return candidates

    def _quota_exceeded(self, provider: str, daily_quota: int | None, monthly_quota: int | None) -> bool:
        if not self.db or (daily_quota is None and monthly_quota is None):
            return False
        today = datetime.now(UTC).date()
        try:
            if (
                daily_quota is not None
                and self.db.get_api_call_count(provider, today.isoformat(), today.isoformat()) >= daily_quota
            ):
                return True
            month_start = today.replace(day=1)
            if (
                monthly_quota is not None
                and self.db.get_api_call_count(provider, month_start.isoformat(), today.isoformat()) >= monthly_quota
            ):
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _log_api_call(self, **kwargs: object) -> None:
        if not self.db:
            return
        try:
            self.db.log_api_call(**kwargs)
        except Exception:  # noqa: BLE001
            return


def _unique_queries(queries: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(query.strip().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
