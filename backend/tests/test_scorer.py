from __future__ import annotations

from app.agent.orchestrator import _fast_path_candidate
from app.agent.scorer import has_strong_anchor_evidence, rank_candidates, score_candidate
from app.config import Settings
from app.schemas import AddressCandidate


def _settings() -> Settings:
    return Settings(memory_fast_path_score=0.94, standard_fast_path_score=0.96, fast_path_score=0.95)


def _candidate(source: str = "standard", metadata: dict | None = None) -> AddressCandidate:
    return AddressCandidate(
        source=source,
        candidate_id="C-1",
        name="光明路北小区",
        full_address="新疆维吾尔自治区乌鲁木齐市天山区光明路北小区18栋-1单元-2楼-8号",
        city="乌鲁木齐市",
        district="天山区",
        score=0,
        metadata=metadata or {},
    )


def test_short_fuzzy_input_does_not_get_high_confidence() -> None:
    standard = score_candidate("光明路北北", "光明路北北", _candidate("standard"))
    memory = score_candidate("光明路北北", "光明路北北", _candidate("memory", {"matched_alias": "光明路北小区"}))

    assert standard.score < _settings().standard_fast_path_score
    assert memory.score < _settings().memory_fast_path_score
    assert has_strong_anchor_evidence(standard) is False
    assert has_strong_anchor_evidence(memory) is False


def test_complete_anchor_with_context_and_detail_can_score_high() -> None:
    anchor = score_candidate("乌鲁木齐市光明路北小区", "乌鲁木齐市光明路北小区", _candidate("standard"))
    detail = score_candidate(
        "光明路北小区18栋1单元2层8号",
        "光明路北小区18栋1单元2层8号",
        _candidate("standard", {"building": "18栋", "unit": "1单元", "floor": "2楼", "room": "8号"}),
    )
    exact_alias = score_candidate(
        "光明路北小区",
        "光明路北小区",
        _candidate("memory", {"matched_alias": "光明路北小区"}),
    )

    assert anchor.score >= _settings().standard_fast_path_score
    assert detail.score >= _settings().standard_fast_path_score
    assert exact_alias.score >= _settings().memory_fast_path_score
    assert has_strong_anchor_evidence(anchor) is True
    assert has_strong_anchor_evidence(detail) is True
    assert has_strong_anchor_evidence(exact_alias) is True


def test_name_only_standard_and_auto_memory_do_not_fast_path() -> None:
    standard = score_candidate(
        "华府写字楼",
        "华府写字楼",
        AddressCandidate(
            source="standard",
            candidate_id="S-1",
            name="华府写字楼",
            full_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
            city="苏州市",
            district="吴中区",
            score=0.88,
        ),
    )
    auto_memory = score_candidate(
        "华府写字楼",
        "华府写字楼",
        AddressCandidate(
            source="memory",
            candidate_id="M-1",
            name="华府写字楼",
            full_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
            city="苏州市",
            district="吴中区",
            score=0.96,
            metadata={"matched_alias": "华府写字楼", "alias_kind": "observed", "confirmed_by": "auto"},
        ),
    )

    assert standard.score < _settings().standard_fast_path_score
    assert auto_memory.score < _settings().memory_fast_path_score
    assert has_strong_anchor_evidence(standard) is False
    assert has_strong_anchor_evidence(auto_memory) is False
    assert _fast_path_candidate([standard], _settings()) is None
    assert _fast_path_candidate([auto_memory], _settings()) is None


def test_city_prefix_makes_name_match_contextual() -> None:
    suzhou = score_candidate(
        "苏州华府写字楼",
        "苏州华府写字楼",
        AddressCandidate(
            source="standard",
            candidate_id="S-SZ",
            name="华府写字楼",
            full_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼",
            city="苏州市",
            district="吴中区",
            score=0,
        ),
    )
    nanjing = score_candidate(
        "南京华府写字楼",
        "南京华府写字楼",
        AddressCandidate(
            source="standard",
            candidate_id="S-NJ",
            name="华府写字楼",
            full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
            city="南京市",
            district="玄武区",
            score=0,
        ),
    )

    assert has_strong_anchor_evidence(suzhou) is True
    assert has_strong_anchor_evidence(nanjing) is True
    assert suzhou.score >= _settings().standard_fast_path_score
    assert nanjing.score >= _settings().standard_fast_path_score


def test_standard_source_alone_does_not_fast_path() -> None:
    candidate = score_candidate("光明路北北", "光明路北北", _candidate("standard"))

    assert _fast_path_candidate([candidate], _settings()) is None


def test_fast_path_requires_strong_anchor_for_memory_and_standard() -> None:
    weak_memory = _candidate("memory")
    weak_memory.score = 0.99
    weak_memory.metadata["strong_anchor_evidence"] = False
    strong_memory = score_candidate(
        "光明路北小区",
        "光明路北小区",
        _candidate("memory", {"matched_alias": "光明路北小区"}),
    )
    strong_standard = score_candidate(
        "光明路北小区18栋1单元2层8号",
        "光明路北小区18栋1单元2层8号",
        _candidate("standard", {"building": "18栋", "unit": "1单元", "floor": "2楼", "room": "8号"}),
    )

    assert _fast_path_candidate([weak_memory], _settings()) is None
    assert _fast_path_candidate([strong_memory], _settings()) == strong_memory
    assert _fast_path_candidate([strong_standard], _settings()) == strong_standard


def test_name_only_memory_candidate_loses_to_matching_standard_with_city_and_road() -> None:
    raw = "江苏省南京市光明中路5981华府写字楼"
    memory = AddressCandidate(
        source="memory",
        candidate_id="M-1",
        name="华府写字楼",
        full_address="江苏省苏州市吴中区南湖镇迎宾中大道8721号华府写字楼28栋-6单元-2楼-1924室",
        province="江苏省",
        city="苏州市",
        district="吴中区",
        score=0.92,
        metadata={"matched_alias": "华府写字楼"},
    )
    standard = AddressCandidate(
        source="standard",
        candidate_id="S-1",
        name="华府写字楼",
        full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼41栋-6单元-6楼-0807室",
        province="江苏省",
        city="南京市",
        district="玄武区",
        score=0,
        metadata={"provider": "doris", "road": "光明中路", "road_no": "5981号"},
    )

    ranked = rank_candidates(raw, raw, [memory, standard], 2)

    assert ranked[0].candidate_id == "S-1"
    assert ranked[0].score >= _settings().standard_fast_path_score
    assert ranked[1].candidate_id == "M-1"
    assert ranked[1].score <= 0.68
    assert has_strong_anchor_evidence(ranked[1]) is False
    assert "city" in ranked[1].metadata["score_features"]["conflicts"]
    assert "road_no" in ranked[1].metadata["score_features"]["conflicts"]


def test_province_conflict_blocks_cross_province_candidates() -> None:
    raw = "江苏华府写字楼"
    jiangsu = AddressCandidate(
        source="standard",
        candidate_id="S-JS",
        name="华府写字楼",
        full_address="江苏省南京市玄武区金桥街道光明中路5981号华府写字楼",
        province="江苏省",
        city="南京市",
        district="玄武区",
        score=0,
    )
    guangdong = AddressCandidate(
        source="standard",
        candidate_id="S-GD",
        name="华府写字楼",
        full_address="广东省广州市天河区迎宾大道300号华府写字楼",
        province="广东省",
        city="广州市",
        district="天河区",
        score=0,
    )

    ranked = rank_candidates(raw, raw, [guangdong, jiangsu], 2)

    assert ranked[0].candidate_id == "S-JS"
    assert "province" not in ranked[0].metadata["score_features"]["conflicts"]
    assert ranked[1].candidate_id == "S-GD"
    assert "province" in ranked[1].metadata["score_features"]["conflicts"]
    assert ranked[1].score <= 0.68
