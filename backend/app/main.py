from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from app.adapters.hive_client import HiveClient
from app.adapters.mgeo_client import MGeoClient
from app.adapters.qwen_client import QwenClient
from app.agent.orchestrator import AddressAgent
from app.config import Settings, get_settings
from app.db import Database
from app.schemas import (
    ApiUsageSummary,
    ConfigStatus,
    ConfirmFeedbackRequest,
    NormalizedAddress,
    NormalizeBatchRequest,
    NormalizeBatchResponse,
)


settings: Settings = get_settings()
db = Database(settings.database_url)
qwen = QwenClient(settings, db)
hive = HiveClient(settings, db)
mgeo = MGeoClient(settings.mgeo_url, settings.mgeo_enabled, settings.mgeo_timeout_seconds)
agent = AddressAgent(settings, db, qwen, hive, mgeo)
AUTO_PERSIST_WARNING = "已自动沉淀到记忆库"
AUTO_PERSIST_SOURCES = {"standard"}
STANDARD_AUTO_PERSIST_MIN_CONFIDENCE = 0.95


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_init_db:
        db.initialize()
    if settings.auto_seed_public_poi:
        db.seed_public_poi(settings.public_poi_csv)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config/status", response_model=ConfigStatus)
def config_status() -> ConfigStatus:
    today = datetime.now(UTC).date().isoformat()
    hive_state = "disabled"
    try:
        status = db.status()
        hive_calls_today = db.get_api_call_count("hive", today, today)
        qwen_calls_today = db.get_api_call_count("qwen", today, today)
        database_state = "configured"
    except Exception:  # noqa: BLE001
        status = {"poi_rows": 0, "memory_rows": 0, "memory_alias_rows": 0, "memory_detail_rows": 0, "standard_rows": 0}
        hive_calls_today = 0
        qwen_calls_today = 0
        database_state = "unavailable"
    if settings.hive_configured:
        hive_state = "connected" if hive.check_connection() else "disconnected"
    return ConfigStatus(
        database=database_state,
        qwen="configured" if settings.qwen_configured else "disabled",
        mgeo="configured" if mgeo.enabled else "disabled",
        hive=hive_state,
        recall_scope_mode=settings.recall_scope_mode,
        hive_table=settings.hive_table if settings.hive_configured else None,
        poi_rows=status.get("poi_rows", 0),
        memory_rows=status.get("memory_rows", 0),
        memory_alias_rows=status.get("memory_alias_rows", 0),
        memory_detail_rows=status.get("memory_detail_rows", 0),
        default_city=settings.default_city,
        hive_calls_today=hive_calls_today,
        qwen_calls_today=qwen_calls_today,
    )


@app.get("/api/usage/summary", response_model=ApiUsageSummary)
def api_usage_summary(provider: str, start: str, end: str) -> ApiUsageSummary:
    try:
        summary = db.get_api_usage_summary(provider, start, end)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"API usage summary unavailable: {exc}") from exc
    return ApiUsageSummary(**summary)


@app.post("/api/normalize/batch", response_model=NormalizeBatchResponse)
async def normalize_batch(request: NormalizeBatchRequest) -> NormalizeBatchResponse:
    addresses = [address.strip() for address in request.addresses if address.strip()]
    if not addresses:
        raise HTTPException(status_code=400, detail="No valid addresses provided")

    job_id = db.create_job(len(addresses)) if request.persist_job else None
    concurrency = _effective_concurrency(request.concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    results: list[Any] = [None] * len(addresses)

    async def run_one(index: int, address: str) -> None:
        async with semaphore:
            result = await agent.normalize_one(address, use_qwen=request.use_qwen)
            auto_persisted = _try_auto_persist(result) if request.auto_persist_memory else False
            result.auto_persist_reason = _auto_persist_reason(result, request.auto_persist_memory, auto_persisted)
            results[index] = result

    await asyncio.gather(*(run_one(index, address) for index, address in enumerate(addresses)))

    for address, result in zip(addresses, results, strict=True):
        if job_id and result:
            db.save_result(job_id, address, result.normalized_address, result.source, result.confidence, result.model_dump(mode="json"))
    return NormalizeBatchResponse(results=results)


@app.post("/api/normalize/stream")
async def normalize_stream(request: NormalizeBatchRequest) -> StreamingResponse:
    addresses = [address.strip() for address in request.addresses if address.strip()]
    if not addresses:
        raise HTTPException(status_code=400, detail="No valid addresses provided")

    concurrency = _effective_concurrency(request.concurrency)

    async def event_stream():
        started_at = time.perf_counter()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        semaphore = asyncio.Semaphore(concurrency)
        job_id = db.create_job(len(addresses)) if request.persist_job else None
        finished = 0
        failed = 0

        yield _json_line(
            {
                "type": "batch_start",
                "total": len(addresses),
                "concurrency": concurrency,
                "elapsed_ms": 0,
            }
        )

        async def run_one(index: int, address: str) -> None:
            row_started_at = time.perf_counter()

            def publish(stage: str, message: str) -> None:
                queue.put_nowait(
                    {
                        "type": "progress",
                        "index": index,
                        "input": address,
                        "status": "running",
                        "stage": stage,
                        "message": message,
                        "elapsed_ms": round((time.perf_counter() - row_started_at) * 1000),
                    }
                )

            async with semaphore:
                publish("start", "开始处理")
                try:
                    result = await agent.normalize_one(
                        address,
                        use_qwen=request.use_qwen,
                        progress=publish,
                    )
                    auto_persisted = _try_auto_persist(result) if request.auto_persist_memory else False
                    result.auto_persist_reason = _auto_persist_reason(result, request.auto_persist_memory, auto_persisted)
                    if job_id:
                        db.save_result(
                            job_id,
                            address,
                            result.normalized_address,
                            result.source,
                            result.confidence,
                            result.model_dump(mode="json"),
                        )
                    await queue.put(
                        {
                            "type": "result",
                            "index": index,
                            "input": address,
                            "status": "done",
                            "stage": "done",
                            "message": "完成",
                            "elapsed_ms": round((time.perf_counter() - row_started_at) * 1000),
                            "result": result.model_dump(mode="json"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await queue.put(
                        {
                            "type": "error",
                            "index": index,
                            "input": address,
                            "status": "error",
                            "stage": "error",
                            "message": str(exc),
                            "elapsed_ms": round((time.perf_counter() - row_started_at) * 1000),
                        }
                    )

        tasks = [asyncio.create_task(run_one(index, address)) for index, address in enumerate(addresses)]
        try:
            while finished + failed < len(addresses):
                event = await queue.get()
                if event["type"] == "result":
                    finished += 1
                    event["completed"] = finished
                    event["failed"] = failed
                    event["total"] = len(addresses)
                elif event["type"] == "error":
                    failed += 1
                    event["completed"] = finished
                    event["failed"] = failed
                    event["total"] = len(addresses)
                yield _json_line(event)
            await asyncio.gather(*tasks, return_exceptions=True)
            yield _json_line(
                {
                    "type": "batch_complete",
                    "total": len(addresses),
                    "completed": finished,
                    "failed": failed,
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
                }
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/api/feedback/confirm")
def confirm_feedback(request: ConfirmFeedbackRequest) -> dict[str, int]:
    memory_id = db.upsert_memory(request.model_dump(mode="json"))
    return {"memory_id": memory_id}


def _try_auto_persist(result: NormalizedAddress) -> bool:
    if not _should_auto_persist(result):
        return False
    try:
        db.upsert_memory(_memory_payload(result, confirmed_by="auto"))
    except Exception as exc:  # noqa: BLE001
        _append_warning(result, f"自动沉淀失败: {exc}")
        return False
    _append_warning(result, AUTO_PERSIST_WARNING)
    return True


def _should_auto_persist(result: NormalizedAddress) -> bool:
    if not result.normalized_address or result.anchor_type == "unmatched" or result.source == "none":
        return False
    if result.source not in AUTO_PERSIST_SOURCES:
        return False
    if result.confidence < max(settings.auto_memory_min_confidence, STANDARD_AUTO_PERSIST_MIN_CONFIDENCE):
        return False
    return _result_covers_input(result)


def _result_covers_input(result: NormalizedAddress) -> bool:
    return _input_residual(result) == ""


def _input_residual(result: NormalizedAddress) -> str:
    input_key = _signal_key(result.cleaned_input or result.input)
    if not input_key:
        return ""

    components = result.components or {}
    candidate_keys = _unique_keys(
        [
            result.normalized_address,
            str(components.get("name") or ""),
        ]
    )
    if any(input_key in key for key in candidate_keys):
        return ""

    residual = input_key
    removable_values = [
        result.normalized_address,
        str(components.get("name") or ""),
        str(components.get("province") or ""),
        str(components.get("city") or ""),
        str(components.get("district") or ""),
        str(components.get("town") or ""),
        str(components.get("building") or ""),
        str(components.get("unit") or ""),
        str(components.get("floor") or ""),
        str(components.get("room") or ""),
        str(components.get("address_detail") or ""),
    ]
    for key in sorted(_unique_keys(removable_values), key=len, reverse=True):
        residual = residual.replace(key, "")
        for suffix in ("维吾尔自治区", "自治区", "自治州", "地区", "省", "市", "区", "县"):
            if key.endswith(suffix):
                residual = residual.replace(key[: -len(suffix)], "")

    residual = _strip_non_anchor_terms(residual, components)
    return residual if len(residual) > 3 else ""


def _auto_persist_reason(result: NormalizedAddress, enabled: bool, persisted: bool) -> str | None:
    if persisted:
        return None
    if not enabled:
        return "本次未开启自动沉淀"
    if not result.normalized_address or result.anchor_type == "unmatched" or result.source == "none":
        return "当前结果未形成可沉淀的规范地址"
    if any(warning.startswith("自动沉淀失败:") for warning in result.warnings):
        return next(warning for warning in result.warnings if warning.startswith("自动沉淀失败:"))
    if result.source == "memory":
        return None
    if result.source not in AUTO_PERSIST_SOURCES:
        return f"当前仅对 {', '.join(sorted(AUTO_PERSIST_SOURCES))} 结果自动沉淀"
    threshold = max(settings.auto_memory_min_confidence, STANDARD_AUTO_PERSIST_MIN_CONFIDENCE)
    if result.confidence < threshold:
        return f"置信度 {result.confidence:.3f} 未达到自动沉淀阈值 {threshold:.3f}"
    residual = _input_residual(result)
    if residual:
        return f"输入里仍有未被最终结果覆盖的关键信号：{residual}"
    return "未满足自动沉淀条件"


def _unique_keys(values: list[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _signal_key(value)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _signal_key(value: str | None) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffa-zA-Z0-9]+", value or "")).lower()


def _strip_non_anchor_terms(value: str, components: dict[str, Any] | None = None) -> str:
    text = value
    for term in _location_terms(components):
        text = text.replace(term, "")
    for term in (
        "送到",
        "快递",
        "驿站",
        "就行",
        "门口",
        "对面",
        "旁边",
        "附近",
        "楼下",
        "入口",
        "出口",
    ):
        text = text.replace(term, "")
    text = re.sub(r"\d+号门", "", text)
    return text


def _location_terms(components: dict[str, Any] | None) -> list[str]:
    suffixes = ("维吾尔自治区", "自治区", "自治州", "地区", "省", "市", "区", "县", "旗")
    values = [
        str((components or {}).get("province") or ""),
        str((components or {}).get("city") or ""),
        str((components or {}).get("district") or ""),
        str((components or {}).get("town") or ""),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token:
            continue
        variants = [token]
        for suffix in suffixes:
            if token.endswith(suffix):
                variants.append(token[: -len(suffix)])
        for item in variants:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
    return terms


def _memory_payload(result: NormalizedAddress, confirmed_by: str) -> dict[str, Any]:
    return ConfirmFeedbackRequest(
        raw_address=result.input,
        normalized_address=result.normalized_address,
        components=result.components,
        anchor_type=result.anchor_type or "business_memory",
        anchor_id=result.anchor_id,
        anchor_source=result.source,
        lon=result.components.get("lon") if isinstance(result.components.get("lon"), (int, float)) else None,
        lat=result.components.get("lat") if isinstance(result.components.get("lat"), (int, float)) else None,
        confirmed_by=confirmed_by,
    ).model_dump(mode="json")


def _append_warning(result: NormalizedAddress, warning: str) -> None:
    if warning not in result.warnings:
        result.warnings.append(warning)


def _effective_concurrency(value: int) -> int:
    return max(1, min(value, settings.max_batch_concurrency))


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
