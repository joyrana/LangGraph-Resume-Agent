from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.core.config import _read_dotenv, load_settings
from app.core.errors import ConfigurationError
from app.core.logging import JsonFormatter
from app.workflow.graph import SequentialGraph


def test_defaults_are_valid():
    s = load_settings({})
    assert s.deployment_mode == "local" and s.llm_temperature == 0.1


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"RESUME_DEPLOYMENT_MODE": "shared"}, "RESUME_SIGNING_SECRET"),
        ({"RESUME_DEPLOYMENT_MODE": "shared", "RESUME_SIGNING_SECRET": "x" * 40}, "RESUME_API_AUTH_TOKEN"),
        ({"OPENAI_BASE_URL": "ftp://x"}, "OPENAI_BASE_URL"),
        ({"RESUME_LLM_TEMPERATURE": "3"}, "RESUME_LLM_TEMPERATURE"),
        ({"RESUME_MAX_UPLOAD_BYTES": "abc"}, "RESUME_MAX_UPLOAD_BYTES"),
        ({"RESUME_MAX_UPLOAD_BYTES": "9000000", "RESUME_MAX_UNCOMPRESSED_BYTES": "5000"}, "RESUME_MAX_UNCOMPRESSED_BYTES"),
    ],
)
def test_invalid_configuration_rejected_at_startup(env, fragment):
    with pytest.raises(ConfigurationError) as exc:
        load_settings(env)
    assert fragment in exc.value.message or "settings" in exc.value.message


def test_frontend_origins_split_and_remote_llm_detection():
    s = load_settings({"FRONTEND_ORIGIN": "http://a.test, http://b.test", "OPENAI_BASE_URL": "https://llm.example.com/v1"})
    assert s.frontend_origins == ["http://a.test", "http://b.test"]
    assert s.llm_is_remote
    assert not load_settings({}).llm_is_remote


def _record(**extra) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "evt", None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_logs_redact_resume_content_by_default():
    line = JsonFormatter(log_content=False).format(
        _record(job_description="Senior Python role at Acme", original_text="secret bullet", session_id="abc")
    )
    payload = json.loads(line)
    assert "Senior Python" not in line and "secret bullet" not in line
    assert payload["job_description"].startswith("<redacted") and payload["session_id"] == "abc"


def test_exception_logs_omit_tracebacks():
    try:
        raise ValueError("contains resume text: Jordan Lee")
    except ValueError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    line = JsonFormatter().format(rec)
    assert "Jordan Lee" not in line and json.loads(line)["exception"] == "ValueError"


def test_sequential_graph_routing():
    g = SequentialGraph(dict)
    g.add_node("a", lambda s: {"n": 1})
    g.add_node("b", lambda s: {"path": "b"})
    g.add_node("c", lambda s: {"path": "c"})
    g.set_entry_point("a")
    g.add_conditional_edges("a", lambda s: "x" if s["n"] == 1 else "y", {"x": "b", "y": "c"})
    g.add_edge("b", "__end__")
    out = asyncio.run(g.compile().ainvoke({}))
    assert out == {"n": 1, "path": "b"}


@pytest.mark.requires_langgraph
def test_graphs_compile_with_langgraph(tmp_path):
    from tests.support.service_harness import make_service

    h = make_service(tmp_path, [], renderer=False)
    from app.workflow.graph import HAS_LANGGRAPH, build_finalize_graph, build_proposal_graph

    assert HAS_LANGGRAPH
    assert build_proposal_graph(h.service.deps) is not None
    assert build_finalize_graph(h.service.deps) is not None


def test_dotenv_strips_inline_comments_but_not_quoted_hashes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("RESUME_LLM_PROVIDER=openai            # openai | disabled\nA='x # y'\nB=a#b\n")
    assert _read_dotenv(env) == {"RESUME_LLM_PROVIDER": "openai", "A": "x # y", "B": "a#b"}


def test_env_example_is_a_valid_configuration():
    from pathlib import Path

    load_settings(environ=_read_dotenv(Path(__file__).resolve().parents[2] / "env.example"))


def test_openai_compatible_provider_payload_and_response():
    import httpx

    from app.llm.provider import LLMRequest, OpenAICompatibleProvider

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = {
            "model": "q",
            "choices": [{"message": {"content": '{"a": 1}<|im_end|>'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3},
        }
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("http://x/v1/", "q", 5, 0, api_key="k", client=client)
    schema = {"type": "object", "required": ["a"]}
    response = asyncio.run(
        provider.complete(LLMRequest(system="s", user="u", json_schema=schema, temperature=0, max_tokens=10, purpose="t"))
    )
    payload = json.loads(sent[0].content)
    assert str(sent[0].url) == "http://x/v1/chat/completions" and sent[0].headers["authorization"] == "Bearer k"
    assert payload["response_format"]["json_schema"]["schema"] == schema
    assert '"required":["a"]' in payload["messages"][0]["content"]  # restated for servers that ignore response_format
    assert response.parse_json() == {"a": 1} and response.prompt_tokens == 3


@pytest.mark.parametrize(
    "content", ['{"a": 1}', '```json\n{"a": 1}\n```', '<think>hmm {"b": 2}</think>\n{"a": 1}', 'Sure: {"a": 1} <|im_end|>']
)
def test_parse_json_tolerates_wrappers(content):
    from app.llm.provider import LLMResponse

    assert LLMResponse(content=content, provider="p", model="m", latency_ms=0, attempts=1).parse_json() == {"a": 1}


def test_default_provider_is_local_mlx_lm():
    s = load_settings({})
    assert s.llm_provider == "openai" and s.openai_base_url == "http://127.0.0.1:8000/v1"
    assert s.openai_model == "mlx-community/Qwen3-4B-Instruct-2507-4bit"
