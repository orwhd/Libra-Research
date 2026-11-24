from __future__ import annotations

import ast
import json
import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import SANDBOX_TIMEOUT_SECONDS


FORBIDDEN_IMPORTS = {
    "subprocess",
    "socket",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "shutil",
    "ctypes",
    "multiprocessing",
    "pty",
    "fcntl",
    "webbrowser",
    "ftplib",
    "smtplib",
    "telnetlib",
    "ssl",
}

FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
}

FORBIDDEN_ATTR_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "spawnl"),
    ("os", "spawnv"),
    ("os", "execv"),
    ("os", "execl"),
    ("os", "fork"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
}


class SandboxSecurityError(ValueError):
    """Raised when generated Python code fails static AST safety inspection."""


def validate_python_ast(source_code: str) -> None:
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise SandboxSecurityError(f"SyntaxError in generated script: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".")[0]
                if top_mod in FORBIDDEN_IMPORTS:
                    raise SandboxSecurityError(f"Forbidden import '{alias.name}' inside sandbox")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_IMPORTS:
                raise SandboxSecurityError(f"Forbidden import from '{node.module}' inside sandbox")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                raise SandboxSecurityError(f"Forbidden builtin call '{node.func.id}()'")
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in FORBIDDEN_ATTR_CALLS:
                    raise SandboxSecurityError(f"Forbidden call '{pair[0]}.{pair[1]}()'")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "environ":
                raise SandboxSecurityError("Access to os.environ is forbidden inside sandbox")


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    output_data: Any | None
    error: str | None = None


def _apply_posix_limits() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (SANDBOX_TIMEOUT_SECONDS, SANDBOX_TIMEOUT_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024, 48 * 1024 * 1024))
    except Exception:
        pass


def run_python_sandbox(
    script_code: str,
    work_dir: Path,
    input_path: Path,
    output_path: Path,
    timeout_seconds: int = SANDBOX_TIMEOUT_SECONDS,
) -> SandboxResult:
    """
    Execute untrusted LLM-generated Python analysis/transformation script in an
    isolated Python (`-I`) subprocess with stripped environment (zero API keys),
    AST static checks, POSIX CPU/file limits, and explicit input/output paths.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    script_file = work_dir / "script.py"
    script_file.write_text(script_code, encoding="utf-8")

    try:
        validate_python_ast(script_code)
    except SandboxSecurityError as exc:
        return SandboxResult(
            ok=False,
            exit_code=-2,
            stdout="",
            stderr=str(exc),
            duration_ms=0,
            output_data=None,
            error=str(exc),
        )

    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(script_file),
                str(input_path),
                str(output_path),
            ],
            cwd=str(work_dir),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            preexec_fn=_apply_posix_limits if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return SandboxResult(
            ok=False,
            exit_code=-1,
            stdout=(exc.stdout or "")[:4000] if isinstance(exc.stdout, str) else "",
            stderr=f"Sandbox execution timed out after {timeout_seconds}s",
            duration_ms=elapsed,
            output_data=None,
            error=f"Timeout after {timeout_seconds}s",
        )

    elapsed = int((time.monotonic() - t0) * 1000)
    stdout = proc.stdout[:12000]
    stderr = proc.stderr[:12000]

    if proc.returncode != 0:
        return SandboxResult(
            ok=False,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=elapsed,
            output_data=None,
            error=f"Script exited with code {proc.returncode}: {stderr.strip() or stdout.strip()}",
        )

    if not output_path.exists():
        return SandboxResult(
            ok=False,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=elapsed,
            output_data=None,
            error=f"Script finished cleanly but did not write expected output file {output_path.name}",
        )

    try:
        output_data = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return SandboxResult(
            ok=False,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=elapsed,
            output_data=None,
            error=f"Output file is not valid JSON: {exc}",
        )

    return SandboxResult(
        ok=True,
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        duration_ms=elapsed,
        output_data=output_data,
        error=None,
    )
