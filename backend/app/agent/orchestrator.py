from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.adapters.map_client import MapClient
from app.adapters.mgeo_client import MGeoClient
from app.adapters.qwen_client import QwenClient
from app.agent.cleaner import clean_address
from app.agent.scorer import rank_candidates
from app.config import Settings
from app.db import Database
from app.schemas import AddressCandidate, NormalizedAddress

ProgressCallback = Callable[[str, str], None]


class AddressAgent:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        qwen: QwenClient,
        map_client: MapClient,
        mgeo: MGeoClient,
    ) -> None:
        self.settings = settings
        self.db = db
        self.qwen = qwen
        self.map_client = map_client
        self.mgeo = mgeo

    async def normalize_one(
        self,
        raw_address: str,
        use_qwen: bool,
        use_map_api: bool,
        progress: ProgressCallback | None = None,
    ) -> NormalizedAddress:
        _emit(progress, "clean", "清洗输入")
        cleaned = clean_address(raw_address)
        warnings: list[str] = []
        if not cleaned:
            _emit(progress, "done", "空地址")
            return NormalizedAddress(
                input=raw_address,
                cleaned_input=cleaned,
                normalized_address="",
                output_line="",
                anchor_type="none",
                source="none",
                confidence=0,
                match_level="empty",
                warnings=["地址为空或只包含配送备注"],
            )

        recall_query, input_detail = _split_input_detail(cleaned)
        recall_queries = _unique_texts([cleaned, recall_query])

        _emit(progress, "recall", "召回记忆库、标准库和POI候选")
        candidates: list[AddressCandidate] = []
        for query in recall_queries:
            candidates.extend(self.db.search_memory(query, self.settings.candidate_limit))
            candidates.extend(self.db.search_standard(query, self.settings.candidate_limit))
            candidates.extend(self.db.search_poi(query, self.settings.default_city, self.settings.candidate_limit * 2))

        _emit(progress, "rank", "本地候选排序")
        ranked = _rank_unique_candidates(raw_address, cleaned, candidates, self.settings.candidate_limit)

        mgeo_payload = None
        if use_map_api and self.settings.map_configured and (not ranked or ranked[0].score < 0.72):
            if self.mgeo.enabled:
                _emit(progress, "mgeo", "解析地址要素用于地图召回")
                try:
                    mgeo_payload = await self.mgeo.parse(cleaned)
                    if mgeo_payload:
                        warnings.append("MGeo解析结果已附加到模型上下文")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"MGeo解析失败: {exc}")
            _emit(progress, "map_api", "本地候选不足，调用地图API补召回")
            try:
                map_candidates = await self.map_client.search_many(
                    _map_queries(recall_query or cleaned, mgeo_payload, self.settings.default_city),
                    self.settings.default_city,
                    self.settings.candidate_limit,
                )
                if not map_candidates:
                    warnings.append("地图API未召回候选")
                ranked = _rank_unique_candidates(raw_address, cleaned, ranked + map_candidates, self.settings.candidate_limit)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"地图API调用失败: {exc}")

        fast_path_candidate = _fast_path_candidate(ranked, self.settings)
        if fast_path_candidate:
            warnings.append("高置信候选直接命中，跳过Qwen" if mgeo_payload else "高置信候选直接命中，跳过MGeo和Qwen")
            _emit(progress, "fast_path", "高置信候选直接命中")
            raw_model_output = {"mgeo": mgeo_payload, "qwen": None} if mgeo_payload else None
            return _selected_result(raw_address, cleaned, fast_path_candidate, ranked, warnings, raw_model_output, None, input_detail)

        if use_qwen and self.settings.qwen_configured and ranked:
            if not mgeo_payload:
                _emit(progress, "mgeo", "解析地址要素")
                try:
                    mgeo_payload = await self.mgeo.parse(cleaned)
                    if mgeo_payload:
                        warnings.append("MGeo解析结果已附加到模型上下文")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"MGeo解析失败: {exc}")

        qwen_output = None
        raw_model_output = {"mgeo": mgeo_payload, "qwen": None} if mgeo_payload else None
        selected = ranked[0] if ranked else None
        qwen_rejected = False
        if use_qwen and self.settings.qwen_configured and ranked:
            _emit(progress, "qwen", "Qwen选择或拒识候选")
            try:
                qwen_output = await self.qwen.choose_candidate(raw_address, cleaned, ranked, mgeo_payload=mgeo_payload)
                raw_model_output = {"mgeo": mgeo_payload, "qwen": qwen_output}
                selected_index = qwen_output.get("selected_index") if qwen_output else None
                if qwen_output and selected_index is None:
                    selected = None
                    qwen_rejected = True
                    reason = qwen_output.get("reason")
                    if reason:
                        warnings.append(f"Qwen拒识: {reason}")
                elif isinstance(selected_index, int) and 0 <= selected_index < len(ranked):
                    selected = ranked[selected_index]
                    selected.score = max(selected.score, _model_confidence(qwen_output, selected.score))
                elif qwen_output:
                    warnings.append("Qwen返回的候选索引无效，保留本地排序结果")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Qwen候选选择失败: {exc}")

        if not selected:
            _emit(progress, "unmatched", "没有可信候选")
            if qwen_rejected:
                warnings.append("候选均不可信，不生成规范地址")
            else:
                warnings.append("没有召回可信候选，不生成规范地址")
            return NormalizedAddress(
                input=raw_address,
                cleaned_input=cleaned,
                normalized_address="",
                output_line="",
                components={},
                anchor_type="unmatched",
                source="none",
                confidence=round(_model_confidence(qwen_output, 0.0 if qwen_rejected else 0.3), 3),
                match_level=str(qwen_output.get("match_level") or "unknown") if qwen_output else "unknown",
                candidates=ranked,
                warnings=warnings,
                raw_model_output=raw_model_output,
            )

        _emit(progress, "done", "完成")
        return _selected_result(raw_address, cleaned, selected, ranked, warnings, raw_model_output, qwen_output, input_detail)


def _match_level(candidate: AddressCandidate) -> str:
    if candidate.source == "standard":
        return "standard"
    if candidate.source == "memory":
        return "memory"
    if candidate.name:
        return "poi"
    if candidate.district:
        return "district"
    return "unknown"


def _model_confidence(payload: dict | None, default: float) -> float:
    if not payload:
        return default
    try:
        return float(payload.get("confidence", default))
    except (TypeError, ValueError):
        return default


def _emit(progress: ProgressCallback | None, stage: str, message: str) -> None:
    if progress:
        progress(stage, message)


def _fast_path_candidate(ranked: list[AddressCandidate], settings: Settings) -> AddressCandidate | None:
    if not settings.fast_path_enabled or not ranked:
        return None
    top = ranked[0]
    if top.source == "memory" and top.score >= settings.memory_fast_path_score:
        return top
    if top.source == "standard" and top.score >= settings.standard_fast_path_score:
        return top
    runner_up_score = ranked[1].score if len(ranked) > 1 else 0.0
    if top.score >= settings.fast_path_score and top.score - runner_up_score >= settings.fast_path_gap:
        return top
    return None


def _rank_unique_candidates(
    raw_address: str,
    cleaned: str,
    candidates: list[AddressCandidate],
    limit: int,
) -> list[AddressCandidate]:
    if not candidates:
        return []
    ranked = rank_candidates(raw_address, cleaned, candidates, len(candidates))
    unique: list[AddressCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in ranked:
        key = _candidate_identity(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= limit:
            break
    return unique


def _candidate_identity(candidate: AddressCandidate) -> tuple[str, str]:
    address_key = _identity_text(candidate.full_address)
    if address_key:
        return ("address", address_key)
    return (candidate.source, candidate.candidate_id)


def _identity_text(value: str | None) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffa-zA-Z0-9]+", value or "")).lower()


def _unique_texts(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


_BUILDING_TOKEN = r"[A-Za-z0-9一二三四五六七八九十百千万零〇两]+"
_UNIT_TOKEN = r"[A-Za-z0-9一二三四五六七八九十百千万零〇两]+"
_ROOM_TOKEN = r"[A-Za-z]?\d{2,5}"
_BUILDING = rf"(?P<building>{_BUILDING_TOKEN}(?:栋|幢|号楼|楼栋|座|#))"
_GROUND_FLOOR = r"(?P<floor>[0-9一二三四五六七八九十]+(?:楼|层|F|f))"
_FLOOR = (
    r"(?P<floor>"
    r"(?:-[0-9一二三四五六七八九十]+|负[0-9一二三四五六七八九十]+|地下[0-9一二三四五六七八九十]+)(?:楼|层|F|f)"
    r"|(?:B|b)[0-9]+(?:楼|层|F|f)?"
    r"|(?:地下一|地下二|地下三|地下四|地下五|负一|负二|负三|负四|负五)(?:楼|层|F|f)?"
    r")"
)
_UNIT = rf"(?P<unit>{_UNIT_TOKEN})(?:单元|单|门)"
_ROOM = rf"(?P<room>{_ROOM_TOKEN})(?:室|房|号房|号)?"
_ROOM_WITH_SUFFIX = r"(?P<room>[A-Za-z]?\d{1,5}(?:室|房|号房|号))"
_DETAIL_PATTERNS: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (re.compile(rf"(?P<detail>{_BUILDING}{_UNIT}{_GROUND_FLOOR}{_ROOM_WITH_SUFFIX})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_UNIT}{_GROUND_FLOOR}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_GROUND_FLOOR}{_ROOM_WITH_SUFFIX})$"), {}),
    (re.compile(rf"(?P<detail>{_GROUND_FLOOR}{_ROOM_WITH_SUFFIX})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_FLOOR}{_UNIT}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_FLOOR}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_FLOOR})$"), {}),
    (re.compile(rf"(?P<detail>{_FLOOR}{_UNIT}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_FLOOR}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_FLOOR})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_UNIT}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_UNIT})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}(?P<unit_hyphen>{_UNIT_TOKEN})[-/]{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}(?P<unit_alpha>[A-Za-z])(?P<room_alpha>\d{{2,5}})(?:室|房|号房|号)?)$"), {}),
    (re.compile(rf"(?P<detail>{_BUILDING}{_ROOM})$"), {"default_unit": "1"}),
    (re.compile(rf"(?P<detail>{_BUILDING})$"), {}),
    (re.compile(rf"(?P<detail>{_UNIT}{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>{_UNIT})$"), {}),
    (re.compile(rf"(?P<detail>(?P<unit_hyphen>{_UNIT_TOKEN})[-/]{_ROOM})$"), {}),
    (re.compile(rf"(?P<detail>(?P<room>{_ROOM_TOKEN})(?:室|房|号房))$"), {}),
]


def _input_detail(cleaned: str, selected: AddressCandidate) -> dict[str, str]:
    tail = _detail_after_anchor(cleaned, selected)
    if tail:
        parsed = _parse_detail(tail)
        if parsed:
            return parsed

    parsed = _parse_detail(cleaned)
    if parsed:
        return parsed
    return {}


def _detail_after_anchor(cleaned: str, selected: AddressCandidate) -> str:
    for term in [selected.name]:
        normalized_term = _compact_text(term)
        if not normalized_term:
            continue
        normalized_input = _compact_text(cleaned)
        index = normalized_input.rfind(normalized_term)
        if index < 0:
            continue
        tail = normalized_input[index + len(normalized_term) :]
        if tail and len(tail) <= 40:
            return tail
    return ""


def _parse_detail(value: str) -> dict[str, str]:
    _, parsed = _split_input_detail(value)
    return parsed


def _split_input_detail(value: str) -> tuple[str, dict[str, str]]:
    compact = _compact_text(value)
    if not compact:
        return "", {}
    for pattern, defaults in _DETAIL_PATTERNS:
        match = pattern.search(compact)
        if not match:
            continue
        parsed = _normalize_detail_match(match, defaults)
        if parsed:
            return compact[: match.start("detail")], parsed
    return compact, {}


def _compact_text(value: str | None) -> str:
    normalized = (value or "").replace("－", "-").replace("—", "-").replace("～", "-")
    return re.sub(r"[\s,，。;；]+", "", normalized)


def _normalize_detail_match(match: re.Match[str], defaults: dict[str, str]) -> dict[str, str]:
    groups = match.groupdict()
    building = _normalize_building(groups.get("building"))
    floor = _normalize_floor(groups.get("floor"))
    unit = _normalize_unit(groups.get("unit") or groups.get("unit_hyphen") or groups.get("unit_alpha"))
    room = _normalize_room(groups.get("room") or _alpha_room(groups.get("unit_alpha"), groups.get("room_alpha")))

    if not unit and defaults.get("default_unit") and room:
        unit = _normalize_unit(defaults["default_unit"])

    parts = [part for part in [building, unit, floor, room] if part]
    if not parts:
        return {}
    parsed: dict[str, str] = {}
    if building:
        parsed["building"] = building
    if floor:
        parsed["floor"] = floor
    if unit:
        parsed["unit"] = unit
    if room:
        parsed["room"] = room
    parsed["address_detail"] = "-".join(parts)
    return parsed


def _normalize_building(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("#"):
        return f"{value[:-1]}栋"
    if value.endswith("楼栋"):
        return f"{value[:-2]}栋"
    return value


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _normalize_floor(value: str | None) -> str | None:
    if not value:
        return None
    is_basement = bool(re.match(r"^(?:-|负|B|b|地下)", value))
    text = re.sub(r"(楼|层|F|f)$", "", value)
    text = text.replace("地下", "").replace("负", "").replace("-", "").replace("B", "").replace("b", "")
    floor_no = _parse_small_number(text)
    if floor_no is None:
        return None
    return f"负{floor_no}楼" if is_basement else f"{floor_no}楼"


def _parse_small_number(value: str) -> int | None:
    if not value:
        return 1
    if value.isdigit():
        return int(value)
    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    if value.startswith("十"):
        return 10 + _CHINESE_DIGITS.get(value[1:], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return _CHINESE_DIGITS.get(left, 0) * 10 + _CHINESE_DIGITS.get(right, 0)
    return None


def _normalize_unit(value: str | None) -> str | None:
    if not value:
        return None
    token = re.sub(r"(单元|单|门)$", "", value)
    if len(token) == 1 and token.isalpha():
        token = str(ord(token.upper()) - ord("A") + 1)
    elif token.isdigit():
        token = str(int(token))
    return f"{token}单元"


def _normalize_room(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("号") and not value.endswith("号房"):
        return f"{value[:-1]}号"
    room = re.sub(r"(室|房|号房)$", "", value)
    return f"{room}室"


def _alpha_room(unit_alpha: str | None, room_alpha: str | None) -> str | None:
    if not unit_alpha or not room_alpha:
        return None
    return f"{unit_alpha.upper()}{room_alpha}"


def _merge_input_detail(base_address: str, detail: dict[str, str]) -> str:
    address_detail = detail.get("address_detail")
    if not address_detail:
        return base_address

    base_anchor, base_detail = _split_input_detail(base_address)
    if base_detail and _details_overlap(base_detail, detail):
        return f"{base_anchor}{address_detail}"

    base_key = _identity_text(base_address)
    if _identity_text(address_detail) in base_key:
        return base_address

    remaining_parts = [
        part
        for part in [detail.get("building"), detail.get("unit"), detail.get("floor"), detail.get("room")]
        if part
    ]
    skipped_existing = False
    while remaining_parts and _identity_text(remaining_parts[0]) in base_key:
        remaining_parts.pop(0)
        skipped_existing = True
    remaining_detail = "-".join(remaining_parts) or address_detail
    if _identity_text(remaining_detail) in base_key:
        return base_address
    separator = "-" if skipped_existing and remaining_parts else ""
    return f"{base_address}{separator}{remaining_detail}"


def _details_overlap(left: dict[str, str], right: dict[str, str]) -> bool:
    for key in ("building", "floor", "unit", "room"):
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value and right_value and _detail_part_key(left_value) == _detail_part_key(right_value):
            return True
    return False


def _detail_part_key(value: str) -> str:
    return re.sub(r"(室|房|号房|号)$", "", _identity_text(value))


def _map_queries(cleaned: str, mgeo_payload: dict[str, Any] | None, default_city: str) -> list[str]:
    queries = [cleaned]
    components = (mgeo_payload or {}).get("components") or {}
    for key in ("poi", "road", "community"):
        for value in _component_values(components.get(key)):
            queries.append(value)
            if default_city and default_city not in value:
                queries.append(f"{default_city}{value}")
    return queries


def _component_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _selected_result(
    raw_address: str,
    cleaned: str,
    selected: AddressCandidate,
    ranked: list[AddressCandidate],
    warnings: list[str],
    raw_model_output: dict | None,
    qwen_output: dict | None,
    input_detail: dict[str, str] | None = None,
) -> NormalizedAddress:
    components = {
        "province": selected.province,
        "city": selected.city,
        "district": selected.district,
        "town": selected.town,
        "name": selected.name,
        "category": selected.category,
        "lon": selected.lon,
        "lat": selected.lat,
    }
    input_detail = input_detail or _input_detail(cleaned, selected)
    normalized_address = _merge_input_detail(selected.full_address, input_detail)
    if input_detail:
        components.update(input_detail)
    if normalized_address != selected.full_address:
        warnings.append("已保留输入中的楼栋/单元/房号")

    match_level = _match_level(selected)
    if qwen_output and qwen_output.get("match_level"):
        match_level = str(qwen_output["match_level"])

    return NormalizedAddress(
        input=raw_address,
        cleaned_input=cleaned,
        normalized_address=normalized_address,
        output_line=normalized_address,
        components=components,
        anchor_type=selected.source,
        anchor_id=selected.candidate_id,
        source=selected.source,
        confidence=round(selected.score, 3),
        match_level=match_level,
        candidates=ranked,
        warnings=warnings,
        raw_model_output=raw_model_output,
    )
