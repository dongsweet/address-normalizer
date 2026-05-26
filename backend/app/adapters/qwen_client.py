from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.config import Settings
from app.schemas import AddressCandidate

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)


class QwenClient:
    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db

    async def choose_candidate(
        self,
        raw: str,
        cleaned: str,
        candidates: list[AddressCandidate],
        mgeo_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.settings.qwen_configured or not candidates:
            return None
        if self._quota_exceeded("qwen", self.settings.qwen_daily_quota, self.settings.qwen_monthly_quota):
            logger.warning("qwen api quota exceeded, skipping chat completion")
            self._log_api_call(
                provider="qwen",
                call_type="chat_completion",
                request_query=cleaned or raw,
                response_status="quota_exceeded",
                metadata={"candidate_count": len(candidates), "model": self.settings.qwen_model},
            )
            return None

        base_url = self.settings.qwen_base_url.rstrip("/")
        endpoint = f"{base_url}/chat/completions"
        if not base_url.endswith("/v1"):
            endpoint = f"{base_url}/v1/chat/completions"

        candidate_payload = [
            {
                "index": idx,
                "source": item.source,
                "candidate_id": item.candidate_id,
                "name": item.name,
                "full_address": item.full_address,
                "components": {
                    "province": item.province,
                    "city": item.city,
                    "district": item.district,
                    "town": item.town,
                },
                "score": item.score,
            }
            for idx, item in enumerate(candidates)
        ]
        system_prompt = (
            "你是地址规范化候选选择器。只能从候选列表中选择最合理的一项，"
            "不能凭空发明地址。若候选都不可信，selected_index 返回 null。"
            "只输出 JSON。"
        )
        user_prompt = {
            "raw_address": raw,
            "cleaned_address": cleaned,
            "mgeo_parse": mgeo_payload,
            "candidates": candidate_payload,
            "output_schema": {
                "selected_index": "number|null",
                "confidence": "0-1 number",
                "match_level": "memory|standard|poi|road|district|unknown",
                "reason": "short Chinese explanation",
                "normalized_address": "string|null",
            },
        }
        request_payload = {
            "model": self.settings.qwen_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ],
        }
        headers = {"Authorization": f"Bearer {self.settings.qwen_api_key}"}
        http_status: int | None = None
        try:
            async with httpx.AsyncClient(timeout=self.settings.qwen_timeout_seconds) as client:
                response = await client.post(endpoint, headers=headers, json=request_payload)
                http_status = response.status_code
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            self._log_api_call(
                provider="qwen",
                call_type="chat_completion",
                request_query=cleaned or raw,
                response_status="error",
                http_status=http_status,
                error_message=str(exc),
                metadata={"candidate_count": len(candidates), "model": self.settings.qwen_model},
            )
            raise

        usage = payload.get("usage") or {}
        tokens_used = _to_int(usage.get("total_tokens"))
        self._log_api_call(
            provider="qwen",
            call_type="chat_completion",
            request_query=cleaned or raw,
            response_status="success",
            http_status=http_status,
            tokens_used=tokens_used,
            metadata={"candidate_count": len(candidates), "model": self.settings.qwen_model},
        )

        content = payload["choices"][0]["message"]["content"]
        return _parse_json_content(content)

    def _quota_exceeded(self, provider: str, daily_quota: int | None, monthly_quota: int | None) -> bool:
        if not self.db or (daily_quota is None and monthly_quota is None):
            return False
        today = datetime.now(UTC).date()
        try:
            if (
                daily_quota is not None
                and self.db.get_api_call_count(provider, today.isoformat(), today.isoformat()) >= daily_quota
            ):
                return True
            month_start = today.replace(day=1)
            if (
                monthly_quota is not None
                and self.db.get_api_call_count(provider, month_start.isoformat(), today.isoformat()) >= monthly_quota
            ):
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _log_api_call(self, **kwargs: object) -> None:
        if not self.db:
            return
        try:
            self.db.log_api_call(**kwargs)
        except Exception:  # noqa: BLE001
            return


def _parse_json_content(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
