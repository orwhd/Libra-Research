from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from ..config import get_reproduce_root
from ..schemas import Artifact, ArtifactKindLiteral, UploadReceipt, ValidationStatusLiteral
from .db import get_reproduce_conn, jdumps, jloads, utc_now_iso

MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024

ALLOWED_UPLOAD_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".parquet": "application/vnd.apache.parquet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".py": "text/x-python",
    ".zip": "application/zip",
}


def sanitize_filename(raw_name: str) -> str:
    base = Path(raw_name.replace("\\", "/")).name.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    cleaned = cleaned.lstrip(".")
    return cleaned[:120] or "artifact.bin"


class ArtifactValidationError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ArtifactStore:
    """
    Scoped artifact storage under `<reproduce_root>/runs/{run_id}/artifacts/{artifact_id}/`.
    Never exposes raw host filesystem paths via API responses.
    """

    def __init__(self, root_dir: Path | None = None, db_path: Path | None = None) -> None:
        self.root_dir = (root_dir or get_reproduce_root()).resolve()
        self.db_path = db_path

    def run_dir(self, run_id: str) -> Path:
        safe_run = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
        p = (self.root_dir / "runs" / safe_run).resolve()
        p.relative_to(self.root_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def register_bytes(
        self,
        *,
        run_id: str,
        kind: ArtifactKindLiteral,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        validation_status: ValidationStatusLiteral = "pending",
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> tuple[Artifact, dict[str, Any]]:
        aid = artifact_id or f"art_{uuid.uuid4().hex[:12]}"
        safe_name = sanitize_filename(filename)
        art_dir = self.run_dir(run_id) / "artifacts" / aid
        art_dir.mkdir(parents=True, exist_ok=True)
        dest = art_dir / safe_name
        dest.write_bytes(content)

        sha = hashlib.sha256(content).hexdigest()
        guessed_mime = (
            mime_type
            or ALLOWED_UPLOAD_EXTENSIONS.get(Path(safe_name).suffix.lower())
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        rel_path = str(dest.relative_to(self.root_dir))
        now = utc_now_iso()
        meta = {
            "filename": safe_name,
            "size_bytes": len(content),
            **(metadata or {}),
        }

        with get_reproduce_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reproduce_artifacts (
                    artifact_id, run_id, kind, filename, rel_path, sha256,
                    mime_type, size_bytes, validation_status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    run_id,
                    kind,
                    safe_name,
                    rel_path,
                    sha,
                    guessed_mime,
                    len(content),
                    validation_status,
                    jdumps(meta),
                    now,
                ),
            )

        art = Artifact(
            artifact_id=aid,
            run_id=run_id,
            kind=kind,
            sha256=sha,
            mime_type=guessed_mime,
            validation_status=validation_status,
            download_url=f"/api/artifacts/{aid}/download",
        )
        return art, meta

    def register_file(
        self,
        *,
        run_id: str,
        kind: ArtifactKindLiteral,
        source_path: Path,
        validation_status: ValidationStatusLiteral = "pending",
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> tuple[Artifact, dict[str, Any]]:
        return self.register_bytes(
            run_id=run_id,
            kind=kind,
            filename=source_path.name,
            content=source_path.read_bytes(),
            validation_status=validation_status,
            metadata=metadata,
            artifact_id=artifact_id,
        )

    def save_upload_attachment(
        self,
        *,
        run_id: str,
        filename: str,
        content: bytes,
        declared_mime: str | None = None,
    ) -> UploadReceipt:
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ArtifactValidationError(
                413,
                f"Uploaded file ({len(content) / (1024 * 1024):.1f} MB) exceeds 64 MB limit.",
            )
        safe_name = sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ArtifactValidationError(
                415,
                f"Unsupported attachment extension '{ext}'. Allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS.keys())}",
            )
        canonical_mime = ALLOWED_UPLOAD_EXTENSIONS[ext]
        if ext == ".pdf" and b"%PDF-" not in content[:1024]:
            raise ArtifactValidationError(415, "Invalid PDF header in uploaded file.")

        art, _ = self.register_bytes(
            run_id=run_id,
            kind="dataset" if ext in (".csv", ".tsv", ".parquet", ".json") else "other",
            filename=safe_name,
            content=content,
            mime_type=canonical_mime,
            validation_status="pending",
            metadata={"is_user_attachment": True, "declared_mime": declared_mime or ""},
        )
        return UploadReceipt(
            artifact_id=art.artifact_id,
            sha256=art.sha256,
            mime_type=canonical_mime,
            size_bytes=len(content),
            filename=safe_name,
        )

    def update_validation_status(
        self,
        artifact_ids: list[str],
        status: ValidationStatusLiteral,
    ) -> None:
        if not artifact_ids:
            return
        with get_reproduce_conn(self.db_path) as conn:
            placeholders = ",".join("?" for _ in artifact_ids)
            conn.execute(
                f"UPDATE reproduce_artifacts SET validation_status = ? WHERE artifact_id IN ({placeholders})",
                (status, *artifact_ids),
            )

    def list_run_artifacts(self, run_id: str) -> list[Artifact]:
        with get_reproduce_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM reproduce_artifacts WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def list_run_artifacts_rich(self, run_id: str) -> list[dict[str, Any]]:
        with get_reproduce_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM reproduce_artifacts WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            art = self._row_to_artifact(r)
            meta = jloads(r["metadata_json"], {})
            out.append(
                {
                    **art.model_dump(),
                    "filename": r["filename"],
                    "size_bytes": r["size_bytes"],
                    "created_at": r["created_at"],
                    "reproduction_level": meta.get("reproduction_level"),
                    "description": meta.get("description", ""),
                }
            )
        return out

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return self._row_to_artifact(row) if row else None

    def resolve_artifact_bytes(self, artifact_id: str) -> tuple[Path, str, str] | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if not row:
            return None
        target = (self.root_dir / row["rel_path"]).resolve()
        target.relative_to(self.root_dir)
        if not target.exists():
            return None
        return target, str(row["filename"]), str(row["mime_type"] or "application/octet-stream")

    @staticmethod
    def _row_to_artifact(row: Any) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            sha256=row["sha256"],
            mime_type=row["mime_type"],
            validation_status=row["validation_status"],
            download_url=f"/api/artifacts/{row['artifact_id']}/download",
        )
