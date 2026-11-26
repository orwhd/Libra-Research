from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from ..config import resolve_llm_runtime_config
from ..schemas import RunMode
from ..security.tool_permissions import (
    DISALLOWED_SDK_BUILTIN_TOOLS,
    ToolPermissionDeniedError,
    ToolPermissionGate,
    redact_secrets,
)

ScienceToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class AgentTurnResult:
    ok: bool
    provider: str
    public_summary: str
    tool_traces: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class SDKAdapter:
    """
    Controlled Agent SDK Adapter (L3 §12 / ADR-V2-06).
    - Primary LIVE engine: `claude-agent-sdk==0.2.189` (`ClaudeSDKClient`) with:
      * Custom in-process MCP server (`create_sdk_mcp_server("science", ...)`)
      * `tools=[]` and `disallowed_tools=DISALLOWED_SDK_BUILTIN_TOOLS` so built-in Bash/Write/Read/Task/Agent tools cannot bypass `ResourceScheduler` or `ToolPermissionGate`
      * Explicit `can_use_tool` permission callback backed by `ToolPermissionGate`
      * Configurable deep thinking (`effort="high"`) & multi-turn tool iteration (`max_turns >= 6`)
    - Secondary LIVE engine: OpenAI-compatible function-calling loop (`AsyncOpenAI`) enforcing the exact same `ToolPermissionGate` & science handlers when Claude CLI/Anthropic auth is unavailable.
    - Simulation engine: deterministic offline turn (ONLY allowed when `mode == "simulation"`).
    Never exposes private `ThinkingBlock` / reasoning text.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.gate = ToolPermissionGate(workspace_root)
        self.workspace_root = self.gate.allowed_root

    def _should_prefer_openai_first(self, cfg: dict[str, Any]) -> bool:
        forced = cfg["forced_provider"]
        if forced == "openai":
            return True
        if forced == "claude":
            return False
        return bool(
            cfg["has_openai_compatible"]
            and (cfg.get("has_ui_openai_config") or not cfg["has_anthropic_key"])
        )

    def detect_live_capability(self) -> tuple[bool, str]:
        cfg = resolve_llm_runtime_config()
        forced = cfg["forced_provider"]
        if forced == "simulation":
            return False, "simulation"
        if self._should_prefer_openai_first(cfg) and cfg["has_openai_compatible"]:
            return True, f"openai_tool_harness:{cfg['openai_model']}"
        if forced in ("", "claude") and (cfg["has_claude_cli"] or cfg["has_anthropic_key"]):
            return True, "claude_agent_sdk:0.2.189"
        if forced in ("", "openai") and cfg["has_openai_compatible"]:
            return True, f"openai_tool_harness:{cfg['openai_model']}"
        return False, "none"

    async def run_controlled_science_turn(
        self,
        *,
        mode: RunMode,
        role: str,
        system_prompt: str,
        user_prompt: str,
        tools_spec: list[dict[str, Any]],
        handlers: dict[str, ScienceToolHandler],
        max_turns: int = 6,
        timeout_seconds: float = 75.0,
        effort: str | None = None,
        on_tool_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentTurnResult:
        if mode == "simulation":
            return await self._run_simulation_turn(role=role, handlers=handlers, tools_spec=tools_spec)

        cfg = resolve_llm_runtime_config()
        forced = cfg["forced_provider"]
        eff = (effort or cfg.get("thinking_effort") or "medium").strip().lower()
        if eff not in ("low", "medium", "high"):
            eff = "medium"
        errors: list[str] = []

        prefer_openai = self._should_prefer_openai_first(cfg)
        provider_order: list[str] = []
        if forced == "openai":
            provider_order = ["openai"]
        elif forced == "claude":
            provider_order = ["claude"]
        elif prefer_openai:
            if cfg["has_openai_compatible"]:
                provider_order.append("openai")
            if cfg["has_claude_cli"] or cfg["has_anthropic_key"]:
                provider_order.append("claude")
        else:
            if cfg["has_claude_cli"] or cfg["has_anthropic_key"]:
                provider_order.append("claude")
            if cfg["has_openai_compatible"]:
                provider_order.append("openai")

        for prov in provider_order:
            if prov == "openai" and cfg["has_openai_compatible"]:
                try:
                    res_oa = await asyncio.wait_for(
                        self._run_openai_tool_turn(
                            cfg=cfg,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            tools_spec=tools_spec,
                            handlers=handlers,
                            max_turns=max_turns,
                            effort=eff,
                            on_tool_trace=on_tool_trace,
                        ),
                        timeout=timeout_seconds,
                    )
                    if res_oa.ok:
                        return res_oa
                    errors.append(res_oa.error or "OpenAI tool turn returned non-ok")
                except Exception as exc:
                    errors.append(f"OpenAI tool harness error: {type(exc).__name__}: {exc}")
            elif prov == "claude" and (cfg["has_claude_cli"] or cfg["has_anthropic_key"]):
                try:
                    res = await asyncio.wait_for(
                        self._run_claude_sdk_turn(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            tools_spec=tools_spec,
                            handlers=handlers,
                            max_turns=max_turns,
                            effort=eff,
                            on_tool_trace=on_tool_trace,
                        ),
                        timeout=timeout_seconds,
                    )
                    if res.ok:
                        return res
                    errors.append(res.error or "ClaudeSDKClient turn returned non-ok")
                except Exception as exc:
                    errors.append(f"ClaudeSDKClient error: {type(exc).__name__}: {exc}")

        return AgentTurnResult(
            ok=False,
            provider="blocked",
            public_summary="",
            tool_traces=[],
            error=str(redact_secrets(" | ".join(errors) or "No live LLM/SDK credentials available.")),
        )

    async def _run_claude_sdk_turn(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools_spec: list[dict[str, Any]],
        handlers: dict[str, ScienceToolHandler],
        max_turns: int,
        effort: str,
        on_tool_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentTurnResult:
        import claude_agent_sdk as sdk

        traces: list[dict[str, Any]] = []

        def _record_trace(item: dict[str, Any]) -> None:
            traces.append(item)
            if on_tool_trace is not None:
                try:
                    on_tool_trace(item)
                except Exception:
                    pass

        sdk_tools = []
        allowed_mcp_names: list[str] = []

        for spec in tools_spec:
            t_name = str(spec["name"])
            t_desc = str(spec["description"])
            t_schema = dict(spec.get("parameters") or {"type": "object", "properties": {}})
            mcp_full_name = f"mcp__science__{t_name}"
            allowed_mcp_names.append(mcp_full_name)
            handler_fn = handlers[t_name]

            def _make_tool(bound_name: str, bound_desc: str, bound_schema: dict[str, Any], bound_fn: ScienceToolHandler):
                @sdk.tool(bound_name, bound_desc, bound_schema)
                async def _wrapped(args: dict[str, Any]) -> dict[str, Any]:
                    try:
                        self.gate.authorize_tool_call(bound_name, args)
                    except ToolPermissionDeniedError as exc:
                        _record_trace(
                            {
                                "tool": bound_name,
                                "provider": "claude-agent-sdk==0.2.189",
                                "args": redact_secrets(args),
                                "status": "denied",
                                "error": str(exc),
                            }
                        )
                        return {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"ok": False, "blocked": True, "error": str(exc)}),
                                }
                            ]
                        }

                    result = await bound_fn(args)
                    safe_res = redact_secrets(result)
                    _record_trace(
                        {
                            "tool": bound_name,
                            "provider": "claude-agent-sdk==0.2.189",
                            "args": redact_secrets(args),
                            "status": "ok" if not (isinstance(result, dict) and result.get("ok") is False) else "error",
                            "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
                        }
                    )
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(safe_res, ensure_ascii=False),
                            }
                        ]
                    }

                return _wrapped

            sdk_tools.append(_make_tool(t_name, t_desc, t_schema, handler_fn))

        async def _can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            _ctx: Any,
        ) -> Any:
            try:
                self.gate.authorize_tool_call(tool_name, input_data)
                return sdk.PermissionResultAllow(updated_input=input_data)
            except ToolPermissionDeniedError as exc:
                return sdk.PermissionResultDeny(message=str(exc), interrupt=True)

        mcp_server = sdk.create_sdk_mcp_server(name="science", version="1.0.0", tools=sdk_tools)
        sdk_cwd = self.workspace_root / "sdk_cwd"
        sdk_cwd.mkdir(parents=True, exist_ok=True)

        opts = sdk.ClaudeAgentOptions(
            tools=[],
            mcp_servers={"science": mcp_server},
            allowed_tools=allowed_mcp_names,
            disallowed_tools=list(DISALLOWED_SDK_BUILTIN_TOOLS),
            can_use_tool=_can_use_tool,
            max_turns=max_turns,
            effort=effort,  # type: ignore[arg-type]
            cwd=str(sdk_cwd),
            system_prompt=(
                f"{system_prompt}\n"
                "Provide concise, rigorous, evidence-grounded summaries suitable for public research logs. "
                "Never include private chain-of-thought."
            ),
        )

        text_chunks: list[str] = []
        async with sdk.ClaudeSDKClient(options=opts) as client:
            await client.query(user_prompt)
            async for msg in client.receive_response():
                if isinstance(msg, sdk.AssistantMessage):
                    for block in getattr(msg, "content", []) or []:
                        # Extract TextBlock only; explicitly ignore ThinkingBlock (no private CoT!)
                        if isinstance(block, sdk.TextBlock) and getattr(block, "text", ""):
                            text_chunks.append(str(block.text).strip())

        summary_txt = "\n".join(c for c in text_chunks if c).strip()
        if not summary_txt and traces:
            summary_txt = f"Executed {len(traces)} controlled scientific tool call(s) via ClaudeSDKClient."
        return AgentTurnResult(
            ok=True,
            provider="claude-agent-sdk==0.2.189",
            public_summary=str(redact_secrets(summary_txt))[:1600],
            tool_traces=traces,
        )

    async def _run_openai_tool_turn(
        self,
        *,
        cfg: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        tools_spec: list[dict[str, Any]],
        handlers: dict[str, ScienceToolHandler],
        max_turns: int,
        effort: str = "medium",
        on_tool_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentTurnResult:
        client = AsyncOpenAI(
            api_key=cfg["openai_api_key"],
            base_url=cfg["openai_base_url"],
            timeout=45.0,
        )
        oa_tools = [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": s.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for s in tools_spec
        ]
        effort_guidance = {
            "low": "Work concisely and complete tool calls rapidly without redundant reads.",
            "medium": "Think methodically and complete tool calls concisely using already provided context.",
            "high": "Think deeply about scientific validity, edge cases, and quantitative precision; prefer direct tool execution.",
        }.get(effort, "Work methodically and execute required scientific tools directly.")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{system_prompt}\nReasoning effort setting: {effort}. {effort_guidance}"},
            {"role": "user", "content": user_prompt},
        ]
        traces: list[dict[str, Any]] = []
        final_text = ""

        def _record_oa_trace(item: dict[str, Any]) -> None:
            traces.append(item)
            if on_tool_trace is not None:
                try:
                    on_tool_trace(item)
                except Exception:
                    pass

        for _ in range(max_turns):
            resp = await client.chat.completions.create(
                model=cfg["openai_model"],
                messages=messages,
                tools=oa_tools if oa_tools else None,
                max_tokens=4096,
            )
            choice = resp.choices[0].message
            tool_calls = getattr(choice, "tool_calls", None) or []
            if not tool_calls:
                final_text = (choice.content or "").strip()
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                fn_name = tc.function.name
                parse_err: str | None = None
                try:
                    parsed_args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(parsed_args, dict):
                        parse_err = "Tool arguments must be a JSON object."
                        parsed_args = {}
                except json.JSONDecodeError:
                    parse_err = "Malformed JSON arguments; retry with valid concise JSON."
                    parsed_args = {}

                if parse_err:
                    result: dict[str, Any] = {"ok": False, "error": parse_err}
                elif fn_name not in handlers:
                    result = {"ok": False, "error": f"Unknown tool '{fn_name}'."}
                else:
                    try:
                        self.gate.authorize_tool_call(fn_name, parsed_args)
                        handler = handlers[fn_name]
                        result = await handler(parsed_args)
                    except ToolPermissionDeniedError as exc:
                        result = {"ok": False, "blocked": True, "error": str(exc)}
                safe_res = redact_secrets(result)
                _record_oa_trace(
                    {
                        "tool": fn_name,
                        "provider": f"openai_compatible:{cfg['openai_model']}",
                        "args": redact_secrets(parsed_args),
                        "status": "ok" if not (isinstance(result, dict) and result.get("ok") is False) else "error",
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(safe_res, ensure_ascii=False),
                    }
                )

        if not final_text and traces:
            final_text = f"Executed {len(traces)} controlled scientific tool call(s) via {cfg['openai_model']}."
        return AgentTurnResult(
            ok=True,
            provider=f"openai_compatible:{cfg['openai_model']}",
            public_summary=str(redact_secrets(final_text))[:1600],
            tool_traces=traces,
        )

    async def _run_simulation_turn(
        self,
        *,
        role: str,
        handlers: dict[str, ScienceToolHandler],
        tools_spec: list[dict[str, Any]],
    ) -> AgentTurnResult:
        traces: list[dict[str, Any]] = []
        for spec in tools_spec[:1]:
            t_name = str(spec["name"])
            if t_name in handlers:
                self.gate.authorize_tool_call(t_name, {"simulation": True})
                await handlers[t_name]({"simulation": True})
                traces.append({"tool": t_name, "provider": "simulation_mock", "status": "ok"})
        return AgentTurnResult(
            ok=True,
            provider="simulation_mock",
            public_summary=f"[SIMULATION] {role.capitalize()} completed protocol check.",
            tool_traces=traces,
        )
