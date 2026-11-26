from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import DEFAULT_LIMITS
from .tool_permissions import ToolPermissionGate, redact_secrets

FORBIDDEN_EXPERIMENT_IMPORTS: frozenset[str] = frozenset(
    {
        "subprocess",
        "socket",
        "http",
        "urllib",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "ctypes",
        "pty",
        "webbrowser",
        "ftplib",
        "smtplib",
        "telnetlib",
        "ssl",
    }
)

FORBIDDEN_BUILTINS: frozenset[str] = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})

FORBIDDEN_OS_ATTRS: frozenset[str] = frozenset(
    {"system", "popen", "spawnl", "spawnv", "execv", "execl", "fork", "environ"}
)


class ExperimentSecurityError(ValueError):
    """Raised when an experiment script violates static AST policy."""


def validate_experiment_script_ast(source_code: str) -> None:
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise ExperimentSecurityError(f"SyntaxError in experiment script: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_EXPERIMENT_IMPORTS:
                    raise ExperimentSecurityError(
                        f"Forbidden network/process import '{alias.name}' in experiment runner."
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_EXPERIMENT_IMPORTS:
                raise ExperimentSecurityError(
                    f"Forbidden import from '{node.module}' in experiment runner."
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
                raise ExperimentSecurityError(
                    f"Forbidden builtin call '{node.func.id}()' in experiment runner."
                )
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "os" and node.func.attr in FORBIDDEN_OS_ATTRS:
                    raise ExperimentSecurityError(
                        f"Forbidden call 'os.{node.func.attr}' in experiment runner."
                    )
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "environ":
                raise ExperimentSecurityError(
                    "Access to os.environ is forbidden inside experiment runner."
                )


@dataclass
class ExperimentExecutionResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    script_path: Path
    script_sha256: str
    error: str | None = None


def _posix_child_limits(timeout_sec: int) -> None:
    try:
        cpu_limit = max(10, int(timeout_sec))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    except Exception:
        pass


class SafeExperimentRunner:
    """
    Executes approved Python scientific scripts inside an isolated subprocess (`-E` clean env,
    zero API keys / HOME / SSH credentials, AST static verification, timeout & file-size caps).
    Note: OS-level subprocess isolation + POSIX rlimits + AST gate (not a VM/Docker container).
    """

    def __init__(
        self,
        workspace_root: Path,
        timeout_seconds: int = DEFAULT_LIMITS.experiment_timeout_seconds,
    ) -> None:
        self.gate = ToolPermissionGate(workspace_root)
        self.workspace_root = self.gate.allowed_root
        self.timeout_seconds = timeout_seconds

    def run_sync(
        self,
        script_name: str,
        script_code: str,
        args: list[str] | None = None,
    ) -> ExperimentExecutionResult:
        script_path = self.gate.check_path(script_name)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_bytes = script_code.encode("utf-8")
        script_sha256 = hashlib.sha256(script_bytes).hexdigest()

        try:
            validate_experiment_script_ast(script_code)
        except ExperimentSecurityError as exc:
            return ExperimentExecutionResult(
                ok=False,
                exit_code=-2,
                stdout="",
                stderr=str(exc),
                duration_ms=0,
                script_path=script_path,
                script_sha256=script_sha256,
                error=str(exc),
            )

        script_path.write_bytes(script_bytes)
        mpl_dir = self.workspace_root / ".mplconfig"
        mpl_dir.mkdir(parents=True, exist_ok=True)

        # Stripped environment: zero LLM_API_KEY, ANTHROPIC_API_KEY, HOME, SSH_AUTH_SOCK, cookies
        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(mpl_dir),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }

        cmd = [sys.executable, "-E", str(script_path), *(args or [])]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                preexec_fn=(
                    (lambda: _posix_child_limits(self.timeout_seconds))
                    if os.name == "posix"
                    else None
                ),
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            out_txt = (exc.stdout or "")[:4000] if isinstance(exc.stdout, str) else ""
            msg = f"Experiment timed out after {self.timeout_seconds}s"
            return ExperimentExecutionResult(
                ok=False,
                exit_code=-1,
                stdout=str(redact_secrets(out_txt)),
                stderr=msg,
                duration_ms=elapsed,
                script_path=script_path,
                script_sha256=script_sha256,
                error=msg,
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        stdout = str(redact_secrets(proc.stdout[:16000]))
        stderr = str(redact_secrets(proc.stderr[:16000]))
        ok = proc.returncode == 0
        return ExperimentExecutionResult(
            ok=ok,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=elapsed,
            script_path=script_path,
            script_sha256=script_sha256,
            error=None if ok else f"Script exited with code {proc.returncode}: {stderr.strip() or stdout.strip()}",
        )

    async def run_async(
        self,
        script_name: str,
        script_code: str,
        args: list[str] | None = None,
    ) -> ExperimentExecutionResult:
        return await asyncio.to_thread(self.run_sync, script_name, script_code, args)
