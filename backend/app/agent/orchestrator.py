from __future__ import annotations

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

        _emit(progress, "recall", "召回记忆库、标准库和POI候选")
        candidates: list[AddressCandidate] = []
        candidates.extend(self.db.search_memory(cleaned, self.settings.candidate_limit))
        candidates.extend(self.db.search_standard(cleaned, self.settings.candidate_limit))
        candidates.extend(self.db.search_poi(cleaned, self.settings.default_city, self.settings.candidate_limit * 2))

        _emit(progress, "rank", "本地候选排序")
        ranked = rank_candidates(raw_address, cleaned, candidates, self.settings.candidate_limit)

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
                    _map_queries(cleaned, mgeo_payload, self.settings.default_city),
                    self.settings.default_city,
                    self.settings.candidate_limit,
                )
                if not map_candidates:
                    warnings.append("地图API未召回候选")
                ranked = rank_candidates(raw_address, cleaned, ranked + map_candidates, self.settings.candidate_limit)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"地图API调用失败: {exc}")

        fast_path_candidate = _fast_path_candidate(ranked, self.settings)
        if fast_path_candidate:
            warnings.append("高置信候选直接命中，跳过Qwen" if mgeo_payload else "高置信候选直接命中，跳过MGeo和Qwen")
            _emit(progress, "fast_path", "高置信候选直接命中")
            raw_model_output = {"mgeo": mgeo_payload, "qwen": None} if mgeo_payload else None
            return _selected_result(raw_address, cleaned, fast_path_candidate, ranked, warnings, raw_model_output, None)

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
        return _selected_result(raw_address, cleaned, selected, ranked, warnings, raw_model_output, qwen_output)


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
    match_level = _match_level(selected)
    if qwen_output and qwen_output.get("match_level"):
        match_level = str(qwen_output["match_level"])

    return NormalizedAddress(
        input=raw_address,
        cleaned_input=cleaned,
        normalized_address=selected.full_address,
        output_line=selected.full_address,
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
