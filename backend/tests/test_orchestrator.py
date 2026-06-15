from __future__ import annotations

import asyncio

from app.agent.orchestrator import AddressAgent
from app.config import Settings
from app.schemas import AddressCandidate


class FakeDb:
    def __init__(self) -> None:
        self.last_city: str | None = None
        self.last_district: str | None = None

    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(self, query: str, city: str | None, district: str | None, limit: int) -> list[AddressCandidate]:
        self.last_city = city
        self.last_district = district
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

    async def search(self, query: str, city: str | None, district: str | None, limit: int) -> list[AddressCandidate]:
        raise RuntimeError("mock hive down")


class WeakStandardDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(self, query: str, city: str | None, district: str | None, limit: int) -> list[AddressCandidate]:
        return []


class WeakStandardHive:
    enabled = True

    async def search(self, query: str, city: str | None, district: str | None, limit: int) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="standard",
                candidate_id="HIVE-weak",
                name="光明路北小区",
                full_address="新疆维吾尔自治区乌鲁木齐市天山区光明路北小区18栋-1单元-2楼-8号",
                city="乌鲁木齐市",
                district="天山区",
                score=0,
                metadata={"provider": "hive"},
            )
        ]


class FakeQwen:
    async def repair_cleaned_address(self, raw: str, cleaned: str) -> None:
        return None


class FakeMgeo:
    enabled = False


def test_hive_failure_degrades_to_other_candidates() -> None:
    db = FakeDb()
    agent = AddressAgent(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
        ),
        db,
        FakeQwen(),
        FakeHive(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("乌鲁木齐市沙依巴克区友好北路689美美友好购物中心H&M", use_qwen=False))

    assert result.source == "poi"
    assert db.last_city == "乌鲁木齐市"
    assert db.last_district == "沙依巴克区"
    assert any("标准地址库查询失败" in warning for warning in result.warnings)


def test_recall_scope_normalizes_common_district_aliases() -> None:
    db = FakeDb()
    agent = AddressAgent(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
            default_city="乌鲁木齐市",
        ),
        db,
        FakeQwen(),
        FakeHive(),
        FakeMgeo(),
    )

    asyncio.run(agent.normalize_one("沙区友好北路689号美美友好购物中心H&M，放前台", use_qwen=False))

    assert db.last_city == "乌鲁木齐市"
    assert db.last_district == "沙依巴克区"


def test_weak_standard_candidate_rejects_without_qwen_confirmation() -> None:
    agent = AddressAgent(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
        ),
        WeakStandardDb(),
        FakeQwen(),
        WeakStandardHive(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("光明路北北", use_qwen=False))

    assert result.source == "none"
    assert result.anchor_type == "unmatched"
    assert any("候选锚点证据不足" in warning for warning in result.warnings)
