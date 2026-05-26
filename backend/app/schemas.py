from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CandidateSource = Literal["memory", "standard", "poi", "map_api", "qwen"]


class AddressCandidate(BaseModel):
    source: CandidateSource
    candidate_id: str
    name: str | None = None
    full_address: str
    province: str | None = None
    city: str | None = None
    district: str | None = None
    town: str | None = None
    category: str | None = None
    lon: float | None = None
    lat: float | None = None
    score: float = 0
    evidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedAddress(BaseModel):
    input: str
    cleaned_input: str
    normalized_address: str
    output_line: str
    components: dict[str, Any] = Field(default_factory=dict)
    anchor_type: str
    anchor_id: str | None = None
    source: str
    confidence: float
    match_level: str
    candidates: list[AddressCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_model_output: dict[str, Any] | None = None


class NormalizeBatchRequest(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=200)
    use_qwen: bool = True
    use_map_api: bool = True
    persist_job: bool = True
    auto_persist_memory: bool = False
    concurrency: int = Field(default=2, ge=1, le=8)


class NormalizeBatchResponse(BaseModel):
    results: list[NormalizedAddress]


class ConfirmFeedbackRequest(BaseModel):
    raw_address: str
    normalized_address: str
    components: dict[str, Any] = Field(default_factory=dict)
    anchor_type: str = "business_memory"
    anchor_id: str | None = None
    anchor_source: str | None = None
    lon: float | None = None
    lat: float | None = None
    confirmed_by: str | None = None


class ConfigStatus(BaseModel):
    database: str
    qwen: str
    mgeo: str
    map_api: str
    standard_address: str
    poi_rows: int
    memory_rows: int
    memory_alias_rows: int = 0
    memory_detail_rows: int = 0
    default_city: str
    map_api_calls_today: int = 0
    qwen_calls_today: int = 0


class ApiUsageDaily(BaseModel):
    date: str
    calls: int
    errors: int
    result_count: int


class ApiUsageSummary(BaseModel):
    provider: str
    start_date: str
    end_date: str
    total_calls: int
    daily: list[ApiUsageDaily] = Field(default_factory=list)
