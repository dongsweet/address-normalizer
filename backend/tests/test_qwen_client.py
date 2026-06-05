from __future__ import annotations

import asyncio

import httpx

from app.adapters.qwen_client import QwenClient
from app.config import Settings
from app.schemas import AddressCandidate


class DummyResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": '{"selected_index": 0, "confidence": 0.92}'}}],
            "usage": {"total_tokens": 12},
        }


class DummyAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, endpoint: str, headers: dict[str, str], json: dict) -> DummyResponse:
        DummyAsyncClient.last_request = {"endpoint": endpoint, "headers": headers, "json": json}
        return DummyResponse()


def test_qwen_request_omits_authorization_when_api_key_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)
    client = QwenClient(Settings(qwen_base_url="http://qwen.internal/v1", qwen_model="qwen3"))

    result = asyncio.run(
        client.choose_candidate(
            "友好北路689美美友好购物中心H&M",
            "友好北路689美美友好购物中心H&M",
            [
                AddressCandidate(
                    source="standard",
                    candidate_id="HIVE-001",
                    name="美美友好购物中心H&M",
                    full_address="新疆维吾尔自治区乌鲁木齐市沙依巴克区友好北路689号美美友好购物中心H&M",
                    score=0.9,
                    metadata={},
                )
            ],
        )
    )

    assert result is not None
    assert DummyAsyncClient.last_request["headers"] == {}
