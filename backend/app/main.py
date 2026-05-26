from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from app.adapters.map_client import MapClient
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
map_client = MapClient(settings, db)
mgeo = MGeoClient(settings.mgeo_url, settings.mgeo_enabled, settings.mgeo_timeout_seconds)
agent = AddressAgent(settings, db, qwen, map_client, mgeo)
AUTO_PERSIST_WARNING = "已自动沉淀到记忆库"
AUTO_PERSIST_MATCH_LEVELS = {"memory", "standard", "poi"}


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
    try:
        status = db.status()
        map_api_calls_today = db.get_api_call_count("amap", today, today)
        qwen_calls_today = db.get_api_call_count("qwen", today, today)
        database_state = "configured"
    except Exception:  # noqa: BLE001
        status = {"poi_rows": 0, "memory_rows": 0, "standard_rows": 0}
        map_api_calls_today = 0
        qwen_calls_today = 0
        database_state = "unavailable"
    return ConfigStatus(
        database=database_state,
        qwen="configured" if settings.qwen_configured else "disabled",
        mgeo="configured" if mgeo.enabled else "disabled",
        map_api="configured" if settings.map_configured else "disabled",
        standard_address="configured" if status.get("standard_rows", 0) else "missing",
        poi_rows=status.get("poi_rows", 0),
        memory_rows=status.get("memory_rows", 0),
        default_city=settings.default_city,
        map_api_calls_today=map_api_calls_today,
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
            result = await agent.normalize_one(address, use_qwen=request.use_qwen, use_map_api=request.use_map_api)
            if request.auto_persist_memory:
                _try_auto_persist(result)
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
                        use_map_api=request.use_map_api,
                        progress=publish,
                    )
                    if request.auto_persist_memory:
                        _try_auto_persist(result)
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
    if result.match_level not in AUTO_PERSIST_MATCH_LEVELS:
        return False
    threshold = settings.auto_memory_min_confidence
    if result.match_level == "memory":
        threshold = min(threshold, settings.memory_fast_path_score)
    return result.confidence >= threshold


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
