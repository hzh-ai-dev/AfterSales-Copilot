from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings
from backend.runtime import Runtime
from backend.schemas import MessageRequest, ReviewDecision, StartRequest


def create_app(settings=None, model=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = Runtime(settings, model)
        yield
        app.state.runtime.close()

    app = FastAPI(title="AfterSales Copilot", version="1.0.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        accounts = {
            settings.user_token: "user-1",
            settings.other_token: "user-2",
            settings.admin_token: "admin",
        }
        if not credentials or credentials.credentials not in accounts:
            raise HTTPException(401, "请提供有效的演示账户令牌")
        return accounts[credentials.credentials]

    def admin(user=Depends(identity)):
        if user != "admin":
            raise HTTPException(403, "需要管理员权限")
        return user

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": settings.model_mode, "simulation_only": True}

    @app.post("/chat/start")
    def start(body: StartRequest, user=Depends(identity)):
        return app.state.runtime.start(user, body)

    @app.post("/chat/message")
    def message(body: MessageRequest, user=Depends(identity)):
        return app.state.runtime.message(user, body)

    @app.get("/sessions")
    def sessions(user=Depends(identity)):
        runtime = app.state.runtime
        query = "SELECT id,created_at,status,initial_message FROM sessions"
        args = ()
        if user != "admin":
            query += " WHERE user_id=?"
            args = (user,)
        return runtime.store.all(query + " ORDER BY created_at DESC LIMIT 100", args)

    @app.get("/session/{session_id}")
    def session(session_id: str, user=Depends(identity)):
        return app.state.runtime.view(session_id, user)

    @app.post("/session/{session_id}/retry")
    def retry(session_id: str, user=Depends(identity)):
        return app.state.runtime.retry(session_id, user)

    @app.get("/orders")
    def orders(user=Depends(identity)):
        if user == "admin":
            raise HTTPException(403, "请使用用户账户查询订单")
        return app.state.runtime.store.all(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id", (user,)
        )

    @app.get("/reviews/pending")
    def pending(user=Depends(admin)):
        return app.state.runtime.pending_reviews()

    @app.post("/reviews/{review_id}/decision")
    def decision(review_id: str, body: ReviewDecision, user=Depends(admin)):
        return app.state.runtime.decide(review_id, body)

    return app


app = create_app()
