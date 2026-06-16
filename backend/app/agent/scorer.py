from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.schemas import AddressCandidate


SOURCE_BOOSTS = {
    "memory": 0.06,
    "standard": 0.04,
    "poi": 0.02,
}

WEAK_MATCH_CEILING = 0.82
CONFLICT_MATCH_CEILING = 0.68

_PROVINCE_PREFIX_RE = re.compile(r"^(?P<province>[\u4e00-\u9fff]{2,20}(?:特别行政区|自治区|省))")
_CITY_RE = re.compile(r"(?P<city>[\u4e00-\u9fff]{2,20}?(?:自治州|地区|盟|市))")
_DISTRICT_RE = re.compile(r"(?P<district>[\u4e00-\u9fff]{1,20}?(?:区|县|旗|市))")
_ROAD_WITH_NO_RE = re.compile(
    r"(?P<road>[\u4e00-\u9fffa-zA-Z0-9]{2,40}(?:大道|公路|快速路|路|街|道|巷|弄))"
    r"(?P<road_no>[0-9]{1,8}(?:号|號)?)"
)
_ADMIN_SUFFIXES = ("自治州", "地区", "街道", "省", "市", "区", "县", "旗", "镇", "乡")


def clamp_score(value: float) -> float:
    return max(0.0, min(0.99, value))


def candidate_text(candidate: AddressCandidate) -> str:
    return " ".join(
        part
        for part in [
            candidate.name,
            candidate.full_address,
            candidate.province,
            candidate.city,
            candidate.district,
            candidate.town,
            candidate.category,
        ]
        if part
    )


def match_key(value: str | None) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffa-zA-Z0-9]+", value or "")).lower()


def relaxed_match_key(value: str | None) -> str:
    key = match_key(value)
    for suffix in ("维吾尔自治区", "自治区", "自治州", "地区", "省", "市", "区", "县"):
        key = key.replace(suffix, "")
    return key


def has_strong_anchor_evidence(candidate: AddressCandidate) -> bool:
    return bool(candidate.metadata.get("strong_anchor_evidence"))


def score_candidate(raw: str, cleaned: str, candidate: AddressCandidate) -> AddressCandidate:
    text = candidate_text(candidate)
    fuzzy = max(fuzz.WRatio(raw, text), fuzz.WRatio(cleaned, text)) / 100
    partial = fuzz.partial_ratio(cleaned, text) / 100
    db_score = candidate.score or 0
    source_boost = SOURCE_BOOSTS.get(candidate.source, 0)
    name_key = match_key(candidate.name)
    query_key = match_key(cleaned or raw)
    full_key = match_key(candidate.full_address)
    exact_alias = _is_exact_alias_match(query_key, candidate)
    name_in_query = bool(name_key and name_key in query_key)
    query_in_address = bool(query_key and len(query_key) >= 6 and query_key in full_key)
    has_detail = _has_detail_signal(query_key)
    candidate_has_detail = _candidate_has_detail(candidate)
    conflicts = _candidate_conflicts(raw, cleaned, candidate)
    has_conflict = bool(conflicts)
    strong_anchor = (exact_alias or name_in_query or query_in_address) and not has_conflict

    if has_conflict:
        weak_similarity = max(fuzzy * 0.75, partial * 0.5)
        score = min(weak_similarity + source_boost, CONFLICT_MATCH_CEILING)
    elif strong_anchor:
        anchor_score = 0.97 if exact_alias else 0.94
        if has_detail and candidate_has_detail:
            anchor_score = max(anchor_score, 0.96)
        name_boost = 0.02 if name_in_query else 0.0
        detail_boost = 0.02 if has_detail and candidate_has_detail else 0.0
        score = max(db_score, fuzzy * 0.9, partial * 0.85, anchor_score) + source_boost + name_boost + detail_boost
    else:
        weak_similarity = max(db_score, fuzzy * 0.9, partial * 0.62)
        score = min(weak_similarity + source_boost, WEAK_MATCH_CEILING)

    candidate.score = clamp_score(score)
    candidate.metadata["strong_anchor_evidence"] = strong_anchor
    candidate.metadata["score_features"] = {
        "fuzzy": round(fuzzy, 4),
        "partial": round(partial, 4),
        "db_score": round(db_score, 4),
        "exact_alias": exact_alias,
        "name_in_query": name_in_query,
        "query_in_address": query_in_address,
        "has_detail": has_detail,
        "candidate_has_detail": candidate_has_detail,
        "conflicts": conflicts,
    }
    return candidate


def rank_candidates(raw: str, cleaned: str, candidates: list[AddressCandidate], limit: int) -> list[AddressCandidate]:
    ranked = [score_candidate(raw, cleaned, candidate) for candidate in candidates]
    ranked.sort(key=lambda item: (item.score, SOURCE_BOOSTS.get(item.source, 0)), reverse=True)
    return ranked[:limit]


def _is_exact_alias_match(query_key: str, candidate: AddressCandidate) -> bool:
    alias = candidate.metadata.get("matched_alias")
    alias_key = match_key(str(alias or ""))
    return bool(query_key and alias_key and query_key == alias_key)


def _has_detail_signal(query_key: str) -> bool:
    return bool(
        re.search(r"\d+(?:栋|号楼|单元|楼|层|室|房|号)", query_key)
        or re.search(r"\d+-\d+", query_key)
    )


def _candidate_has_detail(candidate: AddressCandidate) -> bool:
    metadata = candidate.metadata or {}
    detail_values = [
        metadata.get("building"),
        metadata.get("unit"),
        metadata.get("floor"),
        metadata.get("room"),
        metadata.get("address_detail"),
        metadata.get("xxdz"),
    ]
    if any(value for value in detail_values):
        return True
    return _has_detail_signal(match_key(candidate.full_address))


def _candidate_conflicts(raw: str, cleaned: str, candidate: AddressCandidate) -> list[str]:
    conflicts: list[str] = []
    city, district = _extract_query_scope(cleaned or raw)
    if city and candidate.city and relaxed_match_key(city) != relaxed_match_key(candidate.city):
        conflicts.append("city")
    if district and candidate.district and relaxed_match_key(district) != relaxed_match_key(candidate.district):
        conflicts.append("district")

    road, road_no = _extract_query_road_number(cleaned or raw)
    if road and road_no and not _candidate_matches_road_number(candidate, road, road_no):
        conflicts.append("road_no")
    return conflicts


def _extract_query_scope(value: str) -> tuple[str | None, str | None]:
    scoped_value = _strip_province_prefix(value)
    city_match = _CITY_RE.search(scoped_value)
    city = city_match.group("city") if city_match else None
    district = _extract_district_after_city(scoped_value, city_match)
    return city, district


def _strip_province_prefix(value: str) -> str:
    match = _PROVINCE_PREFIX_RE.match(value)
    if not match:
        return value
    return value[match.end() :]


def _extract_district_after_city(value: str, city_match: re.Match[str] | None) -> str | None:
    search_value = value[city_match.end() :] if city_match else value
    for match in _DISTRICT_RE.finditer(search_value):
        district = match.group("district")
        if district.endswith(("小区", "园区", "校区")):
            continue
        return district
    return None


def _extract_query_road_number(value: str) -> tuple[str | None, str | None]:
    compact = match_key(value)
    if not compact:
        return None, None
    matches = list(_ROAD_WITH_NO_RE.finditer(compact))
    if not matches:
        return None, None
    match = matches[-1]
    road = _trim_location_prefix(match.group("road"))
    road_no = _normalize_road_no(match.group("road_no"))
    return (road or None), road_no


def _candidate_matches_road_number(candidate: AddressCandidate, road: str, road_no: str) -> bool:
    candidate_text_key = match_key(
        " ".join(
            str(value)
            for value in [
                candidate.full_address,
                candidate.metadata.get("src_address"),
                candidate.metadata.get("xxdz"),
                candidate.metadata.get("part_path"),
            ]
            if value
        )
    )
    candidate_road = match_key(str(candidate.metadata.get("road") or ""))
    road_key = match_key(road)
    road_matches = bool(road_key and (road_key in candidate_text_key or road_key == candidate_road))

    road_no_digits = re.sub(r"\D", "", road_no)
    candidate_road_no_digits = re.sub(r"\D", "", str(candidate.metadata.get("road_no") or ""))
    road_no_matches = bool(
        road_no_digits
        and (
            road_no_digits in candidate_text_key
            or (candidate_road_no_digits and road_no_digits == candidate_road_no_digits)
        )
    )
    return road_matches and road_no_matches


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
