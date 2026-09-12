"""Exercise the provider adapter without credentials, a dependency or paid calls."""

import sys
from types import SimpleNamespace

import pytest

from kpi_maker.ai.client import AIUnavailable, Client, Refused


@pytest.fixture
def sdk(monkeypatch):
    calls = []
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text='{"ok":true}',
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=40),
        ),
    )

    def create(**kwargs):
        calls.append(kwargs)
        return response

    fake = SimpleNamespace(
        OpenAI=lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(create=create)
        ),
        APIError=type("APIError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setenv("MASTERBI_AI_MAX_CALL_USD", ".50")
    return response, calls


def request(client):
    return client.json(
        system="Be precise.",
        user="A small question.",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        purpose="test",
        max_tokens=1000,
    )


def test_responses_contract_and_cache_accounting(sdk):
    response, calls = sdk
    client = Client("gpt-5.6-luna")
    assert request(client) == {"ok": True}
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["model"] == "gpt-5.6-luna"
    assert client.usage.total == 130
    assert client.usage.input_tokens == 60
    assert client.usage.cache_read_tokens == 40


@pytest.mark.parametrize("status", ["incomplete", "failed"])
def test_incomplete_is_not_parsed(sdk, status):
    response, _ = sdk
    response.status = status
    response.output_text = '{"broken'
    client = Client("gpt-6-astra")
    with pytest.raises(AIUnavailable, match="incomplete"):
        request(client)
    assert len(client.calls) == 1


def test_refusal_before_json(sdk):
    response, _ = sdk
    response.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
    response.output_text = ""
    with pytest.raises(Refused):
        request(Client("gpt-5.6-luna"))


def test_spend_guard_and_model_allowlist(sdk, monkeypatch):
    _, calls = sdk
    monkeypatch.setenv("MASTERBI_AI_MAX_CALL_USD", ".001")
    with pytest.raises(AIUnavailable, match="per-call"):
        request(Client("gpt-6-astra"))
    assert calls == []
    with pytest.raises(AIUnavailable, match="Choose Standard"):
        Client("unpriced-model")
