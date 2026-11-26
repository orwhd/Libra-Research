from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunMode = Literal["live", "simulation"]
RunStatusLiteral = Literal["created", "running", "paused", "completed", "failed"]
VisualStateLiteral = Literal[
    "idle",
    "assigned",
    "working",
    "waiting",
    "debating",
    "reporting",
    "handoff",
    "paused",
    "failed",
    "completed",
]
CollaborationKindLiteral = Literal["debate", "pod", "collaboration"]
CollaborationStatusLiteral = Literal["active", "ending", "ended"]
ArtifactKindLiteral = Literal["dataset", "script", "figure", "report", "log", "other"]
ValidationStatusLiteral = Literal["pending", "passed", "failed", "inconclusive"]
SelectedContextKindLiteral = Literal[
    "agent",
    "task",
    "pod",
    "debate",
    "artifact",
    "paper",
    "collaboration",
]
EventSubjectKindLiteral = Literal[
    "run",
    "agent",
    "task",
    "debate",
    "pod",
    "artifact",
    "evidence",
    "paper",
    "collaboration",
]
EventSourceLiteral = Literal["harness", "sdk", "runner", "validator", "user"]


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    paper_id: str = "paper_A"


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatusLiteral
    created_at: str
    mode: RunMode
    title: str | None = None


class SelectedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SelectedContextKindLiteral
    id: str


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    selected_context: SelectedContext | None = None
    attachment_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    status: Literal["answered", "accepted", "queued", "requires_approval", "rejected"]
    message: str
    reason_code: str | None = None
    last_seq: int | None = None


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["debate", "pod", "task", "collaboration"]
    id: str = Field(min_length=1)


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    label: str = Field(min_length=1)
    runtime_agent_id: str | None = None
    state: VisualStateLiteral = "idle"
    is_main: bool = False
    task_ids: list[str] = Field(default_factory=list)


class Collaboration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collaboration_id: str = Field(min_length=1)
    kind: CollaborationKindLiteral
    status: CollaborationStatusLiteral
    topic: str
    participant_persona_ids: list[str] = Field(min_length=1)
    ref: SourceRef
    last_event_seq: int = Field(ge=0)


class CanvasProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    at_seq: int = Field(ge=0)
    mode: RunMode
    personas: list[Persona]
    collaborations: list[Collaboration]


class RunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    at_seq: int = Field(ge=0)
    agents: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    debates: list[dict[str, Any]]
    pods: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    resources: dict[str, Any]
    canvas: CanvasProjection | None = None


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    kind: ArtifactKindLiteral
    sha256: str
    mime_type: str | None = None
    validation_status: ValidationStatusLiteral
    download_url: str | None = None


class ReportStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_started", "generating", "ready", "failed"]
    artifact_ids: list[str]
    missing_requirements: list[str]


class ChatActivityMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    run_id: str
    kind: Literal["message", "activity", "file", "system"]
    role: Literal["user", "main", "activity", "system"]
    created_at: str
    text: str
    related_refs: list[SelectedContext] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    seq: int | None = Field(default=None, ge=0)


class ChatMessagePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatActivityMessage]
    next_cursor: str | None = None


class UploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    sha256: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    filename: str | None = None


class EventSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EventSubjectKindLiteral
    id: str = Field(min_length=1)


class HarnessEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    occurred_at: str
    type: str = Field(pattern=r"^[a-z]+(?:[._][a-z_]+)+$")
    actor_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    subject: EventSubject | None = None
    status: str | None = None
    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    mode: RunMode
    source: EventSourceLiteral | None = None
