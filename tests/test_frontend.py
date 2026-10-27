from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from conftest import USER, start

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def mount(monkeypatch, client):
    def request(method, url, json=None, headers=None, **kwargs):
        response = client.request(method, "/" + url.split("/", 3)[3], json=json, headers=headers)
        return SimpleNamespace(ok=response.is_success, json=response.json)

    monkeypatch.setattr("requests.request", request)
    return AppTest.from_file(str(APP), default_timeout=30).run()


def test_ui_multiturn_chat_and_evidence(monkeypatch, client):
    app = mount(monkeypatch, client)
    assert not app.exception
    app.chat_input[0].set_value("我的耳机坏了，我要退款").run()
    assert not app.exception
    assert any("请补充" in m.value for m in app.markdown)
    app.chat_input[0].set_value("订单 A1002").run()
    assert not app.exception
    assert any("未发生真实资金操作" in m.value for m in app.markdown)
    assert app.metric[0].value == "2"


def test_ui_admin_approval(monkeypatch, client):
    result = start(client, "A1001 耳机坏了，客服拒绝换货，我要换货").json()
    app = mount(monkeypatch, client)
    app.sidebar.selectbox[0].select("管理员").run()
    assert not app.exception
    app.text_input[0].set_value("核实政策，批准模拟换货")
    button = next(b for b in app.button if b.label == "提交审核")
    button.click().run()
    assert not app.exception
    view = client.get("/session/" + result["session_id"], headers=USER).json()
    assert view["operation_result"]["code"] == "simulated_exchange"


def test_ui_lost_response_reuses_original_request(monkeypatch, client):
    import requests

    lost = False

    def request(method, url, json=None, headers=None, **kwargs):
        nonlocal lost
        path = "/" + url.split("/", 3)[3]
        response = client.request(method, path, json=json, headers=headers)
        if path == "/chat/start" and not lost:
            lost = True
            raise requests.ConnectionError("simulated response loss after server commit")
        return SimpleNamespace(ok=response.is_success, json=response.json)

    monkeypatch.setattr("requests.request", request)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.chat_input[0].set_value("A1002 耳机坏了，我要退款").run()
    assert not app.exception
    next(b for b in app.button if b.label == "重试上一条请求").click().run()
    assert not app.exception
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM sessions")["n"] == 1
    assert client.app.state.runtime.store.one("SELECT COUNT(*) n FROM operations")["n"] == 1
