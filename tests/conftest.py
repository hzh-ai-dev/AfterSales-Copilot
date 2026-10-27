from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings

USER = {"Authorization": "Bearer demo-user-token"}
OTHER = {"Authorization": "Bearer demo-other-token"}
ADMIN = {"Authorization": "Bearer demo-admin-token"}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path)


@pytest.fixture
def client(settings):
    from backend.main import create_app

    with TestClient(create_app(settings)) as client:
        yield client


def start(client, message, headers=USER, request_id=None):
    return client.post(
        "/chat/start",
        headers=headers,
        json={"message": message, "request_id": request_id or str(uuid4())},
    )


def message(client, sid, text, request_id=None):
    return client.post(
        "/chat/message",
        headers=USER,
        json={"session_id": sid, "message": text, "request_id": request_id or str(uuid4())},
    )


def decide(client, rid, decision="approve", reason="依据政策核实完成"):
    return client.post(
        f"/reviews/{rid}/decision", headers=ADMIN, json={"decision": decision, "reason": reason}
    )
