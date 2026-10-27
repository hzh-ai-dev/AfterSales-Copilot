from backend.schemas import ToolArguments, ToolCall
from backend.storage import Store, now
from backend.tools import BusinessTools, ToolError


def test_seed_and_transactional_operation(tmp_path):
    store = Store(tmp_path / "business.db")
    assert len(store.all("SELECT * FROM orders")) == 4
    sid = "storage-test"
    with store.connect() as db:
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            (sid, "user-1", "request-001", "退款", now(), "running", None),
        )
    state = {"session_id": sid, "order_id": "A1002", "user_id": "user-1"}
    first = store.apply_operation(state, "refund")
    assert first["code"] == "simulated_refund"
    assert store.apply_operation(state, "refund") == first
    assert store.one("SELECT COUNT(*) n FROM operations")["n"] == 1
    assert store.one("SELECT status FROM orders WHERE id='A1002'")["status"] == "refunded"
    assert len(Store(tmp_path / "business.db").all("SELECT * FROM orders")) == 4


def test_tools_ownership_and_policy_lookup(tmp_path):
    store = Store(tmp_path / "business.db")
    tools = BusinessTools(store)
    import pytest

    with pytest.raises(ToolError):
        tools.execute(
            ToolCall(name="query_order", arguments=ToolArguments(order_id="A2001")), "user-1"
        )
    order = tools.execute(
        ToolCall(name="query_order", arguments=ToolArguments(order_id="A1001")), "user-1"
    )
    assert "user_id" not in order["data"]
    policy = tools.execute(
        ToolCall(
            name="query_policy",
            arguments=ToolArguments(
                order_id="A1001", request_type="exchange", policy_id="P-HEADPHONE-V1"
            ),
        ),
        "user-1",
    )
    assert policy["data"][0]["rules"]["defect_exchange_days"] == 30
    history = tools.execute(
        ToolCall(name="query_service_history", arguments=ToolArguments(order_id="A1001")), "user-1"
    )
    assert history["data"][0]["decision"] == "reject_exchange"
