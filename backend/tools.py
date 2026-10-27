import json

from backend.schemas import ToolCall


class ToolError(ValueError):
    pass


class BusinessTools:
    """All query tools enforce ownership; identity never comes from the model."""

    def __init__(self, store):
        self.store = store

    def execute(self, call: ToolCall, user_id: str):
        order = self.store.one(
            "SELECT * FROM orders WHERE id=? AND user_id=?", (call.arguments.order_id, user_id)
        )
        if not order:
            raise ToolError("无法查询该订单，请确认订单编号及所属账户。")
        if call.name == "query_order":
            public_order = {k: v for k, v in order.items() if k != "user_id"}
            return {"id": "order:" + order["id"], "kind": "order", "data": public_order}
        if call.name == "query_policy":
            rows = self.store.all("SELECT * FROM policies WHERE category=?", (order["category"],))
            if call.arguments.policy_id:
                rows = [r for r in rows if r["id"] == call.arguments.policy_id]
            for row in rows:
                row["rules"] = json.loads(row["rules"])
            if call.arguments.request_type:
                # One policy may cover both types; filter by applicable rule support.
                key = (
                    "refund_days"
                    if call.arguments.request_type == "refund"
                    else "defect_exchange_days"
                )
                rows = [row for row in rows if key in row["rules"]]
            return {"id": "policy:" + order["category"], "kind": "policy", "data": rows}
        rows = self.store.all("SELECT * FROM service_history WHERE order_id=?", (order["id"],))
        return {"id": "history:" + order["id"], "kind": "history", "data": rows}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "当前会话已确认的订单编号"},
                    "policy_id": {"type": "string", "description": "可选：政策编号"},
                    "request_type": {
                        "type": "string",
                        "enum": ["refund", "exchange"],
                        "description": "可选：售后类型，仅政策查询使用",
                    },
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    }
    for name, description in [
        ("query_order", "查询当前用户的订单、签收时间及保修期限。"),
        ("query_policy", "按当前订单商品类型查询本地模拟售后政策。"),
        ("query_service_history", "用户提及此前客服、拒绝或争议时查询历史客服记录。"),
    ]
]
