"""LLM integration with local Ollama."""

from __future__ import annotations

from typing import Optional

import httpx

from config import get_settings


class OllamaLLM:
    """Ollama LLM interface."""
    
    def __init__(self):
        self.settings = get_settings()
        self.model = self.settings.ollama_model
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    
    async def invoke(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """
        Call Ollama chat API with messages
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            
        Returns:
            Generated text response.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        response = await self.client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )

        if response.status_code >= 400:
            raise RuntimeError(self._format_error(response))

        result = response.json()
        return self._extract_text(result)

    @staticmethod
    def _format_error(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return f"Ollama request failed ({response.status_code}): {payload}"

    @staticmethod
    def _extract_text(result: dict) -> str:
        message = result.get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        output = result.get("response")
        if isinstance(output, str):
            return output
        raise RuntimeError(f"Unexpected Ollama response shape: {result}")
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Global instance
_ollama_llm: Optional[OllamaLLM] = None


async def get_ollama_llm() -> OllamaLLM:
    """Get or create Ollama LLM instance."""
    global _ollama_llm
    if _ollama_llm is None:
        _ollama_llm = OllamaLLM()
    return _ollama_llm
