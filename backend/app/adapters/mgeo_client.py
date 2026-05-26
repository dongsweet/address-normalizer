from __future__ import annotations

import httpx


class MGeoClient:
    def __init__(self, base_url: str | None, enabled: bool, timeout: float) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.enabled = enabled and bool(self.base_url)
        self.timeout = timeout

    async def parse(self, address: str) -> dict | None:
        if not self.enabled or not self.base_url:
            return None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/parse", json={"address": address})
            response.raise_for_status()
            return response.json()
