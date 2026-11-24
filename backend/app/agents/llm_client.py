from __future__ import annotations

import json
import re
from typing import Any
from openai import AsyncOpenAI

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class LLMConfigurationError(RuntimeError):
    """Raised when LLM credentials are missing or rejected."""


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def extract_code_block(text: str, lang_hints: tuple[str, ...] = ("python", "html")) -> str:
    cleaned = text.strip()
    for lang in lang_hints:
        pattern = rf"```{lang}\s*\n(.*?)```"
        m = re.search(pattern, cleaned, flags=re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m_any = re.search(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", cleaned, flags=re.DOTALL)
    if m_any:
        return m_any.group(1).strip()
    return cleaned


class LLMClient:
    def __init__(self) -> None:
        if not LLM_API_KEY:
            raise LLMConfigurationError(
                "LLM_API_KEY (or DEEPSEEK_API / OPENAI_API_KEY) is not configured."
            )
        self.client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=180.0)
        self.model = LLM_MODEL

    async def complete_json(
        self,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        max_tokens: int = 12288,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if not isinstance(user_content, list):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Fallback if a custom OpenAI-compatible endpoint rejects extra_body / response_format
            kwargs.pop("extra_body", None)
            kwargs.pop("response_format", None)
            try:
                resp = await self.client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise RuntimeError(f"LLM JSON request failed: {exc} / {exc2}") from exc2

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError(
                f"LLM JSON completion returned empty content (finish_reason={resp.choices[0].finish_reason})"
            )
        return _extract_json_block(content)

    async def complete_text(
        self,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        max_tokens: int = 24576,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except Exception:
            kwargs.pop("extra_body", None)
            resp = await self.client.chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError(
                f"LLM text completion returned empty content (finish_reason={resp.choices[0].finish_reason})"
            )
        return content
