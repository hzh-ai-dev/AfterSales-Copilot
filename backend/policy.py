from datetime import datetime


def evaluate(state):
    """Deterministic business gate; LLM suggestions never grant permissions."""
    results = {r["kind"]: r["data"] for r in state["tool_results"]}
    order = results.get("order")
    policies = results.get("policy", [])
    if not order or not policies:
        return {
            "code": "insufficient_evidence",
            "allowed": False,
            "reason": "缺少订单或适用政策，不能承诺售后资格。",
            "review": True,
        }
    policy = policies[0]
    rules = policy["rules"]
    intent = state["collected_information"]
    at = datetime.fromisoformat(state["evaluation_time"])
    days = (at - datetime.fromisoformat(order["delivered_at"])).total_seconds() / 86400
    code, reason, allowed, review = (
        "ineligible",
        "不符合当前模拟政策的退款或换货条件。",
        False,
        False,
    )
    if order["status"] != "delivered" or order["after_sales_status"] != "none":
        return {
            "code": "order_state",
            "allowed": False,
            "review": False,
            "reason": "该订单已处理售后或当前状态不支持申请。",
            "policy_id": policy["id"],
        }
    if intent["user_intent"] == "refund" and 0 <= days <= rules["refund_days"]:
        code, reason, allowed = "eligible_refund", "签收七天内，符合模拟退款条件。", True
        review = order["amount_cents"] > rules["auto_refund_cents"]
    elif (
        intent["user_intent"] == "exchange"
        and intent["defect"]
        and 0 <= days <= rules["defect_exchange_days"]
    ):
        code, reason, allowed = (
            "eligible_exchange",
            "质量故障三十天内符合模拟换货条件，外包装缺失不单独构成拒绝理由。",
            True,
        )
    elif intent["defect"] and at <= datetime.fromisoformat(order["warranty_until"]):
        code, reason = (
            "repair_suggestion",
            "不符合本次退款或换货条件，但仍在保修期内，建议咨询维修。",
        )
    history = results.get("history", [])
    conflict = bool(
        allowed
        and intent["user_intent"] == "exchange"
        and any(
            h["decision"] == "reject_exchange" and h["reason_code"] == "seven_days_or_packaging"
            for h in history
        )
    )
    if conflict:
        review = True
        reason += " 历史客服拒绝理由与政策存在冲突，须人工核实。"
    if intent["dispute"]:
        review = True
        reason += " 用户提出争议，交由人工核实。"
    return {
        "code": code,
        "allowed": allowed,
        "review": review,
        "reason": reason,
        "conflict": conflict,
        "policy_id": policy["id"],
        "policy_version": policy["version"],
        "elapsed_days": round(days, 3),
    }
