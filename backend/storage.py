import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, product TEXT NOT NULL,
                    category TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                    purchased_at TEXT NOT NULL, delivered_at TEXT NOT NULL,
                    warranty_until TEXT NOT NULL, status TEXT NOT NULL,
                    after_sales_status TEXT NOT NULL DEFAULT 'none');
                CREATE TABLE IF NOT EXISTS policies (
                    id TEXT PRIMARY KEY, category TEXT NOT NULL, version TEXT NOT NULL,
                    content TEXT NOT NULL, rules TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS service_history (
                    id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(id),
                    content TEXT NOT NULL, decision TEXT NOT NULL, reason_code TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    initial_message TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, pending TEXT,
                    UNIQUE(user_id, request_id));
                CREATE TABLE IF NOT EXISTS commands (
                    session_id TEXT REFERENCES sessions(id), request_id TEXT NOT NULL,
                    message TEXT NOT NULL, PRIMARY KEY(session_id, request_id));
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
                    status TEXT NOT NULL, payload TEXT NOT NULL, decision TEXT,
                    reason TEXT, reviewer TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
                    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
                    kind TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, node TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            base = datetime.now(timezone.utc)
            for oid, user, days, amount in [
                ("A1001", "user-1", 12, 19900),
                ("A1002", "user-1", 3, 9900),
                ("A1003", "user-1", 400, 29900),
                ("A2001", "user-2", 2, 19900),
            ]:
                purchased = base - timedelta(days=days)
                db.execute(
                    "INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        user,
                        "无线耳机",
                        "headphones",
                        amount,
                        purchased.isoformat(),
                        purchased.isoformat(),
                        (purchased + timedelta(days=365)).isoformat(),
                        "delivered",
                        "none",
                    ),
                )
            rules = {
                "refund_days": 7,
                "defect_exchange_days": 30,
                "warranty_days": 365,
                "auto_refund_cents": 20000,
                "packaging_required_for_defect": False,
            }
            db.execute(
                "INSERT OR IGNORE INTO policies VALUES (?,?,?,?,?)",
                (
                    "P-HEADPHONE-V1",
                    "headphones",
                    "demo-v1",
                    "【模拟政策】签收七天内可申请退款；质量故障三十天内可申请换货，"
                    "缺少外包装不单独导致质量换货被拒；超过三十天但在一年保修期内可建议维修。"
                    "金额超过200元的退款须人工审批；存在政策争议时须人工核实。",
                    encode(rules),
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO service_history VALUES (?,?,?,?,?)",
                (
                    "H1001",
                    "A1001",
                    "客服：超过七天且包装盒丢失，不能换货。",
                    "reject_exchange",
                    "seven_days_or_packaging",
                ),
            )

    def one(self, sql, args=()):
        with self.connect() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def event(self, session_id, node, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO workflow_events(session_id,node,detail,created_at) VALUES (?,?,?,?)",
                (session_id, node, encode(detail), now()),
            )

    def session(self, sid):
        return self.one("SELECT * FROM sessions WHERE id=?", (sid,))

    def set_pending(self, sid, pending):
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET pending=?,status=? WHERE id=?",
                (encode(pending), "running", sid),
            )

    def sync(self, sid, status):
        with self.connect() as db:
            db.execute("UPDATE sessions SET status=?,pending=NULL WHERE id=?", (status, sid))

    def review(self, review_id, sid, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?,?,?)",
                (review_id, sid, "pending", encode(payload), None, None, None, now()),
            )

    def apply_operation(self, state, kind):
        sid, oid = state["session_id"], state["order_id"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM operations WHERE session_id=?", (sid,)).fetchone()
            if previous:
                return json.loads(previous["result"])
            order = db.execute(
                "SELECT * FROM orders WHERE id=? AND user_id=?", (oid, state["user_id"])
            ).fetchone()
            if not order or order["status"] != "delivered" or order["after_sales_status"] != "none":
                return {"code": "state_conflict", "message": "订单状态已变化，未执行售后操作。"}
            result = {
                "code": "simulated_refund" if kind == "refund" else "simulated_exchange",
                "message": "模拟退款已登记，未发生真实资金操作。"
                if kind == "refund"
                else "模拟换货申请已登记，未安排真实物流。",
            }
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,?)",
                (f"{kind}:{sid}", sid, oid, kind, encode(result), now()),
            )
            db.execute(
                "UPDATE orders SET status=?,after_sales_status=? WHERE id=?",
                ("refunded" if kind == "refund" else "delivered", kind + "_registered", oid),
            )
            return result
