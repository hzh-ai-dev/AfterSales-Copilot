from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.model import DemoModel, ModelError
from conftest import ADMIN, OTHER, USER, decide, message, start


def test_a_missing_information_continues_same_session(client):
    first = start(client, "我的耳机只有一边有声音，我想换货").json()
    assert first["status"] == "waiting_user"
    assert first["tool_count"] == 0
    second = message(client, first["session_id"], "订单 A1002").json()
    assert second["session_id"] == first["session_id"]
    assert second["status"] == "completed"
    assert second["operation_result"]["code"] == "simulated_exchange"
    assert len(second["messages"]) == 4


def test_b_normal_refund_and_evidence(client):
    response = start(client, "A1002 耳机坏了，我要退款")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["operation_result"]["code"] == "simulated_refund"
    assert {e["kind"] for e in result["evidence"]} == {"order", "policy"}
    calls = [e["detail"]["name"] for e in result["events"] if e["node"] == "tool_call"]
    assert calls == ["query_order", "query_policy"]
    assert "未发生真实资金操作" in result["final_response"]


def test_c_dispute_requests_extra_tool_and_review(client):
    result = start(client, "A1001 耳机只有一边有声音，客服之前拒绝了，我要换货").json()
    assert result["status"] == "waiting_review"
    assert result["proposed_solution"]["conflict"] is True
    assert {e["kind"] for e in result["evidence"]} == {"order", "policy", "history"}
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 0
    finished = decide(client, result["pause"]["review_id"])
    assert finished.status_code == 200, finished.text
    assert finished.json()["operation_result"]["code"] == "simulated_exchange"


def test_review_survives_application_restart(settings):
    with TestClient(create_app(settings)) as first:
        result = start(first, "A1001 耳机坏了，客服拒绝换货，我要换货").json()
        sid, rid = result["session_id"], result["pause"]["review_id"]
    with TestClient(create_app(settings)) as second:
        restored = second.get("/session/" + sid, headers=USER).json()
        assert restored["status"] == "waiting_review"
        assert second.get("/reviews/pending", headers=ADMIN).json()[0]["id"] == rid
        assert decide(second, rid).json()["status"] == "completed"


def test_user_input_survives_restart(settings):
    with TestClient(create_app(settings)) as first:
        sid = start(first, "耳机坏了，我要退款").json()["session_id"]
    with TestClient(create_app(settings)) as second:
        result = message(second, sid, "订单 A1002").json()
        assert result["operation_result"]["code"] == "simulated_refund"


def test_rejection_and_request_information(client):
    result = start(client, "A1001 耳机坏了，客服拒绝换货，我想换货").json()
    rid = result["pause"]["review_id"]
    paused = decide(client, rid, "request_info", "请说明故障是否持续出现").json()
    assert paused["status"] == "waiting_user"
    result = message(client, result["session_id"], "是的，故障一直存在").json()
    assert result["status"] == "waiting_review"
    assert result["pause"]["review_id"] != rid
    rejected = decide(client, result["pause"]["review_id"], "reject", "需线下检查").json()
    assert rejected["status"] == "completed"
    assert rejected["operation_result"] is None
    assert "拒绝" in rejected["final_response"]


def test_permissions_and_no_order_information_leak(client):
    assert client.get("/reviews/pending", headers=USER).status_code == 403
    assert client.get("/orders").status_code == 401
    result = start(client, "A2001 耳机坏了，我要退款").json()
    assert result["proposed_solution"]["code"] == "order_unavailable"
    assert result["evidence"] == []
    assert client.get("/session/" + result["session_id"], headers=OTHER).status_code == 404


def test_idempotent_start_and_message(client):
    first = start(client, "耳机坏了，我要退款", request_id="start-0001").json()
    same = start(client, "耳机坏了，我要退款", request_id="start-0001").json()
    assert first["session_id"] == same["session_id"]
    assert start(client, "不同内容", request_id="start-0001").status_code == 409
    sid = first["session_id"]
    assert message(client, sid, "A1002", "message-001").status_code == 200
    assert message(client, sid, "A1002", "message-001").status_code == 200
    assert message(client, sid, "A1001", "message-001").status_code == 409
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 1


def test_concurrent_review_only_updates_once(client):
    result = start(client, "A1001 耳机坏了，客服拒绝了，我要换货").json()
    rid = result["pause"]["review_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: decide(client, rid), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 1
    assert decide(client, rid, "reject", "改变决定").status_code == 409


def test_duplicate_order_in_new_session_is_not_applied_again(client):
    assert start(client, "A1002 耳机坏了，我要退款").json()["operation_result"]
    duplicate = start(client, "A1002 耳机坏了，我要退款").json()
    assert duplicate["proposed_solution"]["allowed"] is False
    assert duplicate["operation_result"] is None


def test_outside_warranty_is_not_approved_by_llm(client):
    result = start(client, "A1003 耳机坏了，我要换货").json()
    assert result["proposed_solution"]["allowed"] is False
    assert result["status"] == "completed"
    assert result["operation_result"] is None


def test_model_failure_is_recoverable(settings):
    class FailsOnce(DemoModel):
        failed = False

        def intent(self, state):
            if not self.failed:
                self.failed = True
                raise ModelError("simulated API failure")
            return super().intent(state)

    with TestClient(create_app(settings, FailsOnce())) as client:
        response = start(client, "A1002 耳机坏了，我要退款")
        assert response.status_code == 503
        sid = response.json()["detail"]["session_id"]
        assert client.get("/session/" + sid, headers=USER).json()["status"] == "error"
        retried = client.post("/session/" + sid + "/retry", headers=USER)
        assert retried.status_code == 200, retried.text
        assert retried.json()["operation_result"]["code"] == "simulated_refund"


def test_response_failure_does_not_undo_business(settings):
    class BrokenReply(DemoModel):
        def reply(self, state):
            raise ModelError("simulated API failure")

    with TestClient(create_app(settings, BrokenReply())) as client:
        result = start(client, "A1002 耳机坏了，我要退款").json()
        assert result["response_degraded"] is True
        assert result["status"] == "completed"
        assert result["operation_result"]["code"] == "simulated_refund"


def test_crash_after_business_commit_recovers_without_duplicate(settings):
    with TestClient(create_app(settings)) as first:
        runtime = first.app.state.runtime
        original = runtime.store.apply_operation

        def crash_after_commit(state, kind):
            original(state, kind)
            raise RuntimeError("crash after commit before checkpoint")

        runtime.store.apply_operation = crash_after_commit
        response = start(first, "A1002 耳机坏了，我要退款")
        assert response.status_code == 503
        sid = response.json()["detail"]["session_id"]
    with TestClient(create_app(settings)) as second:
        recovered = second.post("/session/" + sid + "/retry", headers=USER)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["operation_result"]["code"] == "simulated_refund"
        assert second.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 1


def test_bounded_questions(client):
    result = start(client, "你好").json()
    sid = result["session_id"]
    for _ in range(3):
        result = message(client, sid, "不知道").json()
    assert result["status"] == "completed"
    assert result["proposed_solution"]["code"] == "question_limit"


def test_bounded_tool_planning(settings):
    class RepeatsTool(DemoModel):
        def select_tools(self, state):
            from backend.schemas import ToolArguments, ToolCall

            return [
                ToolCall(name="query_order", arguments=ToolArguments(order_id=state["order_id"]))
            ]

    with TestClient(create_app(settings, RepeatsTool())) as client:
        result = start(client, "A1002 耳机坏了，我要退款").json()
        assert result["status"] == "waiting_review"
        assert result["tool_count"] == 1
        assert result["proposed_solution"]["code"] == "insufficient_evidence"
        finished = decide(client, result["pause"]["review_id"]).json()
        assert finished["operation_result"] is None


def test_tool_arguments_cannot_change_order(settings):
    class WrongOrder(DemoModel):
        def select_tools(self, state):
            from backend.schemas import ToolArguments, ToolCall

            return [ToolCall(name="query_order", arguments=ToolArguments(order_id="A2001"))]

    with TestClient(create_app(settings, WrongOrder())) as client:
        response = start(client, "A1002 耳机坏了，我要退款")
        assert response.status_code == 503
        assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 0


def test_policy_tool_result_changes_decision(client):
    import json

    store = client.app.state.runtime.store
    policy = store.one("SELECT * FROM policies LIMIT 1")
    rules = json.loads(policy["rules"])
    rules["refund_days"] = 1
    with store.connect() as db:
        db.execute("UPDATE policies SET rules=? WHERE id=?", (json.dumps(rules), policy["id"]))
    result = start(client, "A1002 耳机坏了，我要退款").json()
    assert result["status"] == "completed"
    assert result["proposed_solution"]["allowed"] is False
    assert result["operation_result"] is None


def test_high_amount_requires_approval(client):
    with client.app.state.runtime.store.connect() as db:
        db.execute("UPDATE orders SET amount_cents=30000 WHERE id='A1002'")
    result = start(client, "A1002 耳机坏了，我要退款").json()
    assert result["status"] == "waiting_review"
    assert result["proposed_solution"]["allowed"] is True
    assert (
        decide(client, result["pause"]["review_id"]).json()["operation_result"]["code"]
        == "simulated_refund"
    )


def test_changed_order_during_review_is_not_updated(client):
    result = start(client, "A1001 耳机坏了，客服拒绝，我要换货").json()
    with client.app.state.runtime.store.connect() as db:
        db.execute("UPDATE orders SET status='cancelled' WHERE id='A1001'")
    response = decide(client, result["pause"]["review_id"]).json()
    assert response["operation_result"]["code"] == "revalidation_failed"
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 0


def test_unknown_evidence_cannot_authorize_resolution(settings):
    class InventedEvidence(DemoModel):
        def analyze(self, state):
            from backend.schemas import EvidenceAnalysis

            return EvidenceAnalysis(summary="编造", evidence_ids=["policy:not-exists"])

    with TestClient(create_app(settings, InventedEvidence())) as client:
        assert start(client, "A1002 耳机坏了，我要退款").status_code == 503
        assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 0


def test_evidence_can_trigger_additional_question(settings):
    class Clarifies(DemoModel):
        def analyze(self, state):
            result = super().analyze(state)
            if not any("持续" in m["content"] for m in state["messages"] if m["role"] == "user"):
                result.missing_information = ["故障是否持续出现？"]
            return result

    with TestClient(create_app(settings, Clarifies())) as client:
        first = start(client, "A1002 耳机坏了，我要换货").json()
        assert first["status"] == "waiting_user"
        assert first["tool_count"] == 2
        second = message(client, first["session_id"], "故障持续出现").json()
        assert second["status"] == "completed"
        assert second["operation_result"]["code"] == "simulated_exchange"


def test_approval_does_not_authorize_changed_amount(client):
    result = start(client, "A1001 耳机坏了，客服拒绝，我要换货").json()
    with client.app.state.runtime.store.connect() as db:
        db.execute("UPDATE orders SET amount_cents=50000 WHERE id='A1001'")
    response = decide(client, result["pause"]["review_id"]).json()
    assert response["operation_result"]["code"] == "revalidation_failed"
