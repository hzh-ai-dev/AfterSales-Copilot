from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=8, max_length=80)


class MessageRequest(StartRequest):
    session_id: str = Field(min_length=1, max_length=80)


class ReviewDecision(StrictModel):
    decision: Literal["approve", "reject", "request_info"]
    reason: str = Field(min_length=1, max_length=1000)


class Intent(StrictModel):
    order_id: str | None = None
    user_intent: Literal["refund", "exchange", "unknown"] = "unknown"
    reason: str = Field(default="", max_length=2000)
    defect: bool = False
    dispute: bool = False


class ToolArguments(StrictModel):
    order_id: str = Field(pattern=r"^A\d{4}$")
    policy_id: str | None = None
    request_type: Literal["refund", "exchange"] | None = None


class ToolCall(StrictModel):
    name: Literal["query_order", "query_policy", "query_service_history"]
    arguments: ToolArguments


class EvidenceAnalysis(StrictModel):
    summary: str = Field(max_length=1500)
    evidence_ids: list[str] = Field(max_length=12)
    possible_conflict: bool = False
    missing_information: list[str] = Field(default_factory=list, max_length=3)


class Reply(StrictModel):
    explanation: str = Field(max_length=1000)
    evidence_ids: list[str] = Field(max_length=12)
