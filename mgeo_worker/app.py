from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel


MODEL_ID = os.getenv("MGEO_MODEL_ID", "iic/mgeo_geographic_elements_tagging_chinese_base")
MODEL_REVISION = os.getenv("MGEO_MODEL_REVISION") or None
LOAD_ON_STARTUP = os.getenv("MGEO_LOAD_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}
USE_MODEL = os.getenv("MGEO_USE_MODEL", "true").lower() in {"1", "true", "yes", "on"}

app = FastAPI(title="MGeo Address Parser", version="0.1.0")

_model_lock = threading.Lock()
_pipeline: Optional[Any] = None
_pipeline_error: Optional[str] = None
_loading = False


class ParseRequest(BaseModel):
    address: str


@app.on_event("startup")
def startup() -> None:
    if LOAD_ON_STARTUP and USE_MODEL:
        thread = threading.Thread(target=_load_pipeline, daemon=True)
        thread.start()


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_loaded": _pipeline is not None,
        "model_loading": _loading,
        "model_error": _pipeline_error,
    }


@app.post("/parse")
def parse(request: ParseRequest) -> Dict[str, Any]:
    address = request.address.strip()
    if not address:
        return {
            "address": request.address,
            "model_id": MODEL_ID,
            "model_loaded": _pipeline is not None,
            "fallback": False,
            "items": [],
            "components": {},
        }

    pipeline = _load_pipeline() if USE_MODEL else None
    if pipeline is not None:
        output = pipeline(input=address)
        items = _extract_items(output)
        return {
            "address": address,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "model_loaded": True,
            "fallback": False,
            "items": items,
            "components": _components_from_items(items),
            "raw": _json_safe(output),
        }

    items = _fallback_items(address)
    return {
        "address": address,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_loaded": False,
        "model_error": _pipeline_error,
        "fallback": True,
        "items": items,
        "components": _components_from_items(items),
    }


def _load_pipeline() -> Optional[Any]:
    global _loading, _pipeline, _pipeline_error
    if _pipeline is not None or _pipeline_error:
        return _pipeline
    with _model_lock:
        if _pipeline is not None or _pipeline_error:
            return _pipeline
        _loading = True
        try:
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks

            kwargs = {"model": MODEL_ID}
            if MODEL_REVISION:
                kwargs["model_revision"] = MODEL_REVISION
            _pipeline = pipeline(Tasks.token_classification, **kwargs)
        except Exception as exc:  # noqa: BLE001
            _pipeline_error = repr(exc)
            _pipeline = None
        finally:
            _loading = False
    return _pipeline


def _extract_items(output: Any) -> List[Dict[str, Any]]:
    if isinstance(output, dict):
        candidates = output.get("output") or output.get("items") or output.get("entities") or output.get("spans")
    else:
        candidates = output
    if not isinstance(candidates, list):
        return []

    items: List[Dict[str, Any]] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        label = entry.get("type") or entry.get("label") or entry.get("entity") or entry.get("entity_group")
        text = entry.get("span") or entry.get("word") or entry.get("text") or entry.get("value")
        if not label or not text:
            continue
        items.append(
            {
                "type": str(label),
                "text": str(text),
                "start": _json_safe(entry.get("start")),
                "end": _json_safe(entry.get("end")),
                "score": _json_safe(entry.get("score") or entry.get("prob")),
            }
        )
    return items


def _components_from_items(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    components: Dict[str, List[str]] = {}
    for item in items:
        label = str(item.get("type") or "unknown")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        components.setdefault(label, [])
        if text not in components[label]:
            components[label].append(text)
    return components


def _fallback_items(address: str) -> List[Dict[str, Any]]:
    patterns = [
        ("city", r"乌鲁木齐市?|乌市"),
        ("district", r"(天山区|沙依巴克区|新市区|水磨沟区|头屯河区|达坂城区|米东区|乌鲁木齐县)"),
        ("road", r"[\u4e00-\u9fffA-Za-z0-9]+(?:路|街|大道|巷|道|街道)"),
        ("road_no", r"[0-9０-９一二三四五六七八九十百]+号"),
        ("poi", r"[\u4e00-\u9fffA-Za-z0-9]+(?:中心|大厦|广场|商场|小区|酒店|学校|医院|机场|车站)"),
    ]
    items: List[Dict[str, Any]] = []
    for label, pattern in patterns:
        for match in re.finditer(pattern, address):
            text = match.group(0)
            if any(item["type"] == label and item["text"] == text for item in items):
                continue
            items.append({"type": label, "text": text, "start": match.start(), "end": match.end(), "score": None})
    items.sort(key=lambda item: (item["start"] if item["start"] is not None else 10**9, item["end"] or 0))
    return items


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return str(value)
