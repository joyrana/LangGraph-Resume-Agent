"""LLM provider abstraction.

The rest of the application depends only on ``LLMProvider``. Providers return
raw JSON text plus metadata; schema validation and every factual check happen
in deterministic code afterwards. Models never receive tools and never see
document XML.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.errors import LLMError, LLMOutputInvalid, LLMUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    json_schema: dict[str, Any]
    temperature: float
    max_tokens: int
    purpose: str


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    latency_ms: int
    attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def parse_json(self) -> Any:
        text = self.content.strip()
        if "</think>" in text:
            text = text.rsplit("</think>", 1)[1].strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Unconstrained servers may append chat-template tokens (e.g. "<|im_end|>") after the object.
            start = text.find("{")
            if start != -1:
                try:
                    return json.JSONDecoder().raw_decode(text, start)[0]
                except json.JSONDecodeError:
                    pass
            raise LLMOutputInvalid("The language model returned malformed JSON.", details={"position": exc.pos}) from exc


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def close(self) -> None: ...


async def _post_json(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any], max_retries: int, purpose: str, headers: dict[str, str] | None = None
) -> tuple[dict[str, Any], int]:
    """POST with bounded exponential backoff on transient failures. Returns (JSON body, attempts)."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            response = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            logger.warning("llm_transient_error", extra={"attempt": attempt, "error_type": type(exc).__name__, "purpose": purpose})
        else:
            if response.status_code < 400:
                return response.json(), attempt
            if response.status_code in (408, 429) or response.status_code >= 500:
                last_error = LLMError(f"Model service returned HTTP {response.status_code}.")
                logger.warning("llm_transient_status", extra={"attempt": attempt, "status": response.status_code, "purpose": purpose})
            else:
                # 4xx (bad model name, bad request): retrying cannot help.
                raise LLMError(f"Model service rejected the request (HTTP {response.status_code}). Check the configured model name.")
        if attempt <= max_retries:
            await asyncio.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25))
    if isinstance(last_error, httpx.TimeoutException):
        raise LLMUnavailable("The language model timed out.")
    if isinstance(last_error, httpx.TransportError):
        raise LLMUnavailable("The language model service could not be reached.")
    raise LLMUnavailable("The language model service failed repeatedly.")


class DisabledProvider:
    name = "disabled"
    model = "none"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailable("No language model is configured (RESUME_LLM_PROVIDER=disabled).")

    async def close(self) -> None:
        return None


class OpenAICompatibleProvider:
    """OpenAI-style ``/chat/completions`` (mlx-lm, llama.cpp, vLLM, LM Studio).

    The schema is sent as ``response_format`` *and* restated in the system
    message, because some servers (mlx-lm) accept but do not enforce it; the
    caller's schema validation and re-ask cover the rest.
    """

    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0))

    async def complete(self, request: LLMRequest) -> LLMResponse:
        system = (
            f"{request.system}\n\nReply with a single JSON object, and nothing else, that validates against this JSON schema "
            f"(include every required field):\n{json.dumps(request.json_schema, separators=(',', ':'))}"
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": request.user}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "seed": 7,
            "response_format": {"type": "json_schema", "json_schema": {"name": request.purpose, "schema": request.json_schema}},
        }
        started = time.monotonic()
        body, attempt = await _post_json(
            self._client, f"{self.base_url}/chat/completions", payload, self.max_retries, request.purpose, self._headers
        )
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise LLMOutputInvalid("The language model response had no content.")
        usage = body.get("usage") or {}
        return LLMResponse(
            content=content,
            provider=self.name,
            model=str(body.get("model") or self.model),
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=attempt,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            metadata={"done_reason": choice.get("finish_reason")},
        )

    async def close(self) -> None:
        await self._client.aclose()


class ScriptedProvider:
    """Deterministic provider for tests and offline evaluation."""

    name = "scripted"

    def __init__(self, responses: list[str | Exception], model: str = "scripted-model") -> None:
        self.model = model
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise LLMUnavailable("Scripted provider has no more responses.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, provider=self.name, model=self.model, latency_ms=0, attempts=1)

    async def close(self) -> None:
        return None


def build_provider(settings, model: str | None = None) -> LLMProvider:
    """Provider from settings; ``model`` overrides the configured model (used by the judge)."""
    if settings.llm_provider == "disabled":
        return DisabledProvider()
    return OpenAICompatibleProvider(
        base_url=settings.openai_base_url,
        model=model or settings.openai_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        api_key=settings.openai_api_key,
    )
