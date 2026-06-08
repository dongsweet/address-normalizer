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
    strong_anchor = exact_alias or name_in_query or query_in_address

    if strong_anchor:
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
