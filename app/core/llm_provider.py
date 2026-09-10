"""
Memory Engine — LLM Provider Abstraction
Supports: DeepSeek, Groq, OpenRouter, OpenAI-compatible endpoints.
Falls back through providers if one fails.
"""

import os
import json
import httpx
from typing import Optional


class LLMProvider:
    """Base LLM provider with structured output support."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: int = 30):
        self.name = name
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        """Send completion request, return response text."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def extract_json(self, prompt: str, system: str = "") -> list | dict:
        """Complete and parse JSON response."""
        response = await self.complete(prompt, system)

        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks
        import re
        patterns = [
            r'```json\s*(.*?)\s*```',
            r'```\s*(.*?)\s*```',
            r'(\[.*\])',
            r'(\{.*\})',
        ]
        for pattern in patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue

        return []


# ═══ Provider Factory ═════════════════════════════════════

def create_providers() -> list[LLMProvider]:
    """
    Create LLM providers from environment variables.
    Order = priority (first available is used).
    """
    providers = []

    # DeepSeek (reasoning + vision)
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key and key != "sk-your-deepseek-key":
        providers.append(LLMProvider(
            name="deepseek",
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=key,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        ))

    # Groq (fast inference)
    key = os.getenv("GROQ_API_KEY", "")
    if key:
        providers.append(LLMProvider(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=key,
            model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
        ))

    # OpenRouter (multi-provider)
    key = os.getenv("OPENROUTER_API_KEY", "")
    if key:
        providers.append(LLMProvider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            model=os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324"),
        ))

    # OpenAI-compatible fallback (local or custom)
    base = os.getenv("LLM_BASE_URL", "")
    key = os.getenv("LLM_API_KEY", "")
    if base:
        providers.append(LLMProvider(
            name="custom",
            base_url=base,
            api_key=key or "none",
            model=os.getenv("LLM_MODEL", "default"),
        ))

    return providers


# ═══ Fallback Caller ══════════════════════════════════════

class LLMCaller:
    """
    Tries providers in order, falls back on failure.
    This is the main entry point for all LLM calls.
    """

    def __init__(self):
        self.providers = create_providers()
        self._call_count = 0
        self._error_count = 0

    async def call(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        """Call LLM with fallback."""
        if not self.providers:
            # No providers configured — return empty JSON
            return "[]"

        last_error = None
        for provider in self.providers:
            try:
                self._call_count += 1
                result = await provider.complete(prompt, system, temperature)
                return result
            except Exception as e:
                last_error = e
                self._error_count += 1
                continue

        raise Exception(f"All LLM providers failed. Last error: {last_error}")

    async def extract_json(self, prompt: str, system: str = "") -> list | dict:
        """Call and parse JSON."""
        if not self.providers:
            return []

        last_error = None
        for provider in self.providers:
            try:
                self._call_count += 1
                result = await provider.extract_json(prompt, system)
                return result
            except Exception as e:
                last_error = e
                self._error_count += 1
                continue

        raise Exception(f"All LLM providers failed. Last error: {last_error}")

    @property
    def stats(self):
        return {
            "providers": [p.name for p in self.providers],
            "total_calls": self._call_count,
            "errors": self._error_count,
        }


# Singleton
_llm_caller: Optional[LLMCaller] = None

def get_llm() -> LLMCaller:
    """Get the global LLM caller (singleton)."""
    global _llm_caller
    if _llm_caller is None:
        _llm_caller = LLMCaller()
    return _llm_caller
