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
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMOutputInvalid("The language model returned malformed JSON.", details={"position": exc.pos}) from exc


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def close(self) -> None: ...


class DisabledProvider:
    name = "disabled"
    model = "none"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailable("No language model is configured (RESUME_LLM_PROVIDER=disabled).")

    async def close(self) -> None:
        return None


class OllamaProvider:
    """Ollama ``/api/chat`` with JSON-schema constrained output and bounded retries."""

    name = "ollama"

    def __init__(
        self, base_url: str, model: str, timeout_seconds: float, max_retries: int, client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0))

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.model,
            "stream": False,
            "format": request.json_schema,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens, "seed": 7},
        }
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = await self._client.post(f"{self.base_url}/api/chat", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "llm_transient_error", extra={"attempt": attempt, "error_type": type(exc).__name__, "purpose": request.purpose}
                )
            else:
                if response.status_code < 400:
                    body = response.json()
                    content = (body.get("message") or {}).get("content")
                    if not isinstance(content, str):
                        raise LLMOutputInvalid("The language model response had no content.")
                    return LLMResponse(
                        content=content,
                        provider=self.name,
                        model=str(body.get("model") or self.model),
                        latency_ms=int((time.monotonic() - started) * 1000),
                        attempts=attempt,
                        prompt_tokens=body.get("prompt_eval_count"),
                        completion_tokens=body.get("eval_count"),
                        metadata={"done_reason": body.get("done_reason")},
                    )
                if response.status_code in (408, 429) or response.status_code >= 500:
                    last_error = LLMError(f"Model service returned HTTP {response.status_code}.")
                    logger.warning(
                        "llm_transient_status", extra={"attempt": attempt, "status": response.status_code, "purpose": request.purpose}
                    )
                else:
                    # 4xx (bad model name, bad request): retrying cannot help.
                    raise LLMError(f"Model service rejected the request (HTTP {response.status_code}). Check OLLAMA_MODEL.")
            if attempt <= self.max_retries:
                await asyncio.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25))
        if isinstance(last_error, (httpx.TimeoutException,)):
            raise LLMUnavailable("The language model timed out.")
        if isinstance(last_error, httpx.TransportError):
            raise LLMUnavailable("The language model service could not be reached.")
        raise LLMUnavailable("The language model service failed repeatedly.")

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


def build_provider(settings) -> LLMProvider:
    if settings.llm_provider == "disabled":
        return DisabledProvider()
    return OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
