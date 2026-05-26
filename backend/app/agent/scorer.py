from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.schemas import AddressCandidate


SOURCE_BOOSTS = {
    "memory": 0.16,
    "standard": 0.18,
    "poi": 0.04,
    "map_api": 0.06,
}


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


def score_candidate(raw: str, cleaned: str, candidate: AddressCandidate) -> AddressCandidate:
    text = candidate_text(candidate)
    fuzzy = max(fuzz.WRatio(raw, text), fuzz.WRatio(cleaned, text), fuzz.partial_ratio(cleaned, text)) / 100
    db_score = candidate.score or 0
    source_boost = SOURCE_BOOSTS.get(candidate.source, 0)
    name_key = match_key(candidate.name)
    query_key = match_key(f"{raw}{cleaned}")
    relaxed_name_key = relaxed_match_key(candidate.name)
    relaxed_query_key = relaxed_match_key(f"{raw}{cleaned}")
    name_boost = 0.0
    if name_key and name_key in query_key:
        name_boost = 0.18
    if relaxed_name_key and relaxed_name_key in relaxed_query_key:
        name_boost = max(name_boost, 0.28)
    candidate.score = clamp_score(max(db_score, fuzzy * 0.92) + source_boost + name_boost)
    if candidate.source == "map_api" and relaxed_name_key and relaxed_name_key in relaxed_query_key:
        candidate.score = max(candidate.score, 0.94)
    return candidate


def rank_candidates(raw: str, cleaned: str, candidates: list[AddressCandidate], limit: int) -> list[AddressCandidate]:
    ranked = [score_candidate(raw, cleaned, candidate) for candidate in candidates]
    ranked.sort(key=lambda item: (item.score, SOURCE_BOOSTS.get(item.source, 0)), reverse=True)
    return ranked[:limit]
