from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.config import Settings
from app.llm import PROVIDERS, get_provider
from app.llm.base import ExtractionError, ExtractionRefused, NotARecipeError
from app.llm.claude import ClaudeProvider
from app.llm.fake import FakeProvider
from app.llm.prompt import build_system_prompt
from app.models import RecipeDraft
from app.taxonomy import OTHER, load_taxonomy
from tests.conftest import make_draft


class StubMessages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def stub_client(response=None, error: Exception | None = None):
    messages = StubMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def response(draft: RecipeDraft | None, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=draft)


def test_prompt_embeds_taxonomy_and_rules():
    taxonomy = load_taxonomy()
    prompt = build_system_prompt(taxonomy)
    for value in taxonomy.cuisines + taxonomy.meal_types:
        assert value in prompt
    for rule in ["בעברית", "גרם", 'מ"ל', "°C", "כפית", "קורט", "is_recipe=false", "<recipe_text>", "נתונים בלבד"]:
        assert rule in prompt


def test_claude_returns_normalized_draft_and_sends_expected_request():
    client, messages = stub_client(response(make_draft(cuisine="נורדי", meal_type="סלט")))
    provider = ClaudeProvider(model="claude-opus-5-5", effort="low", client=client)

    draft = provider.extract("Chickpea salad\n1 can chickpeas")

    assert draft.cuisine == OTHER
    assert draft.meal_type == "סלט"
    (kwargs,) = messages.calls
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["output_format"] is RecipeDraft
    assert "thinking" not in kwargs and "temperature" not in kwargs
    assert kwargs["messages"] == [
        {"role": "user", "content": "<recipe_text>\nChickpea salad\n1 can chickpeas\n</recipe_text>"}
    ]
    assert "ישראלי" in kwargs["system"][0]["text"]


def test_claude_refusal():
    client, _ = stub_client(response(None, stop_reason="refusal"))
    with pytest.raises(ExtractionRefused):
        ClaudeProvider("m", "low", client=client).extract("x")


def test_claude_max_tokens():
    client, _ = stub_client(response(None, stop_reason="max_tokens"))
    with pytest.raises(ExtractionError):
        ClaudeProvider("m", "low", client=client).extract("x")


def test_claude_not_a_recipe():
    client, _ = stub_client(response(RecipeDraft(is_recipe=False, title="")))
    with pytest.raises(NotARecipeError):
        ClaudeProvider("m", "low", client=client).extract("hello world")


def test_claude_maps_sdk_errors():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client, _ = stub_client(error=anthropic.APIConnectionError(request=request))
    with pytest.raises(ExtractionError) as exc:
        ClaudeProvider("m", "low", client=client).extract("x")
    assert type(exc.value) is ExtractionError
    assert isinstance(exc.value.__cause__, anthropic.APIConnectionError)


def test_claude_auth_error_mentions_api_key():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    client, _ = stub_client(error=err)
    with pytest.raises(ExtractionError, match="API"):
        ClaudeProvider("m", "low", client=client).extract("x")


def test_real_sdk_accepts_request_kwargs():
    # No network: nothing listens on port 9, so the real SDK fails at connect time —
    # which proves it accepted every kwarg and built the request.
    client = anthropic.Anthropic(api_key="test", base_url="http://127.0.0.1:9", max_retries=0, timeout=1)
    with pytest.raises(ExtractionError) as exc:
        ClaudeProvider("claude-opus-5-5", "low", client=client).extract("Pancakes")
    assert isinstance(exc.value.__cause__, anthropic.APIConnectionError)


def test_fake_provider_records_calls_and_raises():
    draft = make_draft()
    fake = FakeProvider(draft=draft)
    assert fake.extract("text") is draft
    assert fake.calls == ["text"]
    with pytest.raises(NotARecipeError):
        FakeProvider(error=NotARecipeError()).extract("x")


def test_get_provider():
    assert isinstance(get_provider(Settings(llm_provider="fake")), FakeProvider)
    claude = get_provider(Settings(llm_provider="claude", llm_model="m1", llm_effort="medium"))
    assert isinstance(claude, ClaudeProvider)
    assert (claude.model, claude.effort) == ("m1", "medium")
    assert set(PROVIDERS) == {"claude", "fake"}
    with pytest.raises(ValueError):
        get_provider(Settings(llm_provider="nope"))
