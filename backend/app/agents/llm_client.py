from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from openai import AsyncOpenAI

from ..services.api_settings import (
    get_effective_llm_concurrency,
    get_effective_llm_credentials,
)


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
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


_DEEPSEEK_BUDGET_BY_EFFORT: dict[str, int] = {
    "low": 1024,
    "medium": 3072,
    "high": 8192,
}

# Process-wide per-event-loop API concurrency semaphores keyed by (loop_id, max_concurrency)
_LOOP_CONCURRENCY_SEMS: dict[tuple[int, int], asyncio.Semaphore] = {}


def _get_concurrency_semaphore(limit: int) -> asyncio.Semaphore:
    clamped = max(1, min(16, int(limit or 4)))
    lid = id(asyncio.get_running_loop())
    key = (lid, clamped)
    sem = _LOOP_CONCURRENCY_SEMS.get(key)
    if sem is None:
        sem = asyncio.Semaphore(clamped)
        _LOOP_CONCURRENCY_SEMS[key] = sem
    return sem


class LLMClient:
    def __init__(self) -> None:
        api_key, base_url, model, thinking_effort = get_effective_llm_credentials()
        if not api_key:
            raise LLMConfigurationError(
                "LLM API Key is not configured. Click 'click me to complete API config!' on the home screen or set LLM_API_KEY in .env."
            )
        self.base_url = base_url
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
        self.model = model
        self.thinking_effort = thinking_effort
        self.max_concurrency = get_effective_llm_concurrency()
        self._is_deepseek = "deepseek" in base_url.lower() or "deepseek" in model.lower()

    async def _throttled_create(self, **call_kwargs: Any) -> Any:
        sem = _get_concurrency_semaphore(self.max_concurrency)
        async with sem:
            return await self.client.chat.completions.create(**call_kwargs)

    def _apply_thinking(
        self,
        kwargs: dict[str, Any],
        effort_override: str | None = None,
    ) -> str:
        effective = effort_override or self.thinking_effort
        kwargs["reasoning_effort"] = effective
        if "api.deepseek.com" in self.base_url.lower():
            budget = _DEEPSEEK_BUDGET_BY_EFFORT.get(effective, 3072)
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled", "budget_tokens": budget}
            }
            cur_max = int(kwargs.get("max_tokens", 8192))
            kwargs["max_tokens"] = min(32768, max(cur_max, budget + 8192))
        return effective

    @staticmethod
    def _unpack_choice(resp: Any) -> tuple[str, str | None]:
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        finish = getattr(choice, "finish_reason", None)
        return content, finish

    async def _create_with_fallback(
        self,
        base_kwargs: dict[str, Any],
        min_retry_tokens: int,
        effort_override: str | None = None,
    ) -> str:
        kwargs = dict(base_kwargs)
        active_effort = self._apply_thinking(kwargs, effort_override=effort_override)

        try:
            resp = await self._throttled_create(**kwargs)
        except Exception as exc:
            # Fallback if a custom OpenAI-compatible endpoint rejects extra_body / reasoning_effort / response_format
            kwargs.pop("extra_body", None)
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("response_format", None)
            try:
                resp = await self._throttled_create(**kwargs)
            except Exception as exc2:
                raise RuntimeError(f"LLM request failed: {exc} / {exc2}") from exc2

        content, finish = self._unpack_choice(resp)
        if content and finish != "length":
            return content

        best_content = content
        last_finish = finish
        retry_tokens = min(32768, max(int(base_kwargs.get("max_tokens", 8192)), min_retry_tokens))

        # Step 1: Step down thinking budget to low + expand token ceiling
        if active_effort != "low":
            low_kwargs = dict(base_kwargs)
            low_kwargs["max_tokens"] = retry_tokens
            self._apply_thinking(low_kwargs, effort_override="low")
            try:
                resp_low = await self._throttled_create(**low_kwargs)
                c_low, f_low = self._unpack_choice(resp_low)
                if c_low and f_low != "length":
                    return c_low
                if len(c_low) > len(best_content):
                    best_content = c_low
                last_finish = f_low or last_finish
            except Exception:
                pass

        # Step 2: Emergency recovery if reasoning tokens still exhausted max_tokens (finish_reason=length)
        direct_kwargs = dict(base_kwargs)
        direct_kwargs.pop("reasoning_effort", None)
        direct_kwargs["max_tokens"] = retry_tokens
        if self._is_deepseek:
            direct_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            direct_kwargs.pop("extra_body", None)

        try:
            resp_direct = await self._throttled_create(**direct_kwargs)
            c_dir, f_dir = self._unpack_choice(resp_direct)
            if c_dir and f_dir != "length":
                return c_dir
            if len(c_dir) > len(best_content):
                best_content = c_dir
            last_finish = f_dir or last_finish
        except Exception:
            direct_kwargs.pop("extra_body", None)
            direct_kwargs.pop("response_format", None)
            resp_bare = await self._throttled_create(**direct_kwargs)
            c_bare, f_bare = self._unpack_choice(resp_bare)
            if c_bare and f_bare != "length":
                return c_bare
            if len(c_bare) > len(best_content):
                best_content = c_bare
            last_finish = f_bare or last_finish

        if best_content:
            return best_content

        raise RuntimeError(
            f"LLM completion returned empty content (finish_reason={last_finish})"
        )

    async def complete_json(
        self,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        max_tokens: int = 12288,
        effort_override: str | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if not isinstance(user_content, list):
            kwargs["response_format"] = {"type": "json_object"}
        content = await self._create_with_fallback(
            kwargs,
            min_retry_tokens=16384,
            effort_override=effort_override,
        )
        return _extract_json_block(content)

    async def complete_text(
        self,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        max_tokens: int = 24576,
        effort_override: str | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        return await self._create_with_fallback(
            kwargs,
            min_retry_tokens=32768,
            effort_override=effort_override,
        )
