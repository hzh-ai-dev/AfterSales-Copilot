"""Real HTTP smoke test with separate backend/frontend processes and a restart."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    backend_port, frontend_port = free_port(), free_port()
    base = f"http://127.0.0.1:{backend_port}"
    user = {"Authorization": "Bearer demo-user-token"}
    admin = {"Authorization": "Bearer demo-admin-token"}
    children = []

    def stop(process):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
            )
            process.wait(timeout=15)
            return
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    with tempfile.TemporaryDirectory(prefix="aftersales-live-") as tmp:
        env = {
            **os.environ,
            "DATA_DIR": tmp,
            "MODEL_MODE": "demo",
            "API_BASE_URL": base,
            "DEMO_USER_TOKEN": "demo-user-token",
            "DEMO_OTHER_TOKEN": "demo-other-token",
            "DEMO_ADMIN_TOKEN": "demo-admin-token",
            "PYTHONUTF8": "1",
        }
        with open(Path(tmp) / "servers.log", "w", encoding="utf-8") as logs:

            def spawn(args):
                process = subprocess.Popen(
                    [sys.executable, "-m", *args],
                    cwd=ROOT,
                    env=env,
                    stdout=logs,
                    stderr=logs,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                children.append(process)
                return process

            def wait(url):
                for _ in range(120):
                    try:
                        if requests.get(url, timeout=1).ok:
                            return
                    except requests.RequestException:
                        pass
                    time.sleep(0.25)
                raise RuntimeError("Service did not become ready: " + url)

            def start(text, headers=user):
                response = requests.post(
                    base + "/chat/start",
                    headers=headers,
                    json={"message": text, "request_id": str(uuid4())},
                    timeout=30,
                )
                response.raise_for_status()
                return response.json()

            try:
                backend = spawn(
                    [
                        "uvicorn",
                        "backend.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(backend_port),
                    ]
                )
                wait(base + "/health")
                spawn(
                    [
                        "streamlit",
                        "run",
                        "frontend/app.py",
                        "--server.address",
                        "127.0.0.1",
                        "--server.port",
                        str(frontend_port),
                        "--server.headless",
                        "true",
                        "--browser.gatherUsageStats",
                        "false",
                    ]
                )
                wait(f"http://127.0.0.1:{frontend_port}/_stcore/health")
                a = start("耳机坏了，我想换货")
                assert a["status"] == "waiting_user"
                response = requests.post(
                    base + "/chat/message",
                    headers=user,
                    json={
                        "session_id": a["session_id"],
                        "message": "A1002",
                        "request_id": str(uuid4()),
                    },
                    timeout=30,
                )
                response.raise_for_status()
                assert response.json()["operation_result"]["code"] == "simulated_exchange"
                b = start("A2001 耳机坏了，我想退款", {"Authorization": "Bearer demo-other-token"})
                assert b["operation_result"]["code"] == "simulated_refund"
                c = start("A1001 耳机坏了，客服之前拒绝换货，我想换货")
                assert c["status"] == "waiting_review"
                stop(backend)
                # A venv launcher can have a child interpreter on Windows.
                # Prove the old listener is gone before starting a new one.
                for _ in range(60):
                    try:
                        requests.get(base + "/health", timeout=0.3)
                    except requests.RequestException:
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Old backend did not stop; restart verification is invalid")
                backend = spawn(
                    [
                        "uvicorn",
                        "backend.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(backend_port),
                    ]
                )
                wait(base + "/health")
                restored = requests.get(
                    base + "/session/" + c["session_id"], headers=user, timeout=10
                ).json()
                assert restored["status"] == "waiting_review"
                review_url = base + "/reviews/" + restored["pause"]["review_id"] + "/decision"
                result = requests.post(
                    review_url,
                    headers=admin,
                    json={"decision": "approve", "reason": "按模拟政策批准换货"},
                    timeout=30,
                )
                result.raise_for_status()
                assert result.json()["operation_result"]["code"] == "simulated_exchange"
                duplicate = requests.post(
                    review_url,
                    headers=admin,
                    json={"decision": "approve", "reason": "按模拟政策批准换货"},
                    timeout=30,
                )
                assert duplicate.status_code == 200
                report = {
                    "scenario_a": "passed",
                    "scenario_b": "passed",
                    "scenario_c": "passed",
                    "process_restart_resume": "passed",
                    "duplicate_review": "passed",
                    "streamlit_health": "passed",
                    "mode": "demo",
                }
            except BaseException:
                logs.flush()
                print((Path(tmp) / "servers.log").read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                for process in children:
                    if process.poll() is None:
                        stop(process)
                time.sleep(0.5)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
