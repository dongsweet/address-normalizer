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
        return await self._chat_completion(
            request_query=cleaned or raw,
            call_type="chat_completion",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={"candidate_count": len(candidates), "model": self.settings.qwen_model},
        )

    async def repair_cleaned_address(self, raw: str, cleaned: str) -> dict[str, Any] | None:
        if not self.settings.qwen_configured:
            return None

        system_prompt = (
            "你是地址清洗修复器。"
            "请从原始文本中提取可用于地址规范化的地址主体，并保留楼栋、单元、楼层、房号等必要细节。"
            "删除配送备注、动作描述、联系说明、礼貌用语。"
            "不能虚构地址；如果文本里没有明确地址，请返回 has_address=false。"
            "只输出 JSON。"
        )
        user_prompt = {
            "raw_address": raw,
            "local_cleaned_address": cleaned,
            "rules": [
                "优先保留可定位的主地名，再拼接楼栋/楼层/房号等细节",
                "像“放前台”“送到门口”“打电话”这类备注不要保留",
                "若本地清洗已经合理，可以直接复用或轻微修正",
            ],
            "output_schema": {
                "has_address": "boolean",
                "cleaned_address": "string|null",
                "anchor_text": "string|null",
                "detail_text": "string|null",
                "removed_notes": ["string"],
                "confidence": "0-1 number",
                "reason": "short Chinese explanation",
            },
        }
        return await self._chat_completion(
            request_query=cleaned or raw,
            call_type="clean_repair",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={"model": self.settings.qwen_model},
        )

    async def _chat_completion(
        self,
        *,
        request_query: str,
        call_type: str,
        system_prompt: str,
        user_prompt: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._quota_exceeded("qwen", self.settings.qwen_daily_quota, self.settings.qwen_monthly_quota):
            logger.warning("qwen api quota exceeded, skipping chat completion")
            self._log_api_call(
                provider="qwen",
                call_type=call_type,
                request_query=request_query,
                response_status="quota_exceeded",
                metadata=metadata,
            )
            return None

        endpoint = self._chat_endpoint()
        request_payload = {
            "model": self.settings.qwen_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ],
        }
        headers = {}
        if self.settings.qwen_api_key:
            headers["Authorization"] = f"Bearer {self.settings.qwen_api_key}"
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
                call_type=call_type,
                request_query=request_query,
                response_status="error",
                http_status=http_status,
                error_message=str(exc),
                metadata=metadata,
            )
            raise

        usage = payload.get("usage") or {}
        tokens_used = _to_int(usage.get("total_tokens"))
        self._log_api_call(
            provider="qwen",
            call_type=call_type,
            request_query=request_query,
            response_status="success",
            http_status=http_status,
            tokens_used=tokens_used,
            metadata=metadata,
        )
        content = payload["choices"][0]["message"]["content"]
        return _parse_json_content(content)

    def _chat_endpoint(self) -> str:
        base_url = self.settings.qwen_base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

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
