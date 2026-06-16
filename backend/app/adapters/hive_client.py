from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.adapters.standard_address import build_standard_search_sql, map_standard_row
from app.config import Settings
from app.schemas import AddressCandidate

try:
    from impala.dbapi import connect as hive_connect
except ImportError:  # pragma: no cover - exercised in integration environments
    hive_connect = None

if TYPE_CHECKING:
    from app.db import Database


class HiveClient:
    provider = "hive"

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db

    @property
    def enabled(self) -> bool:
        return self.settings.hive_configured

    @property
    def table_name(self) -> str | None:
        return self.settings.hive_table if self.enabled else None

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

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None = None,
        limit: int = 8,
    ) -> list[AddressCandidate]:
        query = query.strip()
        if not self.enabled or not query:
            return []
        return await asyncio.to_thread(self._search_blocking, query, province, city, district, limit)

    def _search_blocking(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        if hive_connect is None:
            raise RuntimeError("Hive client dependency is missing: install impyla")

        fetch_limit = max(limit, min(self.settings.hive_fetch_limit, limit * 3))
        sql = self._build_search_sql(query=query, province=province, city=city, district=district, limit=limit)
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
            self._log_api_call(query=query, province=province, city=city, district=district, status="error", error_message=str(exc))
            raise

        mapped = [map_hive_row(dict(zip(columns, row, strict=False)), table=self.settings.hive_table) for row in rows]
        candidates = [candidate for candidate in mapped if candidate is not None]
        self._log_api_call(query=query, province=province, city=city, district=district, status="success", result_count=len(candidates))
        return candidates[:fetch_limit]

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

    def _build_search_sql(
        self,
        *,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> str:
        fetch_limit = max(limit, min(self.settings.hive_fetch_limit, limit * 3))
        return build_standard_search_sql(
            database=self.settings.hive_database,
            table=self.settings.hive_table,
            query=query,
            province=province,
            city=city,
            district=district,
            default_city=self._default_city_for_search(),
            fetch_limit=fetch_limit,
        )

    def _log_api_call(
        self,
        *,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        status: str,
        result_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if not self.db:
            return
        try:
            self.db.log_api_call(
                provider=self.provider,
                call_type="candidate_search",
                request_query=query,
                response_status=status,
                result_count=result_count,
                error_message=error_message,
                metadata={
                    "province": province,
                    "city": city or self._default_city_for_search(),
                    "district": district,
                    "table": self.settings.hive_table,
                },
            )
        except Exception:  # noqa: BLE001
            return

    def _default_city_for_search(self) -> str | None:
        return self.settings.default_city if self.settings.recall_scope_mode == "fixed" else None


def map_hive_row(row: dict[str, Any], *, table: str) -> AddressCandidate | None:
    return map_standard_row(row, table=table, provider="hive")
