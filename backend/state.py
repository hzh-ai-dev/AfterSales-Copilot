from typing import TypedDict


class AfterSalesState(TypedDict, total=False):
    session_id: str
    user_id: str
    messages: list[dict]
    order_id: str | None
    user_intent: str
    collected_information: dict
    missing_information: list[str]
    tool_results: list[dict]
    evidence: list[dict]
    proposed_solution: dict
    next_action: str
    review_status: str
    final_response: str
    retry_count: int
    tool_count: int
    question_count: int
    planning_count: int
    review_round: int
    review_id: str
    evaluation_time: str
    pending_calls: list[dict]
    tool_error: str
    analysis: dict
    operation_result: dict
    model_explanation: str
    response_degraded: bool
