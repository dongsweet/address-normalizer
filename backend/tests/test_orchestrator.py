from __future__ import annotations

import asyncio

from app.agent.orchestrator import AddressAgent
from app.config import Settings
from app.schemas import AddressCandidate


class FakeDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(self, query: str, city: str | None, limit: int) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="poi",
                candidate_id="POI-1",
                name="美美友好购物中心H&M",
                full_address="新疆维吾尔自治区乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
                city="乌鲁木齐市",
                district="沙依巴克区",
                score=0.78,
                metadata={"provider": "fixture"},
            )
        ]


class FakeHive:
    enabled = True

    async def search(self, query: str, city: str | None, limit: int) -> list[AddressCandidate]:
        raise RuntimeError("mock hive down")


class FakeQwen:
    async def repair_cleaned_address(self, raw: str, cleaned: str) -> None:
        return None


class FakeMgeo:
    enabled = False


def test_hive_failure_degrades_to_other_candidates() -> None:
    agent = AddressAgent(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_table="ysk_datahub_address_standed",
            qwen_base_url=None,
        ),
        FakeDb(),
        FakeQwen(),
        FakeHive(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("友好北路689美美友好购物中心H&M", use_qwen=False))

    assert result.source == "poi"
    assert any("标准地址库查询失败" in warning for warning in result.warnings)
