import json
import sqlite3
import threading
from uuid import uuid4

from fastapi import HTTPException
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from backend.graph import build_graph
from backend.model import build_model
from backend.storage import Store, encode, now


class Runtime:
    """Single-process local runner. Journal + checkpoints recover interrupted commands."""

    def __init__(self, settings, model=None):
        self.settings = settings
        self.store = Store(settings.data_dir / "business.db")
        self.model = model or build_model(settings)
        self.connection = sqlite3.connect(
            settings.data_dir / "checkpoints.db", check_same_thread=False
        )
        self.saver = SqliteSaver(self.connection)
        self.graph = build_graph(self.store, self.model, self.saver, settings)
        self.lock = threading.RLock()

    def close(self):
        self.connection.close()
        self.model.close()

    def config(self, sid):
        return {"configurable": {"thread_id": sid}, "recursion_limit": 80}

    def authorize(self, sid, user):
        row = self.store.session(sid)
        if not row or (user != "admin" and row["user_id"] != user):
            raise HTTPException(404, "会话不存在或无权访问")
        return row

    def snapshot(self, sid):
        return self.graph.get_state(self.config(sid))

    @staticmethod
    def interrupts(snapshot):
        return [item for task in snapshot.tasks for item in task.interrupts]

    def view(self, sid, user):
        with self.lock:
            row = self.authorize(sid, user)
            snapshot = self.snapshot(sid)
            state = dict(snapshot.values)
            interruptions = self.interrupts(snapshot)
            pause = interruptions[0].value if interruptions else None
            if row["status"] == "error":
                status = "error"
            elif pause:
                status = "waiting_user" if pause["type"] == "user_input" else "waiting_review"
            else:
                status = "completed" if state.get("final_response") else "running"
            return {
                "session_id": sid,
                "status": status,
                "mode": self.model.mode,
                "messages": state.get("messages", []),
                "pause": pause,
                "current_nodes": list(snapshot.next),
                "final_response": state.get("final_response", ""),
                "model_explanation": state.get("model_explanation", ""),
                "response_degraded": state.get("response_degraded", False),
                "proposed_solution": state.get("proposed_solution"),
                "operation_result": state.get("operation_result"),
                "evidence": state.get("evidence", state.get("tool_results", [])),
                "tool_count": state.get("tool_count", 0),
                "retry_count": self.store.one(
                    "SELECT COUNT(*) AS n FROM workflow_events WHERE session_id=? AND node='manual_retry'",
                    (sid,),
                )["n"],
                "events": [
                    {**e, "detail": json.loads(e["detail"])}
                    for e in self.store.all(
                        "SELECT * FROM workflow_events WHERE session_id=? ORDER BY id LIMIT 500",
                        (sid,),
                    )
                ],
            }

    def drive_pending(self, sid):
        row = self.store.session(sid)
        if not row["pending"]:
            return
        pending = json.loads(row["pending"])
        snapshot = self.snapshot(sid)
        pauses = self.interrupts(snapshot)
        try:
            if pending["kind"] == "start" and not snapshot.values:
                self.graph.invoke(pending["input"], self.config(sid))
            elif pauses and pending.get("interrupt_id") == pauses[0].id:
                self.graph.invoke(Command(resume=pending["payload"]), self.config(sid))
            elif not pauses and snapshot.next:
                # The command was already consumed before a node failed or the process exited.
                self.graph.invoke(None, self.config(sid))
            snapshot = self.snapshot(sid)
            pauses = self.interrupts(snapshot)
            status = (
                ("waiting_user" if pauses[0].value["type"] == "user_input" else "waiting_review")
                if pauses
                else "completed"
            )
            self.store.sync(sid, status)
        except Exception as exc:
            with self.store.connect() as db:
                db.execute("UPDATE sessions SET status=? WHERE id=?", ("error", sid))
            self.store.event(
                sid,
                "runtime_error",
                {"type": type(exc).__name__, "message": "执行失败，状态已保存，可点击重试。"},
            )
            # The actual exception remains local; provider responses/keys are not returned.
            raise HTTPException(
                503, {"message": "执行失败，状态已保存，可重试。", "session_id": sid}
            ) from exc

    def start(self, user, body):
        if user == "admin":
            raise HTTPException(403, "请使用用户账户提交申请")
        with self.lock:
            previous = self.store.one(
                "SELECT * FROM sessions WHERE user_id=? AND request_id=?", (user, body.request_id)
            )
            if previous:
                if previous["initial_message"] != body.message:
                    raise HTTPException(409, "相同 request_id 不得提交不同内容")
                self.drive_pending(previous["id"])
                return self.view(previous["id"], user)
            sid = str(uuid4())
            initial = {
                "session_id": sid,
                "user_id": user,
                "messages": [{"role": "user", "content": body.message}],
                "tool_results": [],
                "evidence": [],
                "evaluation_time": now(),
                "tool_count": 0,
                "question_count": 0,
                "planning_count": 0,
                "retry_count": 0,
                "review_round": 0,
            }
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                    (
                        sid,
                        user,
                        body.request_id,
                        body.message,
                        now(),
                        "running",
                        encode({"kind": "start", "input": initial}),
                    ),
                )
            self.drive_pending(sid)
            return self.view(sid, user)

    def message(self, user, body):
        with self.lock:
            row = self.authorize(body.session_id, user)
            previous = self.store.one(
                "SELECT * FROM commands WHERE session_id=? AND request_id=?",
                (body.session_id, body.request_id),
            )
            if previous:
                if previous["message"] != body.message:
                    raise HTTPException(409, "相同 request_id 不得提交不同内容")
                self.drive_pending(body.session_id)
                return self.view(body.session_id, user)
            if row["pending"]:
                raise HTTPException(409, "会话有未完成操作，请先重试")
            pauses = self.interrupts(self.snapshot(body.session_id))
            if not pauses or pauses[0].value["type"] != "user_input":
                raise HTTPException(409, "当前会话未等待用户补充；已完成的申请请新建会话")
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO commands VALUES (?,?,?)",
                    (body.session_id, body.request_id, body.message),
                )
                db.execute(
                    "UPDATE sessions SET pending=?,status=? WHERE id=?",
                    (
                        encode(
                            {
                                "kind": "message",
                                "interrupt_id": pauses[0].id,
                                "payload": {"message": body.message},
                            }
                        ),
                        "running",
                        body.session_id,
                    ),
                )
            self.drive_pending(body.session_id)
            return self.view(body.session_id, user)

    def pending_reviews(self):
        return [
            {**r, "payload": json.loads(r["payload"])}
            for r in self.store.all(
                "SELECT * FROM reviews WHERE status='pending' ORDER BY created_at"
            )
        ]

    def decide(self, review_id, body):
        with self.lock:
            row = self.store.one("SELECT * FROM reviews WHERE id=?", (review_id,))
            if not row:
                raise HTTPException(404, "审核记录不存在")
            if row["status"] != "pending":
                if row["decision"] != body.decision or row["reason"] != body.reason:
                    raise HTTPException(409, "该审核已处理，不能覆盖原决定")
                self.drive_pending(row["session_id"])
                return self.view(row["session_id"], "admin")
            sid = row["session_id"]
            pauses = self.interrupts(self.snapshot(sid))
            if not pauses or pauses[0].value.get("review_id") != review_id:
                raise HTTPException(409, "工作流尚未处于对应审核中断，请先恢复会话")
            with self.store.connect() as db:
                db.execute(
                    "UPDATE reviews SET status=?,decision=?,reason=?,reviewer=? WHERE id=? AND status=?",
                    ("decided", body.decision, body.reason, "admin", review_id, "pending"),
                )
                db.execute(
                    "UPDATE sessions SET pending=?,status=? WHERE id=?",
                    (
                        encode(
                            {
                                "kind": "review",
                                "interrupt_id": pauses[0].id,
                                "payload": body.model_dump(),
                            }
                        ),
                        "running",
                        sid,
                    ),
                )
            self.drive_pending(sid)
            return self.view(sid, "admin")

    def retry(self, sid, user):
        with self.lock:
            row = self.authorize(sid, user)
            if not row["pending"]:
                return self.view(sid, user)
            retries = self.store.one(
                "SELECT COUNT(*) AS n FROM workflow_events WHERE session_id=? AND node='manual_retry'",
                (sid,),
            )["n"]
            if retries >= 3:
                raise HTTPException(409, "已达到手动重试上限，请检查配置或联系管理员")
            # Do not use update_state here: it can reroute past a failed node.
            self.store.event(sid, "manual_retry", {"attempt": retries + 1})
            self.drive_pending(sid)
            return self.view(sid, user)
