from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.adapters.standard_address import build_standard_search_sql, map_standard_row
from app.config import Settings
from app.schemas import AddressCandidate

try:
    import pymysql
except ImportError:  # pragma: no cover - exercised in integration environments
    pymysql = None

if TYPE_CHECKING:
    from app.db import Database


class DorisClient:
    provider = "doris"

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db

    @property
    def enabled(self) -> bool:
        return self.settings.doris_configured

    @property
    def table_name(self) -> str | None:
        return self.settings.doris_table if self.enabled else None

    def check_connection(self) -> bool:
        if not self.enabled or pymysql is None:
            return False
        try:
            connection = pymysql.connect(**self._connection_kwargs())
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
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
        if pymysql is None:
            raise RuntimeError("Doris client dependency is missing: install PyMySQL")

        sql = self._build_search_sql(query=query, city=city, district=district, limit=limit)
        try:
            connection = pymysql.connect(**self._connection_kwargs())
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    columns = [column[0] for column in cursor.description or []]
                    rows = cursor.fetchall()
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001
            self._log_api_call(query=query, city=city, district=district, status="error", error_message=str(exc))
            raise

        mapped = [map_doris_row(dict(zip(columns, row, strict=False)), table=self.settings.doris_table) for row in rows]
        candidates = [candidate for candidate in mapped if candidate is not None]
        self._log_api_call(query=query, city=city, district=district, status="success", result_count=len(candidates))
        return candidates

    def _connection_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.settings.doris_host,
            "port": self.settings.doris_port,
            "user": self.settings.doris_username,
            "password": self.settings.doris_password or "",
            "database": self.settings.doris_database,
            "charset": "utf8mb4",
            "connect_timeout": int(self.settings.doris_query_timeout_seconds),
            "read_timeout": int(self.settings.doris_query_timeout_seconds),
            "write_timeout": int(self.settings.doris_query_timeout_seconds),
        }

    def _build_search_sql(self, *, query: str, city: str | None, district: str | None, limit: int) -> str:
        fetch_limit = max(limit, min(self.settings.doris_fetch_limit, limit * 3))
        return build_standard_search_sql(
            database=self.settings.doris_database,
            table=self.settings.doris_table,
            query=query,
            city=city,
            district=district,
            default_city=self.settings.default_city,
            fetch_limit=fetch_limit,
        )

    def _log_api_call(
        self,
        *,
        query: str,
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
                    "city": city or self.settings.default_city,
                    "district": district,
                    "table": self.settings.doris_table,
                },
            )
        except Exception:  # noqa: BLE001
            return


def map_doris_row(row: dict[str, Any], *, table: str) -> AddressCandidate | None:
    return map_standard_row(row, table=table, provider="doris")
