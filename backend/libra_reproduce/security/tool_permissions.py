from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|anthropic[_-]?api[_-]?key\s*[:=]\s*\S+|Bearer\s+[A-Za-z0-9._-]{12,})",
    flags=re.IGNORECASE,
)

DISALLOWED_SDK_BUILTIN_TOOLS: list[str] = [
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "Read",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "NotebookRead",
    "Task",
    "Agent",
    "TodoWrite",
]

ALLOWED_SCIENCE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "inspect_paper_scope",
        "inspect_workspace_files",
        "register_paper_spec",
        "fetch_public_dataset",
        "register_data_manifest",
        "load_verified_dataset",
        "run_team_size_experiment",
        "run_citation_window_sensitivity",
        "audit_evidence_gate",
        "build_reproduction_reports",
        "inspect_workspace_data",
        "run_python_experiment",
        "record_experiment_finding",
        "mcp__science__inspect_paper_scope",
        "mcp__science__inspect_workspace_files",
        "mcp__science__register_paper_spec",
        "mcp__science__fetch_public_dataset",
        "mcp__science__register_data_manifest",
        "mcp__science__load_verified_dataset",
        "mcp__science__run_team_size_experiment",
        "mcp__science__run_citation_window_sensitivity",
        "mcp__science__audit_evidence_gate",
        "mcp__science__build_reproduction_reports",
        "mcp__science__inspect_workspace_data",
        "mcp__science__run_python_experiment",
        "mcp__science__record_experiment_finding",
    }
)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET_PATTERN.sub("[REDACTED_SECRET]", value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if any(tok in k.lower() for tok in ("api_key", "secret", "token", "password", "authorization")):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(x) for x in value]
    return value


class ToolPermissionDeniedError(PermissionError):
    """Raised when an agent or tool call attempts an unauthorized tool or path traversal."""


@dataclass
class AuditTraceEntry:
    tool_name: str
    allowed: bool
    reason: str
    sanitized_args: dict[str, Any] = field(default_factory=dict)


class ToolPermissionGate:
    """
    Enforces strict tool allowlisting, workspace path containment, and redacted audit logging.
    Never relies on Claude SDK `allowed_tools` alone.
    """

    def __init__(self, allowed_root: Path, extra_allowed_tools: set[str] | None = None) -> None:
        self.allowed_root = allowed_root.resolve()
        self.allowed_root.mkdir(parents=True, exist_ok=True)
        self.allowed_tools = set(ALLOWED_SCIENCE_TOOL_NAMES) | (extra_allowed_tools or set())
        self.audit_log: list[AuditTraceEntry] = []

    def check_path(self, candidate: str | Path) -> Path:
        raw_str = str(candidate)
        if "\x00" in raw_str:
            raise ToolPermissionDeniedError("Null byte in path denied.")
        p = Path(raw_str)
        resolved = (self.allowed_root / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            resolved.relative_to(self.allowed_root)
        except ValueError as exc:
            raise ToolPermissionDeniedError(
                "Path traversal outside controlled run workspace denied."
            ) from exc
        return resolved

    def authorize_tool_call(self, tool_name: str, tool_input: dict[str, Any] | None = None) -> AuditTraceEntry:
        args = dict(tool_input or {})
        sanitized = redact_secrets(args)

        if tool_name in DISALLOWED_SDK_BUILTIN_TOOLS or tool_name not in self.allowed_tools:
            entry = AuditTraceEntry(
                tool_name=tool_name,
                allowed=False,
                reason=f"Tool '{tool_name}' is not in the restricted science allowlist.",
                sanitized_args=sanitized,
            )
            self.audit_log.append(entry)
            raise ToolPermissionDeniedError(entry.reason)

        for key, val in args.items():
            if isinstance(val, str) and (
                "path" in key.lower() or "file" in key.lower() or "dir" in key.lower()
            ):
                if ".." in Path(val).parts or val.startswith(("/", "~")):
                    try:
                        self.check_path(val)
                    except ToolPermissionDeniedError as exc:
                        entry = AuditTraceEntry(
                            tool_name=tool_name,
                            allowed=False,
                            reason=str(exc),
                            sanitized_args={"blocked_arg": key},
                        )
                        self.audit_log.append(entry)
                        raise

        entry = AuditTraceEntry(
            tool_name=tool_name,
            allowed=True,
            reason="authorized",
            sanitized_args=sanitized,
        )
        self.audit_log.append(entry)
        return entry
