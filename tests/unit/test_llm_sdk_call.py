"""The real SDK call path (not a fake ``call``) against a mocked HTTP transport: guards against SDK
signature changes such as the removal of ``temperature`` in anthropic 1.x (CLAUDE.md rule 3)."""

import json

import anthropic
import httpx2 as httpx

from app.llm.gateway import _sdk_parse
from app.llm.schemas import AssistantAnswer


def test_sdk_parse_sends_temperature_zero_and_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        answer = {"answer": "ok", "facts_used": [], "caveats": [], "not_available": []}
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": json.dumps(answer)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 7},
            },
        )

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    parsed, tin, tout = _sdk_parse("claude-sonnet-4-6", "sys", "question", AssistantAnswer, 0, 100, client=client)
    assert parsed.answer == "ok" and (tin, tout) == (12, 7)
    body = seen["body"]
    assert body["temperature"] == 0 and body["model"] == "claude-sonnet-4-6" and body["max_tokens"] == 100
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_truncated_json_is_reported_as_max_tokens_not_a_raw_validation_error():
    import pytest

    from app.llm.gateway import LlmUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": '{"answer": "Le CAC 40 r'}],  # cut (22/10/2026 review)
                "stop_reason": "max_tokens",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 100},
            },
        )

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LlmUnavailable, match="max_tokens = 100"):
        _sdk_parse("claude-sonnet-4-6", "sys", "question", AssistantAnswer, 0, 100, client=client)
