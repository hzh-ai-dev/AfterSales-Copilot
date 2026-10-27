from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.model import DeepSeekModel, ModelError
from backend.schemas import Intent


def response(content=None, calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))]
    )


@pytest.fixture
def model(settings, monkeypatch):
    settings.api_key = "test-not-a-real-key"
    model = DeepSeekModel(settings)
    model.client.chat.completions.create = Mock()
    monkeypatch.setattr("backend.model.time.sleep", lambda _: None)
    yield model
    model.close()


def test_json_validation_and_correction(model):
    model.client.chat.completions.create.side_effect = [
        response("{bad json"),
        response('{"order_id":"A1002","user_intent":"refund","reason":"坏了"}'),
    ]
    result = model.intent({"messages": [{"role": "user", "content": "A1002坏了，退款"}]})
    assert isinstance(result, Intent)
    assert result.order_id == "A1002"
    assert model.client.chat.completions.create.call_count == 2


def test_invalid_enum_is_not_accepted(model):
    model.client.chat.completions.create.return_value = response('{"user_intent":"pay_money"}')
    with pytest.raises(ModelError):
        model.intent({"messages": []})


def test_real_function_call_protocol(model):
    call = SimpleNamespace(
        function=SimpleNamespace(name="query_policy", arguments='{"order_id":"A1002"}')
    )
    model.client.chat.completions.create.return_value = response(calls=[call])
    result = model.select_tools(
        {"order_id": "A1002", "collected_information": {}, "tool_results": []}
    )
    assert result[0].name == "query_policy"
    arguments = model.client.chat.completions.create.call_args.kwargs
    assert len(arguments["tools"]) == 3
    assert arguments["tools"][0]["type"] == "function"


def test_unknown_tool_is_rejected(model):
    call = SimpleNamespace(
        function=SimpleNamespace(name="execute_shell", arguments='{"order_id":"A1002"}')
    )
    model.client.chat.completions.create.return_value = response(calls=[call])
    with pytest.raises(ModelError):
        model.select_tools({"order_id": "A1002", "collected_information": {}})
