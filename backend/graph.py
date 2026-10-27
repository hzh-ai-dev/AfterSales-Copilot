import re

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.model import ModelError
from backend.policy import evaluate
from backend.schemas import ToolCall
from backend.state import AfterSalesState
from backend.tools import BusinessTools, ToolError


def build_graph(store, model, checkpointer, settings):
    tools = BusinessTools(store)

    def logged(name, fn):
        def run(state):
            store.event(state["session_id"], name, {"event": "entered"})
            result = fn(state)
            store.event(state["session_id"], name, {"event": "completed"})
            return result

        return run

    def analyze_intent(state):
        parsed = model.intent(state)
        old = state.get("order_id")
        retry_count = store.one(
            "SELECT COUNT(*) AS n FROM workflow_events WHERE session_id=? AND node='manual_retry'",
            (state["session_id"],),
        )["n"]
        update = {
            "order_id": parsed.order_id,
            "user_intent": parsed.user_intent,
            "retry_count": retry_count,
            "collected_information": parsed.model_dump(),
            "tool_error": "",
            "final_response": "",
            "next_action": "",
        }
        if old != parsed.order_id:
            update.update(tool_results=[], evidence=[])
        return update

    def check_information(state):
        info = state["collected_information"]
        missing = []
        if not state.get("order_id"):
            missing.append("订单编号（例如 A1001）")
        if state.get("order_id") and not re.fullmatch(r"A\d{4}", state["order_id"]):
            missing.append("有效的订单编号（格式 A 加四位数字）")
        if info["user_intent"] == "unknown":
            missing.append("希望退款还是换货")
        if not info["reason"].strip():
            missing.append("申请原因或故障现象")
        if missing and state.get("question_count", 0) >= settings.max_questions:
            return {
                "missing_information": missing,
                "next_action": "finish",
                "proposed_solution": {
                    "code": "question_limit",
                    "allowed": False,
                    "reason": "已达到追问上限，请核对信息后新建会话。",
                },
            }
        return {"missing_information": missing, "next_action": "ask" if missing else "tools"}

    def ask_user(state):
        question = "请补充：" + "、".join(state["missing_information"])
        answer = interrupt({"type": "user_input", "question": question})
        return {
            "messages": state["messages"]
            + [
                {"role": "assistant", "content": question},
                {"role": "user", "content": answer["message"]},
            ],
            "question_count": state.get("question_count", 0) + 1,
            "planning_count": 0,
        }

    def select_tools(state):
        if state.get("tool_count", 0) >= settings.max_tools or state.get("planning_count", 0) >= 6:
            return {
                "pending_calls": [],
                "next_action": "analyze",
                "tool_error": "已达到查询预算，未获得的证据不会被猜测。",
            }
        calls = model.select_tools(state)
        have = {r["kind"] for r in state.get("tool_results", [])}
        mapping = {
            "query_order": "order",
            "query_policy": "policy",
            "query_service_history": "history",
        }
        filtered = []
        for call in calls:
            if call.arguments.order_id != state["order_id"]:
                raise ModelError("模型工具参数与已确认订单不一致，已阻止执行。")
            kind = mapping[call.name]
            if kind not in have:
                filtered.append(call.model_dump())
                have.add(kind)
        return {
            "pending_calls": filtered[: settings.max_tools - state.get("tool_count", 0)],
            "planning_count": state.get("planning_count", 0) + 1,
            "next_action": "execute" if filtered else "analyze",
        }

    def execute_tools(state):
        results = list(state.get("tool_results", []))
        count = state.get("tool_count", 0)
        for raw in state["pending_calls"]:
            call = ToolCall.model_validate(raw)
            count += 1
            try:
                result = tools.execute(call, state["user_id"])
            except ToolError as exc:
                return {
                    "tool_count": count,
                    "tool_error": str(exc),
                    "next_action": "invalid",
                    "proposed_solution": {
                        "code": "order_unavailable",
                        "allowed": False,
                        "reason": str(exc),
                    },
                }
            results = [r for r in results if r["kind"] != result["kind"]] + [result]
            store.event(
                state["session_id"],
                "tool_call",
                {
                    "name": call.name,
                    "arguments": call.arguments.model_dump(),
                    "evidence_id": result["id"],
                    "summary": str(result["data"])[:600],
                },
            )
        return {"tool_results": results, "tool_count": count, "next_action": "analyze"}

    def analyze_evidence(state):
        have = {r["kind"] for r in state.get("tool_results", [])}
        needed = {"order", "policy"}
        if state["collected_information"]["dispute"]:
            needed.add("history")
        if (
            not needed.issubset(have)
            and state.get("planning_count", 0) < 6
            and state.get("tool_count", 0) < settings.max_tools
        ):
            return {"next_action": "tools"}
        analysis = model.analyze(state)
        ids = {r["id"] for r in state.get("tool_results", [])}
        if not set(analysis.evidence_ids).issubset(ids):
            raise ModelError("证据引用不存在，已阻止未经验证的分析。")
        if analysis.missing_information and state.get("question_count", 0) < settings.max_questions:
            return {
                "analysis": analysis.model_dump(),
                "evidence": state.get("tool_results", []),
                "missing_information": analysis.missing_information,
                "next_action": "ask",
            }
        return {
            "analysis": analysis.model_dump(),
            "evidence": state.get("tool_results", []),
            "next_action": "evaluate",
        }

    def evaluate_solution(state):
        decision = evaluate(state)
        # A missing service record query must not be silently treated as "no dispute".
        have = {r["kind"] for r in state.get("tool_results", [])}
        if state["collected_information"]["dispute"] and "history" not in have:
            decision.update(
                allowed=False,
                review=True,
                code="insufficient_evidence",
                reason="争议所需的历史记录尚未取得，转人工核实。",
            )
        if state.get("analysis", {}).get("possible_conflict"):
            decision["review"] = True
        if state.get("analysis", {}).get("missing_information"):
            decision.update(
                allowed=False,
                review=True,
                code="insufficient_information",
                reason="关键信息仍不完整且已达追问上限，转人工核实。",
            )
        if decision.get("review"):
            round_number = state.get("review_round", 0) + 1
            return {
                "proposed_solution": decision,
                "next_action": "review",
                "review_round": round_number,
                "review_id": f"{state['session_id']}:{round_number}",
            }
        return {
            "proposed_solution": decision,
            "next_action": "apply" if decision["allowed"] else "finish",
        }

    def prepare_review(state):
        store.review(
            state["review_id"],
            state["session_id"],
            {
                "proposed_solution": state["proposed_solution"],
                "evidence": state.get("evidence", []),
                "order_id": state["order_id"],
            },
        )
        return {"review_status": "pending"}

    def human_review(state):
        decision = interrupt(
            {
                "type": "human_review",
                "review_id": state["review_id"],
                "question": "申请存在争议或需要授权，等待管理员审核。",
            }
        )
        if decision["decision"] == "request_info":
            if state.get("question_count", 0) >= settings.max_questions:
                return {
                    "review_status": "request_info",
                    "next_action": "finish",
                    "proposed_solution": {
                        "code": "question_limit",
                        "allowed": False,
                        "reason": "补充信息次数已达上限，请联系人工客服。",
                    },
                }
            return {
                "review_status": "request_info",
                "next_action": "ask",
                "missing_information": [decision["reason"]],
            }
        approved = decision["decision"] == "approve"
        allowed = state["proposed_solution"]["allowed"]
        if approved and not allowed:
            reason = "人工审核已完成，但现有规则或证据不支持自动执行；需线下进一步核实。"
        elif not approved:
            reason = "管理员拒绝了本次申请：" + decision["reason"]
        else:
            reason = state["proposed_solution"]["reason"]
        return {
            "review_status": decision["decision"],
            "proposed_solution": {**state["proposed_solution"], "reason": reason},
            "next_action": "apply" if approved and allowed else "finish",
        }

    def apply_resolution(state):
        # Refresh order AND policy before a state mutation. Cached evidence is not authority.
        fresh = []
        for name in ["query_order", "query_policy"]:
            fresh.append(
                tools.execute(
                    ToolCall.model_validate(
                        {"name": name, "arguments": {"order_id": state["order_id"]}}
                    ),
                    state["user_id"],
                )
            )
        decision = evaluate({**state, "tool_results": fresh})
        existing = store.one(
            "SELECT result FROM operations WHERE session_id=?", (state["session_id"],)
        )
        needs_new_approval = decision.get("review") and state.get("review_status") != "approve"
        old_order = next((r["data"] for r in state["tool_results"] if r["kind"] == "order"), {})
        scope_changed = fresh[0]["data"].get("amount_cents") != old_order.get(
            "amount_cents"
        ) or decision.get("policy_version") != state["proposed_solution"].get("policy_version")
        if (not decision["allowed"] or needs_new_approval or scope_changed) and not existing:
            return {
                "proposed_solution": {
                    **decision,
                    "reason": "执行前订单或政策已变化，原处理方案已失效。",
                },
                "operation_result": {
                    "code": "revalidation_failed",
                    "message": "执行前校验未通过，未更新订单。",
                },
            }
        result = store.apply_operation(state, state["user_intent"])
        return {"operation_result": result}

    def generate_solution(state):
        degraded = False
        try:
            reply = model.reply(state)
            valid_ids = {r["id"] for r in state.get("evidence", [])}
            if not set(reply.evidence_ids).issubset(valid_ids):
                raise ModelError("回复引用未知证据")
            explanation = reply.explanation
        except ModelError:
            explanation, degraded = "", True
        # Confirmed business result is always rendered separately from model wording.
        decision = state.get("proposed_solution", {})
        response = decision.get("reason", "当前无法确定处理方案，请联系人工客服。")
        if state.get("operation_result"):
            response += "\n" + state["operation_result"]["message"]
        ids = [r["id"] for r in state.get("evidence", [])]
        if ids:
            response += "\n依据：" + "、".join(ids)
        return {
            "final_response": response,
            "model_explanation": explanation,
            "response_degraded": degraded,
            "next_action": "completed",
            "messages": state["messages"] + [{"role": "assistant", "content": response}],
        }

    graph = StateGraph(AfterSalesState)
    nodes = {
        "analyze_intent": analyze_intent,
        "check_information": check_information,
        "ask_user": ask_user,
        "select_tools": select_tools,
        "execute_tools": execute_tools,
        "analyze_evidence": analyze_evidence,
        "evaluate_solution": evaluate_solution,
        "prepare_review": prepare_review,
        "human_review": human_review,
        "apply_resolution": apply_resolution,
        "generate_solution": generate_solution,
    }
    for name, fn in nodes.items():
        graph.add_node(name, logged(name, fn))
    graph.add_edge(START, "analyze_intent")
    graph.add_edge("analyze_intent", "check_information")
    graph.add_conditional_edges(
        "check_information",
        lambda s: s["next_action"],
        {"ask": "ask_user", "tools": "select_tools", "finish": "generate_solution"},
    )
    graph.add_edge("ask_user", "analyze_intent")
    graph.add_conditional_edges(
        "select_tools",
        lambda s: s["next_action"],
        {"execute": "execute_tools", "analyze": "analyze_evidence"},
    )
    graph.add_conditional_edges(
        "execute_tools",
        lambda s: s["next_action"],
        {"invalid": "generate_solution", "analyze": "analyze_evidence"},
    )
    graph.add_conditional_edges(
        "analyze_evidence",
        lambda s: s["next_action"],
        {"tools": "select_tools", "evaluate": "evaluate_solution", "ask": "ask_user"},
    )
    graph.add_conditional_edges(
        "evaluate_solution",
        lambda s: s["next_action"],
        {"review": "prepare_review", "apply": "apply_resolution", "finish": "generate_solution"},
    )
    graph.add_edge("prepare_review", "human_review")
    graph.add_conditional_edges(
        "human_review",
        lambda s: s["next_action"],
        {"ask": "ask_user", "apply": "apply_resolution", "finish": "generate_solution"},
    )
    graph.add_edge("apply_resolution", "generate_solution")
    graph.add_edge("generate_solution", END)
    return graph.compile(checkpointer=checkpointer)
