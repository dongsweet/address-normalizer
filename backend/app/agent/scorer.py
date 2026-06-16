from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.admin_scope import has_admin_scope, resolve_admin_hint
from app.schemas import AddressCandidate


SOURCE_BOOSTS = {
    "memory": 0.06,
    "standard": 0.04,
    "poi": 0.02,
}

WEAK_MATCH_CEILING = 0.82
CONFLICT_MATCH_CEILING = 0.68

_ROAD_WITH_NO_RE = re.compile(
    r"(?P<road>[\u4e00-\u9fffa-zA-Z0-9]{2,40}(?:大道|公路|快速路|路|街|道|巷|弄))"
    r"(?P<road_no>[0-9]{1,8}(?:号|號)?)"
)
_ROAD_RE = re.compile(r"[\u4e00-\u9fffa-zA-Z0-9]{2,40}(?:大道|公路|快速路|路|街|道|巷|弄)")
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
    has_context = _has_context_beyond_name(raw, cleaned, candidate)
    candidate_has_detail = _candidate_has_detail(candidate)
    conflicts = _candidate_conflicts(raw, cleaned, candidate)
    has_conflict = bool(conflicts)
    trusted_exact_alias = exact_alias and _is_trusted_exact_alias_match(query_key, candidate, has_context or has_detail)
    contextual_name_match = name_in_query and has_context
    contextual_address_match = query_in_address and has_context
    strong_anchor = (trusted_exact_alias or contextual_name_match or contextual_address_match) and not has_conflict

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
        "trusted_exact_alias": trusted_exact_alias,
        "name_in_query": name_in_query,
        "query_in_address": query_in_address,
        "has_context": has_context,
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


def _is_trusted_exact_alias_match(query_key: str, candidate: AddressCandidate, has_context: bool) -> bool:
    if candidate.source != "memory":
        return True
    confirmed_by = str(candidate.metadata.get("confirmed_by") or "").lower()
    alias_confirmed_by = str(candidate.metadata.get("alias_confirmed_by") or "").lower()
    alias_kind = str(candidate.metadata.get("alias_kind") or "").lower()
    if confirmed_by != "auto" and alias_confirmed_by != "auto":
        return True
    if alias_kind == "normalized":
        return True
    return has_context or _has_detail_signal(query_key)


def _has_detail_signal(query_key: str) -> bool:
    return bool(
        re.search(r"\d+(?:栋|号楼|单元|楼|层|室|房|号)", query_key)
        or re.search(r"\d+-\d+", query_key)
    )


def _has_context_beyond_name(raw: str, cleaned: str, candidate: AddressCandidate) -> bool:
    query_key = match_key(cleaned or raw)
    name_key = match_key(candidate.name)
    if not query_key:
        return False
    residual = query_key.replace(name_key, "", 1) if name_key else query_key
    if _has_detail_signal(residual):
        return True
    road, road_no = _extract_query_road_number(cleaned or raw)
    if road and road_no:
        return True
    if _ROAD_RE.search(residual) and has_admin_scope(cleaned or raw):
        return True
    return has_admin_scope(residual)


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
    province, city, district = _extract_query_scope(cleaned or raw)
    if province and candidate.province and relaxed_match_key(province) != relaxed_match_key(candidate.province):
        conflicts.append("province")
    if city and candidate.city and relaxed_match_key(city) != relaxed_match_key(candidate.city):
        conflicts.append("city")
    if district and candidate.district and relaxed_match_key(district) != relaxed_match_key(candidate.district):
        conflicts.append("district")

    road, road_no = _extract_query_road_number(cleaned or raw)
    if road and road_no and not _candidate_matches_road_number(candidate, road, road_no):
        conflicts.append("road_no")
    return conflicts


def _extract_query_scope(value: str) -> tuple[str | None, str | None, str | None]:
    hint = resolve_admin_hint(value)
    return hint.province, hint.city, hint.district


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
