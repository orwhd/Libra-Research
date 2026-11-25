from __future__ import annotations

import asyncio
import base64
import csv
import io
import ipaddress
import json
import re
import socket
import urllib.parse
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from openai import AsyncOpenAI
from sse_starlette.sse import EventSourceResponse

from ..agents.capability_catalog import IFRAME_VIEW_STATE_BRIDGE
from ..agents.orchestrator import (
    EVENT_BUS,
    ResearchOrchestrator,
    _add_chat_message,
    _emit_event,
    _load_pending_comments,
)
from ..config import RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..models import (
    AnchorPoint,
    ArtifactVersion,
    AttachedCommentSummary,
    CanvasResponse,
    ChatMessage,
    CreateCommentRequest,
    CreateTaskRequest,
    CreateUploadRequest,
    CreateUrlUploadRequest,
    CreateWorkspaceRequest,
    FetchModelsRequest,
    FetchModelsResponse,
    FollowUpSuggestion,
    LLMSettingsResponse,
    PreviewViewState,
    RunEvent,
    TaskRun,
    UpdateCanvasPositionRequest,
    UpdateLLMSelectionRequest,
    UpdateLLMSettingsRequest,
    UploadSummaryResponse,
    VisualComment,
    WeeklyTrendingChartsResponse,
    Workspace,
    WorkspaceDetailResponse,
)
from ..services.api_settings import (
    clear_user_llm_settings,
    get_llm_settings_public,
    load_raw_user_llm_settings,
    save_user_llm_settings,
    update_user_llm_selection,
)
from ..services.artifact_manager import (
    build_provenance_zip_bytes,
    delete_artifact_version,
    get_artifact_version,
    list_workspace_artifact_versions,
    make_standalone_html,
    update_artifact_canvas_position,
)
from ..services.browser_validator import (
    await_comment_screenshots,
    capture_comment_screenshot,
    schedule_comment_screenshot,
)
from ..services.lineage_manager import load_lineage
from ..services.weekly_trending_charts import get_weekly_trending_charts

router = APIRouter(prefix="/api")

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _track_task(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _row_to_workspace(r: Any) -> Workspace:
    return Workspace(
        workspace_id=r["workspace_id"],
        title=r["title"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _row_to_run(r: Any) -> TaskRun:
    return TaskRun(
        run_id=r["run_id"],
        workspace_id=r["workspace_id"],
        task_group_id=r["task_group_id"],
        prompt=r["prompt"],
        status=r["status"],
        stage=r["stage"],
        produced_version_ids=loads(r["produced_version_ids_json"], []),
        error_message=r["error_message"],
        retry_count=r["retry_count"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _row_to_event(r: Any) -> RunEvent:
    return RunEvent(
        event_id=r["event_id"],
        run_id=r["run_id"],
        workspace_id=r["workspace_id"],
        agent_name=r["agent_name"],
        stage=r["stage"],
        status=r["status"],
        timestamp=r["timestamp"],
        concise_summary=r["concise_summary"],
        details=loads(r["details_json"], {}),
        optional_artifact_refs=loads(r["optional_artifact_refs_json"], []),
        optional_error=r["optional_error"],
    )


def _row_to_comment(r: Any) -> VisualComment:
    return VisualComment(
        comment_id=r["comment_id"],
        artifact_version_id=r["artifact_version_id"],
        workspace_id=r["workspace_id"],
        text=r["text"],
        anchor=AnchorPoint.model_validate(loads(r["anchor_json"], {})),
        screenshot_ref=r["screenshot_ref"],
        preview_view_state=PreviewViewState.model_validate(loads(r["preview_view_state_json"], {})),
        optional_element_hint=r["optional_element_hint"],
        status=r["status"],
        resolved_in_version_id=r["resolved_in_version_id"],
        created_at=r["created_at"],
    )


@router.get("/health")
async def health_check() -> dict[str, Any]:
    pub = get_llm_settings_public()
    return {"ok": True, "model": pub["model"], "configured": pub["configured"]}


@router.get("/settings/llm", response_model=LLMSettingsResponse)
async def get_llm_settings() -> LLMSettingsResponse:
    return LLMSettingsResponse.model_validate(get_llm_settings_public())


@router.put("/settings/llm", response_model=LLMSettingsResponse)
async def put_llm_settings(req: UpdateLLMSettingsRequest) -> LLMSettingsResponse:
    try:
        updated = save_user_llm_settings(
            provider=req.provider,
            api_key=req.api_key,
            base_url=req.base_url,
            model=req.model,
            thinking_effort=req.thinking_effort,
            available_models=req.available_models,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LLMSettingsResponse.model_validate(updated)


@router.patch("/settings/llm/selection", response_model=LLMSettingsResponse)
async def patch_llm_selection(req: UpdateLLMSelectionRequest) -> LLMSettingsResponse:
    updated = update_user_llm_selection(
        model=req.model,
        thinking_effort=req.thinking_effort,
    )
    return LLMSettingsResponse.model_validate(updated)


@router.post("/settings/llm/models", response_model=FetchModelsResponse)
async def fetch_llm_models(req: FetchModelsRequest) -> FetchModelsResponse:
    existing = load_raw_user_llm_settings()
    api_key = req.api_key.strip() or str(existing.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Please enter an API Key to fetch models.")
    base_url = req.base_url.strip().rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="API Base URL must start with http:// or https://.",
        )

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=15.0)
        page = await client.models.list()
        model_ids = sorted(
            {str(item.id).strip() for item in page.data if getattr(item, "id", None)}
        )
        return FetchModelsResponse(models=model_ids, count=len(model_ids))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to fetch models: {exc}") from exc


@router.delete("/settings/llm", response_model=LLMSettingsResponse)
async def delete_llm_settings() -> LLMSettingsResponse:
    return LLMSettingsResponse.model_validate(clear_user_llm_settings())


@router.post("/settings/llm/test")
async def test_llm_settings(req: UpdateLLMSettingsRequest) -> dict[str, Any]:
    existing = load_raw_user_llm_settings()
    api_key = req.api_key.strip() or str(existing.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Please enter an API Key to test.")
    base_url = req.base_url.strip().rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="API Base URL must start with http:// or https://.",
        )
    model_name = req.model.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Model name cannot be empty.")

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=15.0)
        await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=6,
        )
        return {"ok": True, "model": model_name}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Connection test failed: {exc}") from exc


@router.get("/trending-charts", response_model=WeeklyTrendingChartsResponse)
async def get_trending_charts(
    force: bool = Query(default=False),
) -> WeeklyTrendingChartsResponse:
    data = await get_weekly_trending_charts(force_refresh=force)
    return WeeklyTrendingChartsResponse.model_validate(data)


@router.post("/uploads", response_model=UploadSummaryResponse)
async def upload_dataset_files(req: CreateUploadRequest) -> UploadSummaryResponse:
    upload_id = f"up_{uuid.uuid4().hex[:10]}"
    base_dir = RUNTIME_DIR / "uploads" / upload_id / "files"
    base_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    total_bytes = 0
    for item in req.files:
        norm = item.relative_path.replace("\\", "/").lstrip("/")
        if not norm or ".." in Path(norm).parts:
            continue
        if any(
            part.startswith(".") or part in ("node_modules", "__pycache__", ".git", "dist")
            for part in Path(norm).parts
        ):
            continue
        try:
            raw_bytes = base64.b64decode(item.content_base64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Invalid base64 payload for {norm}: {exc}") from exc

        total_bytes += len(raw_bytes)
        if total_bytes > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Total upload size exceeds 25 MB limit")

        dest = (base_dir / norm).resolve()
        if not str(dest).startswith(str(base_dir.resolve())):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
        saved_paths.append(norm)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No valid non-hidden files were included in upload.")

    manifest = {
        "upload_id": upload_id,
        "label": req.label.strip() or "Uploaded Files",
        "created_at": utc_now_iso(),
        "files": saved_paths,
    }
    (RUNTIME_DIR / "uploads" / upload_id / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return UploadSummaryResponse(
        upload_id=upload_id,
        label=manifest["label"],
        file_count=len(saved_paths),
        files=saved_paths,
    )


_HTML_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg"})
_HTML_BLOCK_TAGS = frozenset(
    {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "header", "footer", "blockquote"}
)
_MAX_URL_BYTES = 15 * 1024 * 1024


class _HTMLTableAndTextExtractor(HTMLParser):
    """Extract HTML table cell matrices and visible text while ignoring scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_tags: list[str] = []
        self._table_stack: list[list[list[str]]] = []
        self._row_stack: list[list[str]] = []
        self._in_cell = False
        self._cell_chunks: list[str] = []
        self._text_chunks: list[str] = []
        self.tables: list[list[list[str]]] = []

    def _finish_cell(self) -> None:
        if not self._in_cell:
            return
        cell_text = " ".join("".join(self._cell_chunks).split())
        if self._row_stack:
            self._row_stack[-1].append(cell_text)
        self._in_cell = False
        self._cell_chunks = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in _HTML_SKIP_TAGS:
            self._skip_tags.append(t)
            return
        if self._skip_tags:
            return
        if t in _HTML_BLOCK_TAGS:
            self._text_chunks.append("\n")
        if t == "table":
            self._table_stack.append([])
        elif t == "tr":
            self._finish_cell()
            if self._table_stack:
                self._row_stack.append([])
        elif t in ("td", "th") and self._row_stack:
            self._finish_cell()
            self._in_cell = True
            self._cell_chunks = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _HTML_SKIP_TAGS and self._skip_tags:
            for idx in range(len(self._skip_tags) - 1, -1, -1):
                if self._skip_tags[idx] == t:
                    del self._skip_tags[idx:]
                    break
            return
        if self._skip_tags:
            return
        if t in ("td", "th"):
            self._finish_cell()
        elif t == "tr":
            self._finish_cell()
            if self._row_stack:
                row = self._row_stack.pop()
                if self._table_stack and any(cell for cell in row):
                    self._table_stack[-1].append(row)
        elif t == "table":
            self._finish_cell()
            if self._table_stack:
                table = self._table_stack.pop()
                if table:
                    self.tables.append(table)
        if t in _HTML_BLOCK_TAGS:
            self._text_chunks.append("\n")
        elif t in ("td", "th"):
            self._text_chunks.append(" | ")

    def handle_data(self, data: str) -> None:
        if self._skip_tags:
            return
        self._text_chunks.append(data)
        if self._in_cell:
            self._cell_chunks.append(data)


def extract_html_tables_and_text(html_str: str) -> tuple[list[list[list[str]]], str]:
    """Return extracted HTML tables (list of row lists) and normalized visible text."""
    parser = _HTMLTableAndTextExtractor()
    parser.feed(html_str)
    parser.close()
    raw_text = "".join(parser._text_chunks)
    lines = [" ".join(line.split()) for line in raw_text.splitlines()]
    clean_lines = [line for line in lines if line]
    return parser.tables, "\n".join(clean_lines)


def _normalize_table_rows(raw_rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in raw_rows), default=0)
    if width == 0:
        return []
    padded = [r + [""] * (width - len(r)) for r in raw_rows]
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx, raw_h in enumerate(padded[0]):
        base = raw_h.strip() or f"col_{idx + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        headers.append(base if count == 1 else f"{base}_{count}")
    return [headers, *padded[1:]]


async def _validate_public_http_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="URL cannot be empty.")
    if "://" not in candidate and not candidate.lower().startswith(("javascript:", "data:", "file:")):
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Only valid http:// or https:// URLs are supported.")

    hostname = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addr_info = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            0,
            socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not resolve host '{hostname}': {exc}") from exc

    if not addr_info:
        raise HTTPException(status_code=400, detail=f"Could not resolve host '{hostname}'.")

    for entry in addr_info:
        ip_str = entry[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid IP address resolved for '{hostname}'.") from exc
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Blocked private, loopback, or restricted URL host '{hostname}'.",
            )

    return parsed.geturl()


async def _fetch_public_url(initial_url: str) -> tuple[str, dict[str, str], str]:
    current_url = await _validate_public_http_url(initial_url)
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=False,
        headers={"User-Agent": "LibraResearch/1.0"},
    ) as client:
        for _ in range(6):
            try:
                async with client.stream("GET", current_url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise HTTPException(status_code=400, detail="Redirect missing Location header.")
                        next_url = urllib.parse.urljoin(current_url, loc)
                        current_url = await _validate_public_http_url(next_url)
                        continue
                    if resp.status_code >= 400:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Remote URL returned HTTP {resp.status_code}.",
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_URL_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail="Remote URL response exceeds 15 MB limit.",
                            )
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
                    encoding = resp.encoding or "utf-8"
                    raw_text = raw_bytes.decode(encoding, errors="replace")
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return str(resp.url), headers, raw_text
            except HTTPException:
                raise
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {exc}") from exc

    raise HTTPException(status_code=400, detail="Too many redirects while fetching URL.")


def _persist_url_upload_files(
    base_dir: Path, final_url: str, headers: dict[str, str], raw_text: str
) -> list[str]:
    base_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlsplit(final_url).path).suffix.lower()
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    saved_paths: list[str] = []

    if suffix == ".csv" or content_type in ("text/csv", "application/csv"):
        (base_dir / "remote_data.csv").write_text(raw_text, encoding="utf-8")
        saved_paths.append("remote_data.csv")
    elif suffix == ".tsv" or content_type == "text/tab-separated-values":
        (base_dir / "remote_data.tsv").write_text(raw_text, encoding="utf-8")
        saved_paths.append("remote_data.tsv")
    elif suffix == ".json" or content_type in ("application/json", "text/json"):
        try:
            json.loads(raw_text)
            (base_dir / "remote_data.json").write_text(raw_text, encoding="utf-8")
            saved_paths.append("remote_data.json")
        except Exception:  # noqa: BLE001
            (base_dir / "remote_content.txt").write_text(raw_text[:200_000], encoding="utf-8")
            saved_paths.append("remote_content.txt")
    elif suffix in (".jsonl", ".ndjson") or content_type in (
        "application/x-ndjson",
        "application/jsonl",
        "application/ndjson",
    ):
        (base_dir / "remote_data.jsonl").write_text(raw_text, encoding="utf-8")
        saved_paths.append("remote_data.jsonl")
    elif "html" in content_type or suffix in (".html", ".htm") or "<html" in raw_text[:500].lower():
        tables, page_text = extract_html_tables_and_text(raw_text)
        table_idx = 0
        for raw_table in tables:
            if len(raw_table) < 2:
                continue
            normalized = _normalize_table_rows(raw_table)
            if not normalized:
                continue
            table_idx += 1
            buf = io.StringIO()
            csv.writer(buf).writerows(normalized)
            fname = f"table_{table_idx}.csv"
            (base_dir / fname).write_text(buf.getvalue(), encoding="utf-8")
            saved_paths.append(fname)
            if table_idx >= 5:
                break
        text_payload = page_text[:100_000].strip() or f"Source URL: {final_url}\n"
        (base_dir / "page_content.txt").write_text(text_payload, encoding="utf-8")
        saved_paths.append("page_content.txt")
    else:
        text_payload = raw_text[:200_000].strip() or f"Source URL: {final_url}\n"
        (base_dir / "remote_content.txt").write_text(text_payload, encoding="utf-8")
        saved_paths.append("remote_content.txt")

    return saved_paths


@router.post("/uploads/url", response_model=UploadSummaryResponse)
async def upload_dataset_url(req: CreateUrlUploadRequest) -> UploadSummaryResponse:
    final_url, headers, raw_text = await _fetch_public_url(req.url)
    upload_id = f"up_{uuid.uuid4().hex[:10]}"
    base_dir = RUNTIME_DIR / "uploads" / upload_id / "files"
    saved_paths = _persist_url_upload_files(base_dir, final_url, headers, raw_text)

    parsed_final = urllib.parse.urlsplit(final_url)
    host = parsed_final.hostname or "url"
    last_seg = next((seg for seg in reversed(parsed_final.path.split("/")) if seg), "")
    label = f"{host}/{last_seg}" if last_seg else host
    if len(label) > 80:
        label = f"{label[:77]}..."

    manifest = {
        "upload_id": upload_id,
        "label": label,
        "source_url": final_url,
        "source_kind": "url",
        "created_at": utc_now_iso(),
        "files": saved_paths,
    }
    (RUNTIME_DIR / "uploads" / upload_id / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return UploadSummaryResponse(
        upload_id=upload_id,
        label=label,
        file_count=len(saved_paths),
        files=saved_paths,
    )


@router.get("/workspaces", response_model=list[Workspace])
async def list_workspaces() -> list[Workspace]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workspaces ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_workspace(r) for r in rows]


@router.post("/workspaces", response_model=Workspace)
async def create_workspace(req: CreateWorkspaceRequest) -> Workspace:
    now = utc_now_iso()
    ws = Workspace(
        workspace_id=f"ws_{uuid.uuid4().hex[:10]}",
        title=req.title.strip() or "New Research Workspace",
        created_at=now,
        updated_at=now,
    )
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workspaces (workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (ws.workspace_id, ws.title, ws.created_at, ws.updated_at),
        )
    return ws


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse)
async def get_workspace_detail(workspace_id: str) -> WorkspaceDetailResponse:
    with get_conn() as conn:
        ws_row = conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if not ws_row:
            raise HTTPException(status_code=404, detail="Workspace not found")

        msg_rows = conn.execute(
            "SELECT * FROM messages WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
        run_rows = conn.execute(
            "SELECT * FROM task_runs WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
        ev_rows = conn.execute(
            "SELECT * FROM run_events WHERE workspace_id = ? ORDER BY seq ASC",
            (workspace_id,),
        ).fetchall()
        comment_rows = conn.execute(
            "SELECT * FROM visual_comments WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()

    messages: list[ChatMessage] = []
    for m in msg_rows:
        meta = loads(m["metadata_json"], {}) if "metadata_json" in m.keys() else {}
        fu_raw = meta.get("follow_up") if isinstance(meta, dict) else None
        fu_obj = FollowUpSuggestion.model_validate(fu_raw) if isinstance(fu_raw, dict) else None
        ac_raw = meta.get("attached_comments") if isinstance(meta, dict) else None
        ac_list = (
            [AttachedCommentSummary.model_validate(item) for item in ac_raw]
            if isinstance(ac_raw, list)
            else []
        )
        messages.append(
            ChatMessage(
                message_id=m["message_id"],
                workspace_id=m["workspace_id"],
                role=m["role"],
                content=m["content"],
                run_id=m["run_id"],
                artifact_version_ids=loads(m["artifact_version_ids_json"], []),
                follow_up=fu_obj,
                attached_comments=ac_list,
                created_at=m["created_at"],
            )
        )
    runs = [_row_to_run(r) for r in run_rows]
    events_by_run: dict[str, list[RunEvent]] = {}
    for er in ev_rows:
        ev = _row_to_event(er)
        events_by_run.setdefault(ev.run_id, []).append(ev)

    comments_by_version: dict[str, list[VisualComment]] = {}
    for cr in comment_rows:
        vc = _row_to_comment(cr)
        comments_by_version.setdefault(vc.artifact_version_id, []).append(vc)

    versions = list_workspace_artifact_versions(workspace_id)

    return WorkspaceDetailResponse(
        workspace=_row_to_workspace(ws_row),
        messages=messages,
        runs=runs,
        events_by_run=events_by_run,
        artifact_versions=versions,
        comments_by_version=comments_by_version,
    )


@router.post("/workspaces/{workspace_id}/tasks", response_model=TaskRun)
async def submit_task(workspace_id: str, req: CreateTaskRequest) -> TaskRun:
    with get_conn() as conn:
        ws_row = conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if not ws_row:
            raise HTTPException(status_code=404, detail="Workspace not found")

    all_pending_map: dict[str, list[dict[str, Any]]] = (
        _load_pending_comments(workspace_id) if req.apply_pending_comments else {}
    )
    explicit_targets = set(req.selected_artifact_version_ids or [])
    if req.target_artifact_version_id:
        explicit_targets.add(req.target_artifact_version_id)

    if explicit_targets and any(avid in all_pending_map for avid in explicit_targets):
        captured_pending_map = {
            avid: clist for avid, clist in all_pending_map.items() if avid in explicit_targets
        }
    else:
        captured_pending_map = all_pending_map
    attached_comments_meta: list[dict[str, Any]] = []
    consumed_cids: list[str] = []
    if captured_pending_map:
        av_titles: dict[str, str] = {}
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT artifact_version_id, title FROM artifact_versions WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall():
                av_titles[r["artifact_version_id"]] = r["title"]
        for avid, clist in captured_pending_map.items():
            for c in clist:
                consumed_cids.append(c["comment_id"])
                attached_comments_meta.append(
                    {
                        "comment_id": c["comment_id"],
                        "artifact_version_id": avid,
                        "artifact_title": av_titles.get(avid, ""),
                        "text": c["text"],
                    }
                )

    user_text = req.prompt.strip()
    if not user_text and not attached_comments_meta:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    effective_prompt = (
        user_text
        if user_text
        else "Apply all attached visual comments to the commented chart(s)."
    )
    exec_req = req.model_copy(update={"prompt": effective_prompt})

    run_id = f"run_{uuid.uuid4().hex[:10]}"
    task_group_id = f"task_{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()

    # Auto-title workspace on first prompt if still default
    if ws_row["title"] in ("New Research Workspace", "Research Workspace"):
        new_title = effective_prompt.splitlines()[0][:64]
        with get_conn() as conn:
            conn.execute(
                "UPDATE workspaces SET title = ?, updated_at = ? WHERE workspace_id = ?",
                (new_title, now, workspace_id),
            )

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO task_runs (
                run_id, workspace_id, task_group_id, prompt, status, stage,
                produced_version_ids_json, error_message, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', NULL, 0, ?, ?)
            """,
            (run_id, workspace_id, task_group_id, effective_prompt, "queued", "created", now, now),
        )
        if consumed_cids:
            placeholders = ",".join("?" for _ in consumed_cids)
            conn.execute(
                f"UPDATE visual_comments SET status = 'addressed' WHERE comment_id IN ({placeholders})",
                tuple(consumed_cids),
            )

    _add_chat_message(
        workspace_id,
        "user",
        user_text,
        run_id=run_id,
        metadata={"attached_comments": attached_comments_meta} if attached_comments_meta else None,
    )
    _emit_event(
        run_id,
        workspace_id,
        "Orchestrator",
        "created",
        "queued",
        "Task queued for Multi-Agent Harness execution.",
    )

    orchestrator = ResearchOrchestrator()
    _track_task(
        orchestrator.execute_run(
            run_id,
            workspace_id,
            task_group_id,
            exec_req,
            submitted_comments=captured_pending_map,
        )
    )

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


@router.get("/runs/{run_id}", response_model=TaskRun)
async def get_run(run_id: str) -> TaskRun:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
    return _row_to_run(row)


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str) -> EventSourceResponse:
    with get_conn() as conn:
        run_row = conn.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run_row:
            raise HTTPException(status_code=404, detail="Run not found")
        existing_rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
        ).fetchall()

    existing_events = [_row_to_event(r) for r in existing_rows]
    is_terminal = run_row["status"] in ("completed", "failed", "cancelled")

    async def event_generator():
        sent_ids: set[str] = set()
        for ev in existing_events:
            sent_ids.add(ev.event_id)
            yield {"event": "run_event", "data": ev.model_dump_json()}

        if is_terminal:
            yield {"event": "done", "data": json.dumps({"run_id": run_id, "status": run_row["status"]})}
            return

        queue = EVENT_BUS.subscribe(run_id)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    with get_conn() as conn:
                        final_row = conn.execute(
                            "SELECT status FROM task_runs WHERE run_id = ?", (run_id,)
                        ).fetchone()
                    final_status = final_row["status"] if final_row else "completed"
                    yield {"event": "done", "data": json.dumps({"run_id": run_id, "status": final_status})}
                    break
                if item.event_id in sent_ids:
                    continue
                sent_ids.add(item.event_id)
                yield {"event": "run_event", "data": item.model_dump_json()}
        finally:
            EVENT_BUS.unsubscribe(run_id, queue)

    return EventSourceResponse(event_generator())


@router.get("/workspaces/{workspace_id}/canvas", response_model=CanvasResponse)
async def get_canvas(workspace_id: str) -> CanvasResponse:
    with get_conn() as conn:
        ws_row = conn.execute(
            "SELECT workspace_id FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if not ws_row:
            raise HTTPException(status_code=404, detail="Workspace not found")

    versions = list_workspace_artifact_versions(workspace_id)
    edges = [
        {"from": v.parent_version_id, "to": v.artifact_version_id}
        for v in versions
        if v.parent_version_id
    ]
    return CanvasResponse(workspace_id=workspace_id, artifact_versions=versions, edges=edges)


_DEBUG_BACK_SNIPPET = (
    '<a href="/?debug=1" '
    'style="position:fixed;top:10px;right:12px;z-index:2147483647;'
    'padding:5px 11px;border-radius:999px;background:rgba(15,23,42,0.88);'
    'color:#ffffff;font:600 12px/1.2 -apple-system,BlinkMacSystemFont,sans-serif;'
    'text-decoration:none;border:1px solid rgba(255,255,255,0.22);'
    'box-shadow:0 4px 12px rgba(15,23,42,0.2);">'
    '&larr; Back to Debug Inspector</a>'
)


@router.get("/versions/{version_id}/html")
async def get_version_html(
    version_id: str,
    debug_back: bool = Query(default=False),
) -> HTMLResponse:
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    html_path = RUNTIME_DIR / av.html_ref
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML artifact file missing")
    content = html_path.read_text(encoding="utf-8")
    if 'data-rc-bridge="2"' not in content:
        content = re.sub(
            r'<script\s+data-rc-bridge="1">.*?</script>',
            "",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if "</body>" in content:
            content = content.replace("</body>", f"{IFRAME_VIEW_STATE_BRIDGE}\n</body>", 1)
        elif "</BODY>" in content:
            content = content.replace("</BODY>", f"{IFRAME_VIEW_STATE_BRIDGE}\n</BODY>", 1)
        else:
            content = f"{content}\n{IFRAME_VIEW_STATE_BRIDGE}"

    if debug_back:
        if "</body>" in content:
            content = content.replace("</body>", f"{_DEBUG_BACK_SNIPPET}</body>", 1)
        elif "</BODY>" in content:
            content = content.replace("</BODY>", f"{_DEBUG_BACK_SNIPPET}</BODY>", 1)
        else:
            content = f"{content}\n{_DEBUG_BACK_SNIPPET}"
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
                "connect-src 'none'; frame-ancestors 'self';"
            ),
        },
    )


@router.get("/versions/{version_id}/thumbnail")
async def get_version_thumbnail(version_id: str) -> Response:
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    thumb_path = RUNTIME_DIR / av.thumbnail_ref
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail PNG missing")
    return Response(content=thumb_path.read_bytes(), media_type="image/png")


@router.get("/versions/{version_id}/lineage")
async def get_version_lineage(version_id: str):
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    graph = load_lineage(version_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Lineage graph not found")
    return graph


@router.get("/versions/{version_id}/source")
async def get_version_source(version_id: str) -> dict[str, Any]:
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    html_path = RUNTIME_DIR / av.html_ref
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML source file missing")
    html_text_src = html_path.read_text(encoding="utf-8")
    deps = [
        f"/vendor/{name} (inlined automatically in standalone Download)"
        for name in sorted(set(re.findall(r"/vendor/([A-Za-z0-9._-]+\.js)", html_text_src)))
    ]
    if not deps:
        deps = ["Self-contained HTML5 / SVG / Canvas (zero external runtime dependencies)"]
    return {
        "artifact_version_id": av.artifact_version_id,
        "title": av.title,
        "version_number": av.version_number,
        "filename": f"{av.artifact_version_id}_v{av.version_number}.html",
        "html": html_text_src,
        "external_dependencies": deps,
    }


@router.get("/versions/{version_id}/download")
async def download_version(
    version_id: str,
    format: str = Query(default="html", pattern="^(html|zip)$"),
) -> Response:
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    if format == "zip":
        data = build_provenance_zip_bytes(av)
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{av.artifact_version_id}_v{av.version_number}_bundle.zip"'
            },
        )
    html_path = RUNTIME_DIR / av.html_ref
    standalone = make_standalone_html(html_path.read_text(encoding="utf-8"))
    return Response(
        content=standalone.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{av.artifact_version_id}_v{av.version_number}.html"'
        },
    )


@router.delete("/versions/{version_id}")
async def remove_version(version_id: str) -> dict[str, Any]:
    ok = delete_artifact_version(version_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    return {"deleted": True, "artifact_version_id": version_id}


@router.get("/versions/{version_id}/comments", response_model=list[VisualComment])
async def list_version_comments(version_id: str) -> list[VisualComment]:
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM visual_comments WHERE artifact_version_id = ? ORDER BY created_at ASC",
            (version_id,),
        ).fetchall()
    return [_row_to_comment(r) for r in rows]


@router.post("/versions/{version_id}/comments", response_model=VisualComment)
async def create_version_comment(version_id: str, req: CreateCommentRequest) -> VisualComment:
    """
    Saves a spatially anchored visual comment + contextual annotated screenshot
    STRICTLY WITHOUT creating a new ArtifactVersion (§2 #8 & §3.4).
    """
    av = get_artifact_version(version_id)
    if not av:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")

    comment_id = f"comment_{uuid.uuid4().hex[:10]}"
    now = utc_now_iso()
    screenshot_rel = f"comments/{comment_id}.png"
    screenshot_abs = RUNTIME_DIR / screenshot_rel

    html_path = RUNTIME_DIR / av.html_ref
    html_text = html_path.read_text(encoding="utf-8") if html_path.exists() else ""

    fs = req.preview_view_state.filter_state or {}
    if isinstance(fs, dict) and fs.get("surface") == "data_map":
        import html as html_lib

        lineage_graph = load_lineage(version_id)
        sf = lineage_graph.semantic_flow if lineage_graph else None
        node_lbl = html_lib.escape(str(fs.get("node_label") or req.optional_element_hint or "Data Map Flow"))
        node_kind = html_lib.escape(str(fs.get("node_kind") or "semantic_flow"))
        comment_esc = html_lib.escape(req.text.strip())
        if sf:
            stats_line = (
                f"Input: {sf.total_input_count:,} {html_lib.escape(sf.record_unit)} · "
                f"Retained: {sf.total_retained_count:,} · Excluded: {sf.total_excluded_count:,}"
            )
            cats_html = "".join(
                f'<div style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">'
                f'<strong>{html_lib.escape(c.label)}</strong>'
                f'<div style="font-size:12px;color:#475569;">Raw {c.raw_count} · Retained {c.retained_count} · Excluded {c.excluded_count}</div>'
                f'</div>'
                for c in sf.categories[:6]
            )
            excl_html = "".join(
                f'<div style="padding:8px 12px;border:1px solid #fdba74;border-radius:10px;background:#fff7ed;">'
                f'<strong>{html_lib.escape(o.label)}</strong>'
                f'<div style="font-size:12px;color:#9a3412;">{o.count} excluded · {html_lib.escape(o.filter_rule)}</div>'
                f'</div>'
                for o in sf.outcomes
                if o.outcome_kind == "excluded"
            )
        else:
            stats_line = f"{av.title} · Legacy Data Lineage"
            cats_html = ""
            excl_html = ""
        html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Data Map Comment Context</title></head>
<body style="margin:0;padding:28px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;color:#0f172a;">
  <div style="max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#2563eb;">Data Map Context Snapshot · {node_kind}</div>
        <h2 style="margin:4px 0;">{html_lib.escape(av.title)} (v{av.version_number}) — Target: {node_lbl}</h2>
        <div style="font-size:13px;color:#475569;">{stats_line}</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">{cats_html}{excl_html}</div>
    <div style="padding:12px 14px;border-radius:10px;border:1px solid #bfdbfe;background:#eff6ff;font-size:13px;">
      <strong>Comment:</strong> {comment_esc}
    </div>
  </div>
</body></html>"""

    vc = VisualComment(
        comment_id=comment_id,
        artifact_version_id=version_id,
        workspace_id=av.workspace_id,
        text=req.text.strip(),
        anchor=req.anchor,
        screenshot_ref=screenshot_rel,
        preview_view_state=req.preview_view_state,
        optional_element_hint=req.optional_element_hint,
        status="pending",
        resolved_in_version_id=None,
        created_at=now,
    )

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO visual_comments (
                comment_id, artifact_version_id, workspace_id, text,
                anchor_json, screenshot_ref, preview_view_state_json,
                optional_element_hint, status, resolved_in_version_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?)
            """,
            (
                vc.comment_id,
                vc.artifact_version_id,
                vc.workspace_id,
                vc.text,
                vc.anchor.model_dump_json(),
                vc.screenshot_ref,
                vc.preview_view_state.model_dump_json(),
                vc.optional_element_hint,
                vc.created_at,
            ),
        )

    # Seed immediate placeholder PNG from existing artifact thumbnail (<1ms) so file path exists immediately,
    # then overwrite asynchronously with the scroll-locked, tooltip-hovered, red-reticle Chromium capture.
    screenshot_abs.parent.mkdir(parents=True, exist_ok=True)
    thumb_abs = RUNTIME_DIR / av.thumbnail_ref
    if thumb_abs.exists():
        screenshot_abs.write_bytes(thumb_abs.read_bytes())
    else:
        screenshot_abs.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            )
        )

    schedule_comment_screenshot(
        comment_id,
        {
            "html_text": html_text,
            "screenshot_path": screenshot_abs,
            "x_norm": req.anchor.x_norm,
            "y_norm": req.anchor.y_norm,
            "viewport_width": req.preview_view_state.viewport_width,
            "viewport_height": req.preview_view_state.viewport_height,
            "scroll_x": req.preview_view_state.scroll_x,
            "scroll_y": req.preview_view_state.scroll_y,
            "filter_state": req.preview_view_state.filter_state,
        },
    )
    return vc


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM visual_comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Comment not found")
        if row["status"] == "addressed":
            raise HTTPException(
                status_code=400,
                detail="Addressed historical comments are immutable and cannot be deleted.",
            )
        conn.execute("DELETE FROM visual_comments WHERE comment_id = ?", (comment_id,))
    return {"deleted": True, "comment_id": comment_id}


@router.get("/comments/{comment_id}/screenshot")
async def get_comment_screenshot(comment_id: str) -> Response:
    await await_comment_screenshots([comment_id])
    with get_conn() as conn:
        row = conn.execute(
            "SELECT screenshot_ref FROM visual_comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    shot_path = RUNTIME_DIR / row["screenshot_ref"]
    if not shot_path.exists():
        raise HTTPException(status_code=404, detail="Comment screenshot missing")
    return Response(content=shot_path.read_bytes(), media_type="image/png")


@router.patch("/versions/{version_id}/position", response_model=ArtifactVersion)
async def update_version_position(version_id: str, req: UpdateCanvasPositionRequest) -> ArtifactVersion:
    updated = update_artifact_canvas_position(version_id, req.x, req.y)
    if not updated:
        raise HTTPException(status_code=404, detail="ArtifactVersion not found")
    return updated
