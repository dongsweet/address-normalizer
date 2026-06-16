from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.adapters.mgeo_client import MGeoClient
from app.adapters.qwen_client import QwenClient
from app.agent.cleaner import clean_address
from app.agent.scorer import has_strong_anchor_evidence, rank_candidates
from app.config import Settings
from app.db import Database
from app.schemas import AddressCandidate, NormalizedAddress

ProgressCallback = Callable[[str, str], None]


class StandardAddressClient(Protocol):
    provider: str

    @property
    def enabled(self) -> bool: ...

    async def search(self, query: str, city: str | None, district: str | None = None, limit: int = 8) -> list[AddressCandidate]: ...


@dataclass
class PipelineAttempt:
    cleaned: str
    ranked: list[AddressCandidate]
    warnings: list[str]
    selected: AddressCandidate | None
    qwen_rejected: bool
    qwen_output: dict[str, Any] | None
    raw_model_output: dict[str, Any] | None
    input_detail: dict[str, str]


@dataclass(frozen=True)
class RecallScope:
    city: str | None = None
    district: str | None = None


class AddressAgent:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        qwen: QwenClient,
        standard_client: StandardAddressClient,
        mgeo: MGeoClient,
    ) -> None:
        self.settings = settings
        self.db = db
        self.qwen = qwen
        self.standard_client = standard_client
        self.mgeo = mgeo

    async def normalize_one(
        self,
        raw_address: str,
        use_qwen: bool,
        progress: ProgressCallback | None = None,
    ) -> NormalizedAddress:
        _emit(progress, "clean", "清洗输入")
        cleaned = clean_address(raw_address)
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
        attempt = await self._run_pipeline(
            raw_address=raw_address,
            cleaned=cleaned,
            use_qwen=use_qwen,
            progress=progress,
        )

        repaired_raw_output: dict[str, Any] | None = None
        if self._should_repair_cleaning(raw_address, cleaned, attempt, use_qwen):
            _emit(progress, "repair", "Qwen修复清洗结果")
            try:
                repair_payload = await self.qwen.repair_cleaned_address(raw_address, cleaned)
            except Exception as exc:  # noqa: BLE001
                repair_payload = None
                attempt.warnings.append(f"Qwen清洗修复失败: {exc}")
            repaired_raw_output = {"clean_repair": repair_payload, "cleaned_before_repair": cleaned} if repair_payload else None
            repaired_cleaned = _repaired_cleaned_text(repair_payload)
            if repaired_cleaned and repaired_cleaned != cleaned:
                attempt = await self._run_pipeline(
                    raw_address=raw_address,
                    cleaned=repaired_cleaned,
                    use_qwen=use_qwen,
                    progress=progress,
                )
                attempt.warnings.insert(0, _clean_repair_warning(repair_payload, repaired_cleaned))

        attempt.raw_model_output = _merge_raw_model_output(attempt.raw_model_output, repaired_raw_output)
        if not attempt.selected:
            _emit(progress, "unmatched", "没有可信候选")
            if attempt.qwen_rejected:
                attempt.warnings.append("候选均不可信，不生成规范地址")
            else:
                attempt.warnings.append("没有召回可信候选，不生成规范地址")
            return NormalizedAddress(
                input=raw_address,
                cleaned_input=attempt.cleaned,
                normalized_address="",
                output_line="",
                components={},
                anchor_type="unmatched",
                source="none",
                confidence=round(_model_confidence(attempt.qwen_output, 0.0 if attempt.qwen_rejected else 0.3), 3),
                match_level=str(attempt.qwen_output.get("match_level") or "unknown") if attempt.qwen_output else "unknown",
                candidates=attempt.ranked,
                warnings=attempt.warnings,
                raw_model_output=attempt.raw_model_output,
            )

        _emit(progress, "done", "完成")
        return _selected_result(
            raw_address,
            attempt.cleaned,
            attempt.selected,
            attempt.ranked,
            attempt.warnings,
            attempt.raw_model_output,
            attempt.qwen_output,
            attempt.input_detail,
        )

    async def _run_pipeline(
        self,
        *,
        raw_address: str,
        cleaned: str,
        use_qwen: bool,
        progress: ProgressCallback | None,
    ) -> PipelineAttempt:
        warnings: list[str] = []
        recall_query, input_detail = _split_input_detail(cleaned)
        recall_queries = _unique_texts([cleaned, recall_query])
        recall_scope = _resolve_recall_scope(raw_address, cleaned, self.settings)

        _emit(progress, "recall", "召回记忆库和POI候选")
        candidates: list[AddressCandidate] = []
        for query in recall_queries:
            candidates.extend(self.db.search_memory(query, self.settings.candidate_limit))
            candidates.extend(
                self.db.search_poi(
                    query,
                    recall_scope.city,
                    recall_scope.district,
                    self.settings.candidate_limit * 2,
                )
            )

        if self.standard_client.enabled:
            _emit(progress, "standard", "查询标准地址库")
            for query in recall_queries:
                try:
                    candidates.extend(
                        await self.standard_client.search(
                            query,
                            recall_scope.city,
                            recall_scope.district,
                            self.settings.candidate_limit,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"标准地址库查询失败: {_format_exception_chain(exc)}")
                    break

        _emit(progress, "rank", "本地候选排序")
        ranked = _rank_unique_candidates(raw_address, cleaned, candidates, self.settings.candidate_limit)

        mgeo_payload = None
        fast_path_candidate = _fast_path_candidate(ranked, self.settings)
        if fast_path_candidate:
            warnings.append("高置信候选直接命中，跳过Qwen" if mgeo_payload else "高置信候选直接命中，跳过MGeo和Qwen")
            _emit(progress, "fast_path", "高置信候选直接命中")
            raw_model_output = {"mgeo": mgeo_payload, "qwen": None} if mgeo_payload else None
            return PipelineAttempt(
                cleaned=cleaned,
                ranked=ranked,
                warnings=warnings,
                selected=fast_path_candidate,
                qwen_rejected=False,
                qwen_output=None,
                raw_model_output=raw_model_output,
                input_detail=input_detail,
            )

        if use_qwen and self.settings.qwen_configured and ranked and self.mgeo.enabled:
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
        selected_by_qwen = False
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
                    selected_by_qwen = True
                    selected.score = max(selected.score, _model_confidence(qwen_output, selected.score))
                elif qwen_output:
                    warnings.append("Qwen返回的候选索引无效，保留本地排序结果")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Qwen候选选择失败: {exc}")

        if selected and selected.source in {"memory", "standard"} and not selected_by_qwen and not has_strong_anchor_evidence(selected):
            warnings.append("候选锚点证据不足，不直接输出")
            selected = None

        return PipelineAttempt(
            cleaned=cleaned,
            ranked=ranked,
            warnings=warnings,
            selected=selected,
            qwen_rejected=qwen_rejected,
            qwen_output=qwen_output,
            raw_model_output=raw_model_output,
            input_detail=input_detail,
        )

    def _should_repair_cleaning(
        self,
        raw_address: str,
        cleaned: str,
        attempt: PipelineAttempt,
        use_qwen: bool,
    ) -> bool:
        if not (use_qwen and self.settings.qwen_configured and self.settings.cleaning_repair_enabled):
            return False
        if not _looks_like_mixed_input(raw_address, cleaned):
            return False
        top_score = attempt.selected.score if attempt.selected else (attempt.ranked[0].score if attempt.ranked else 0.0)
        return top_score < self.settings.cleaning_repair_min_score


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
    if top.source == "memory":
        if top.score >= settings.memory_fast_path_score and has_strong_anchor_evidence(top):
            return top
        return None
    if top.source == "standard":
        if top.score >= settings.standard_fast_path_score and has_strong_anchor_evidence(top):
            return top
        return None
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


_PROVINCE_PREFIX_RE = re.compile(r"^(?P<province>[\u4e00-\u9fff]{2,20}(?:特别行政区|自治区|省))")
_CITY_RE = re.compile(r"(?P<city>[\u4e00-\u9fff]{2,20}?(?:自治州|地区|盟|市))")
_DISTRICT_RE = re.compile(r"(?P<district>[\u4e00-\u9fff]{1,20}?(?:区|县|旗|市))")
_DISTRICT_ALIASES = {
    "沙区": RecallScope(city="乌鲁木齐市", district="沙依巴克区"),
    "沙依巴克区": RecallScope(city="乌鲁木齐市", district="沙依巴克区"),
    "水区": RecallScope(city="乌鲁木齐市", district="水磨沟区"),
    "水磨沟区": RecallScope(city="乌鲁木齐市", district="水磨沟区"),
    "米东": RecallScope(city="乌鲁木齐市", district="米东区"),
    "米东区": RecallScope(city="乌鲁木齐市", district="米东区"),
}


def _resolve_recall_scope(raw_address: str, cleaned: str, settings: Settings) -> RecallScope:
    if settings.recall_scope_mode == "off":
        return RecallScope()
    if settings.recall_scope_mode == "fixed":
        return RecallScope(city=settings.default_city)

    cleaned_scope = _extract_recall_scope(cleaned)
    raw_scope = _extract_recall_scope(raw_address)
    return RecallScope(
        city=cleaned_scope.city or raw_scope.city,
        district=cleaned_scope.district or raw_scope.district,
    )


def _extract_recall_scope(value: str) -> RecallScope:
    scoped_value = _strip_province_prefix(value)
    city_match = _CITY_RE.search(scoped_value)
    city = city_match.group("city") if city_match else None
    district = _extract_district_after_city(scoped_value, city_match)
    if city and district and district.startswith(city):
        stripped = district[len(city) :].strip()
        district = stripped or district
    alias_scope = _district_alias_scope(district)
    if alias_scope:
        city = city or alias_scope.city
        district = alias_scope.district
    return RecallScope(city=city, district=district)


def _strip_province_prefix(value: str) -> str:
    match = _PROVINCE_PREFIX_RE.match(value)
    if not match:
        return value
    return value[match.end() :]


def _extract_district_after_city(value: str, city_match: re.Match[str] | None) -> str | None:
    search_value = value[city_match.end() :] if city_match else value
    for match in _DISTRICT_RE.finditer(search_value):
        district = match.group("district")
        if _looks_like_address_anchor(district):
            continue
        return district
    return None


def _looks_like_address_anchor(value: str) -> bool:
    return value.endswith("小区") or value.endswith("园区") or value.endswith("校区")


def _district_alias_scope(value: str | None) -> RecallScope | None:
    if not value:
        return None
    return _DISTRICT_ALIASES.get(value)


def _looks_like_mixed_input(raw_address: str, cleaned: str) -> bool:
    raw_key = _compact_text(raw_address)
    cleaned_key = _compact_text(cleaned)
    if not raw_key or not cleaned_key:
        return False
    if re.search(r"[，,;；\n]", raw_address) and raw_key != cleaned_key:
        return True
    if len(raw_key) - len(cleaned_key) >= 4:
        return True
    return bool(re.search(r"(前台|门口|楼下|保安|电话|联系|备注|麻烦|谢谢|快递|外卖)", raw_address) and raw_key != cleaned_key)


def _repaired_cleaned_text(payload: dict[str, Any] | None) -> str | None:
    if not payload or payload.get("has_address") is False:
        return None
    cleaned = str(payload.get("cleaned_address") or "").strip()
    if not cleaned:
        anchor = str(payload.get("anchor_text") or "").strip()
        detail = str(payload.get("detail_text") or "").strip()
        cleaned = f"{anchor}{detail}".strip()
    if not cleaned:
        return None
    return clean_address(cleaned)


def _clean_repair_warning(payload: dict[str, Any] | None, repaired_cleaned: str) -> str:
    reason = str((payload or {}).get("reason") or "").strip()
    if reason:
        return f"Qwen清洗修复后重试: {repaired_cleaned}（{reason}）"
    return f"Qwen清洗修复后重试: {repaired_cleaned}"


def _merge_raw_model_output(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any] | None:
    if not base and not extra:
        return None
    merged: dict[str, Any] = {}
    if base:
        merged.update(base)
    if extra:
        merged.update(extra)
    return merged


def _format_exception_chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        label = type(current).__name__
        parts.append(f"{label}: {message}" if message else label)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


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
    (re.compile(rf"(?P<detail>{_GROUND_FLOOR})$"), {}),
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
            return _strip_anchor_separator(compact[: match.start("detail")]), parsed
    return compact, {}


def _strip_anchor_separator(value: str) -> str:
    return value.rstrip("-_/\\|:：#")


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

    base_key = _identity_text(base_address)
    if _identity_text(address_detail) in base_key:
        return base_address

    base_anchor, base_detail = _split_input_detail(base_address)
    if base_detail and _details_overlap(base_detail, detail):
        return f"{base_anchor}{address_detail}"

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


def _strip_candidate_detail(selected: AddressCandidate) -> str:
    metadata = selected.metadata or {}
    detail_parts = [
        _clean_detail_part(metadata.get("building")),
        _clean_detail_part(metadata.get("unit")),
        _clean_detail_part(metadata.get("floor")),
        _clean_detail_part(metadata.get("room")),
    ]
    detail_parts = [part for part in detail_parts if part]
    if not detail_parts:
        return selected.full_address

    suffixes: list[str] = []
    for start in range(len(detail_parts)):
        suffixes.extend(_detail_suffix_variants(detail_parts[start:]))

    for suffix in sorted(set(suffixes), key=len, reverse=True):
        if suffix and selected.full_address.endswith(suffix):
            return _strip_anchor_separator(selected.full_address[: -len(suffix)])
    return selected.full_address


def _detail_suffix_variants(parts: list[str]) -> list[str]:
    return [
        "".join(parts),
        "-".join(parts),
        "_".join(parts),
        "/".join(parts),
    ]


def _clean_detail_part(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


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
    input_anchor, _ = _split_input_detail(cleaned)
    output_base_address = _strip_candidate_detail(selected)
    normalized_address = _merge_input_detail(output_base_address, input_detail)
    if input_detail:
        components.update(input_detail)
        if input_anchor:
            components["input_anchor"] = input_anchor
    if normalized_address != selected.full_address:
        if input_detail:
            warnings.append("已保留输入中的楼栋/楼层/单元/房号")
        elif output_base_address != selected.full_address:
            warnings.append("候选包含输入未覆盖的楼栋/楼层/单元/房号，已截断到地址锚点")

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
