from __future__ import annotations

import asyncio

from app.agent.orchestrator import (
    AddressAgent,
    _build_recall_queries,
    _extract_recall_scope,
    _merge_input_detail,
    _selected_result,
    _unique_city_poi_allows_output,
)
from app.config import Settings
from app.schemas import AddressCandidate


class FakeDb:
    def __init__(self) -> None:
        self.last_province: str | None = None
        self.last_city: str | None = None
        self.last_district: str | None = None

    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        self.last_province = province
        self.last_city = city
        self.last_district = district
        return [
            AddressCandidate(
                source="poi",
                candidate_id="POI-1",
                name="美美友好购物中心H&M",
                full_address="新疆维吾尔自治区乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
                province="新疆维吾尔自治区",
                city="乌鲁木齐市",
                district="沙依巴克区",
                score=0.78,
                metadata={"provider": "fixture"},
            )
        ]


class FakeHive:
    enabled = True

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        raise RuntimeError("mock hive down")


class WeakStandardDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return []


class WeakStandardHive:
    enabled = True

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="standard",
                candidate_id="HIVE-weak",
                name="光明路北小区",
                full_address="新疆维吾尔自治区乌鲁木齐市天山区光明路北小区18栋-1单元-2楼-8号",
                province="新疆维吾尔自治区",
                city="乌鲁木齐市",
                district="天山区",
                score=0,
                metadata={"provider": "hive"},
            )
        ]


class FakeQwen:
    async def repair_cleaned_address(self, raw: str, cleaned: str) -> None:
        return None


class SelectingQwen:
    async def repair_cleaned_address(self, raw: str, cleaned: str) -> None:
        return None

    async def choose_candidate(self, raw: str, cleaned: str, candidates: list[AddressCandidate], mgeo_payload=None) -> dict:
        return {"selected_index": 0, "confidence": 0.92, "match_level": "standard", "reason": "pick top"}


class StructuredAnchorQwen:
    async def repair_cleaned_address(self, raw: str, cleaned: str) -> None:
        return None

    async def choose_candidate(self, raw: str, cleaned: str, candidates: list[AddressCandidate], mgeo_payload=None) -> dict:
        return {
            "selected_index": 0,
            "confidence": 0.85,
            "match_level": "poi",
            "reason": "省份、道路和POI均匹配",
            "normalized_address": candidates[0].full_address,
        }


class FakeMgeo:
    enabled = False


class StructuredMgeo:
    enabled = True

    async def parse(self, cleaned: str) -> dict:
        return {
            "components": {
                "prov": ["江苏"],
                "road": ["长江中公路"],
                "poi": ["华府写字楼"],
            }
        }


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
    assert db.last_province == "新疆维吾尔自治区"
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

    assert db.last_province == "新疆维吾尔自治区"
    assert db.last_city == "乌鲁木齐市"
    assert db.last_district == "沙依巴克区"


def test_recall_scope_extracts_city_and_district_after_province() -> None:
    scope = _extract_recall_scope("河北省石家庄市长安区友好镇科技东公路9009号银泰小区")

    assert scope.province == "河北省"
    assert scope.city == "石家庄市"
    assert scope.district == "长安区"


def test_recall_scope_extracts_city_without_city_suffix() -> None:
    suzhou_scope = _extract_recall_scope("苏州华府写字楼")
    nanjing_scope = _extract_recall_scope("南京华府写字楼")

    assert suzhou_scope.province == "江苏省"
    assert suzhou_scope.city == "苏州市"
    assert suzhou_scope.district is None
    assert nanjing_scope.province == "江苏省"
    assert nanjing_scope.city == "南京市"
    assert nanjing_scope.district is None


def test_recall_queries_strip_admin_scope_keywords() -> None:
    queries = _build_recall_queries("南京华府写字楼", "南京华府写字楼")

    assert "南京华府写字楼" in queries
    assert "华府写字楼" in queries


def test_recall_scope_does_not_treat_community_as_district() -> None:
    scope = _extract_recall_scope("光明路北小区18栋1单元2层8号")

    assert scope.city is None
    assert scope.district is None


def test_merge_input_detail_does_not_duplicate_existing_full_detail() -> None:
    base_address = "河北省石家庄市长安区友好镇科技东公路9009号银泰小区1栋-6单元-10楼-2813室"
    detail = {
        "building": "1栋",
        "unit": "6单元",
        "floor": "10楼",
        "room": "2813室",
        "address_detail": "1栋-6单元-10楼-2813室",
    }

    assert _merge_input_detail(base_address, detail) == base_address


def test_selected_result_strips_candidate_detail_not_present_in_input() -> None:
    candidate = AddressCandidate(
        source="standard",
        candidate_id="SYN-test100k-000888",
        name="华府写字楼",
        full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
        city="南京市",
        district="玄武区",
        town="金桥街道",
        score=0.99,
        metadata={"building": "41栋", "unit": "6单元", "floor": "6楼", "room": "0807室"},
    )

    result = _selected_result(
        "江苏省南京市光明中路5981华府写字楼",
        "江苏省南京市光明中路5981华府写字楼",
        candidate,
        [candidate],
        [],
        None,
        None,
    )

    assert result.normalized_address == "江苏省南京市玄武区金桥街道光明中路5981号华府写字楼"
    assert "候选包含输入未覆盖的楼栋/楼层/单元/房号，已截断到地址锚点" in result.warnings
    assert "building" not in result.components


def test_selected_result_keeps_only_input_detail_when_candidate_is_more_specific() -> None:
    candidate = AddressCandidate(
        source="standard",
        candidate_id="SYN-test100k-000888",
        name="华府写字楼",
        full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
        city="南京市",
        district="玄武区",
        town="金桥街道",
        score=0.99,
        metadata={"building": "41栋", "unit": "6单元", "floor": "6楼", "room": "0807室"},
    )

    result = _selected_result(
        "江苏省南京市光明中路5981华府写字楼41栋",
        "江苏省南京市光明中路5981华府写字楼41栋",
        candidate,
        [candidate],
        [],
        None,
        None,
    )

    assert result.normalized_address == "江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋"
    assert result.components["building"] == "41栋"
    assert "unit" not in result.components


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


def test_weak_standard_candidate_still_rejects_even_if_qwen_selects_it() -> None:
    agent = AddressAgent(
        Settings(
            hive_enabled=True,
            hive_host="hive",
            hive_table="ysk_datahub_address_standed",
            qwen_base_url="http://qwen.test",
            qwen_model="qwen-test",
            recall_scope_mode="auto",
        ),
        WeakStandardDb(),
        SelectingQwen(),
        WeakStandardHive(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("华府写字楼", use_qwen=True))

    assert result.source == "none"
    assert result.anchor_type == "unmatched"
    assert any("候选锚点证据不足" in warning for warning in result.warnings)


class ProvinceScopeDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return []


class ProvinceScopeStandard:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None, str | None, int]] = []

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        self.calls.append((query, province, city, district, limit))
        return []


def test_province_scope_is_forwarded_to_standard_search() -> None:
    standard = ProvinceScopeStandard()
    agent = AddressAgent(
        Settings(
            standard_address_source="doris",
            doris_enabled=True,
            doris_host="doris",
            doris_database="address_normalizer",
            doris_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
        ),
        ProvinceScopeDb(),
        FakeQwen(),
        standard,
        FakeMgeo(),
    )

    asyncio.run(agent.normalize_one("江苏华府写字楼", use_qwen=False))

    assert standard.calls
    assert any(call[1] == "江苏省" for call in standard.calls)


class StructuredAnchorDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return []


class StructuredAnchorStandard:
    enabled = True

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="standard",
                candidate_id="SYN-test100k-021862",
                name="华府写字楼",
                full_address="江苏省苏州市姑苏区金桥乡长江中公路8670号华府写字楼8栋-3单元-1楼-1904室",
                province="江苏省",
                city="苏州市",
                district="姑苏区",
                town="金桥乡",
                score=0.0,
                metadata={
                    "provider": "doris",
                    "road": "长江中公路",
                    "road_no": "8670号",
                    "poi": "华府写字楼",
                    "building": "8栋",
                    "unit": "3单元",
                    "floor": "1楼",
                    "room": "1904室",
                },
            )
        ]


def test_qwen_can_release_structured_anchor_without_fast_path() -> None:
    agent = AddressAgent(
        Settings(
            standard_address_source="doris",
            doris_enabled=True,
            doris_host="doris",
            doris_database="address_normalizer",
            doris_table="ysk_datahub_address_standed",
            qwen_base_url="http://qwen.test",
            qwen_model="qwen-test",
            recall_scope_mode="auto",
        ),
        StructuredAnchorDb(),
        StructuredAnchorQwen(),
        StructuredAnchorStandard(),
        StructuredMgeo(),
    )

    result = asyncio.run(agent.normalize_one("江苏长江中公路华府写字楼", use_qwen=True))

    assert result.source == "standard"
    assert result.anchor_type == "standard"
    assert result.normalized_address == "江苏省苏州市姑苏区金桥乡长江中公路8670号华府写字楼"
    assert any("Qwen确认省份+道路+POI结构化锚点" in warning for warning in result.warnings)


class UniqueCityPoiDb:
    def search_memory(self, query: str, limit: int) -> list[AddressCandidate]:
        return []

    def search_poi(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return []


class UniqueCityPoiStandard:
    enabled = True

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="standard",
                candidate_id="S-NJ-1",
                name="华府写字楼",
                full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
                province="江苏省",
                city="南京市",
                district="玄武区",
                town="金桥街道",
                score=0.0,
                metadata={
                    "provider": "doris",
                    "poi": "华府写字楼",
                    "building": "41栋",
                    "unit": "6单元",
                    "floor": "6楼",
                    "room": "0807室",
                },
            )
        ]


class MultiProvincePoiStandard:
    enabled = True

    async def search(
        self,
        query: str,
        province: str | None,
        city: str | None,
        district: str | None,
        limit: int,
    ) -> list[AddressCandidate]:
        return [
            AddressCandidate(
                source="standard",
                candidate_id="S-JS-1",
                name="华府写字楼",
                full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
                province="江苏省",
                city="南京市",
                district="玄武区",
                town="金桥街道",
                score=0.0,
                metadata={"provider": "doris", "poi": "华府写字楼"},
            ),
            AddressCandidate(
                source="standard",
                candidate_id="S-JS-2",
                name="华府写字楼",
                full_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼28栋-6单元-2楼-1924室",
                province="江苏省",
                city="苏州市",
                district="吴中区",
                town="南湖镇",
                score=0.0,
                metadata={"provider": "doris", "poi": "华府写字楼"},
            ),
        ]


def test_unique_city_poi_can_output_anchor_without_qwen() -> None:
    agent = AddressAgent(
        Settings(
            standard_address_source="doris",
            doris_enabled=True,
            doris_host="doris",
            doris_database="address_normalizer",
            doris_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
        ),
        UniqueCityPoiDb(),
        FakeQwen(),
        UniqueCityPoiStandard(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("南京华府写字楼", use_qwen=False))

    assert result.source == "standard"
    assert result.anchor_type == "standard"
    assert result.normalized_address == "江苏省南京市玄武区金桥街道光明中路5981号华府写字楼"


def test_unique_city_poi_release_rule_accepts_unique_city_exact_poi() -> None:
    candidate = AddressCandidate(
        source="standard",
        candidate_id="S-NJ-1",
        name="华府写字楼",
        full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
        province="江苏省",
        city="南京市",
        district="玄武区",
        town="金桥街道",
        score=0.82,
        metadata={"provider": "doris", "poi": "华府写字楼", "score_features": {"conflicts": []}},
    )

    assert _unique_city_poi_allows_output("南京华府写字楼", candidate, [candidate]) is True


def test_province_poi_only_still_rejects_when_not_unique() -> None:
    agent = AddressAgent(
        Settings(
            standard_address_source="doris",
            doris_enabled=True,
            doris_host="doris",
            doris_database="address_normalizer",
            doris_table="ysk_datahub_address_standed",
            qwen_base_url=None,
            recall_scope_mode="auto",
        ),
        UniqueCityPoiDb(),
        FakeQwen(),
        MultiProvincePoiStandard(),
        FakeMgeo(),
    )

    result = asyncio.run(agent.normalize_one("江苏华府写字楼", use_qwen=False))

    assert result.source == "none"
    assert result.anchor_type == "unmatched"
