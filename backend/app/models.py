from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


RunStage = Literal[
    "created",
    "planning",
    "collecting",
    "analyzing",
    "generating_html",
    "validating",
    "retrying",
    "completed",
    "failed",
    "cancelled",
]

RunStatus = Literal["queued", "running", "retrying", "completed", "failed", "cancelled"]
ArtifactStatus = Literal["draft", "validating", "validated", "failed"]
CommentStatus = Literal["pending", "addressed"]


class AnchorPoint(BaseModel):
    x_norm: float = Field(ge=0.0, le=1.0)
    y_norm: float = Field(ge=0.0, le=1.0)


class PreviewViewState(BaseModel):
    viewport_width: int = 1280
    viewport_height: int = 720
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    zoom: float = 1.0
    filter_state: dict[str, Any] = Field(default_factory=dict)


class CanvasPosition(BaseModel):
    group_id: str
    row: int
    revision_column: int
    x: float
    y: float
    width: int = 340
    height: int = 246


class UpdateCanvasPositionRequest(BaseModel):
    x: float
    y: float


class ArtifactVersion(BaseModel):
    artifact_version_id: str
    artifact_id: str
    workspace_id: str
    task_group_id: str
    parent_version_id: str | None = None
    version_number: int = 1
    title: str
    chart_kind: str
    kind: str = "interactive_html"
    status: ArtifactStatus = "validated"
    html_ref: str
    thumbnail_ref: str
    dataset_snapshot_ids: list[str] = Field(default_factory=list)
    analysis_run_ids: list[str] = Field(default_factory=list)
    lineage_ref: str
    pending_comment_count: int = 0
    addressed_comment_count: int = 0
    addressed_comment_ids: list[str] = Field(default_factory=list)
    canvas: CanvasPosition
    validation_report: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    created_at: str


class VisualComment(BaseModel):
    comment_id: str
    artifact_version_id: str
    workspace_id: str
    text: str
    anchor: AnchorPoint
    screenshot_ref: str
    preview_view_state: PreviewViewState = Field(default_factory=PreviewViewState)
    optional_element_hint: str | None = None
    status: CommentStatus = "pending"
    resolved_in_version_id: str | None = None
    created_at: str


class CreateCommentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    anchor: AnchorPoint
    preview_view_state: PreviewViewState = Field(default_factory=PreviewViewState)
    optional_element_hint: str | None = None


class LineageNode(BaseModel):
    node_id: str
    stage: Literal[
        "source",
        "collection",
        "transformation",
        "dataset_snapshot",
        "analysis_run",
        "visualization_artifact",
    ]
    label: str
    subtitle: str = ""
    status: str = "verified"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    edge_id: str
    source: str
    target: str
    relation: str


FlowOutcomeKind = Literal["retained", "excluded", "transformed", "unclassified"]


class FlowSubcategory(BaseModel):
    id: str
    label: str
    raw_count: int
    retained_count: int
    excluded_count: int
    rule_summary: str = ""


class FlowSourceNode(BaseModel):
    id: str
    label: str
    provider: str
    source_url: str = ""
    retrieval_mode: str = ""
    raw_count: int
    record_unit: str = "records"
    snapshot_id: str


class FlowCategoryNode(BaseModel):
    id: str
    label: str
    description: str
    field_basis: str
    rule_summary: str
    confidence: Literal["high", "medium", "low"]
    verifiable_status: Literal["verified_field", "derived_rule", "unverified"]
    dataset_version: str
    raw_count: int
    retained_count: int
    excluded_count: int
    transformed_count: int = 0
    is_unclassified: bool = False
    subcategories: list[FlowSubcategory] = Field(default_factory=list)
    secondary_tags: list[dict[str, Any]] = Field(default_factory=list)


class FlowOutcomeCategoryRef(BaseModel):
    category_id: str
    category_label: str
    count: int


class FlowOutcomeNode(BaseModel):
    id: str
    outcome_kind: FlowOutcomeKind
    label: str
    count: int
    stage: str
    reason_code: str = ""
    reason_label: str = ""
    filter_rule: str = ""
    affected_categories: list[FlowOutcomeCategoryRef] = Field(default_factory=list)


class FlowDatasetNode(BaseModel):
    id: str
    snapshot_id: str
    label: str
    version_label: str
    retained_count: int
    record_unit: str = "records"
    schema_field_count: int = 0
    transform_steps: list[str] = Field(default_factory=list)
    created_at: str = ""


class FlowAnalysisNode(BaseModel):
    id: str
    analysis_run_id: str
    label: str
    algorithm: str
    input_count: int
    input_unit: str = "records"
    output_count: int
    output_unit: str = "records"
    unit_changed: bool = False
    conversion_explanation: str = ""
    summary_headline: str = ""
    key_metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_version_id: str = ""
    artifact_title: str = ""
    chart_kind: str = ""


class FlowLink(BaseModel):
    id: str
    source_id: str
    target_id: str
    source_col: int
    target_col: int
    value: int
    outcome_kind: FlowOutcomeKind
    category_id: str | None = None
    label: str = ""
    unit_changed: bool = False
    conversion_note: str = ""


class ConservationReport(BaseModel):
    passed: bool
    is_exact_conservation: bool
    unit_conversion_explained: bool
    input_count: int
    retained_count: int
    excluded_count: int
    delta: int
    notes: list[str] = Field(default_factory=list)


class SemanticDataFlow(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    unavailable_reason: str | None = None
    record_unit: str = "records"
    total_input_count: int
    total_retained_count: int
    total_excluded_count: int
    conservation_check: ConservationReport
    sources: list[FlowSourceNode] = Field(default_factory=list)
    categories: list[FlowCategoryNode] = Field(default_factory=list)
    outcomes: list[FlowOutcomeNode] = Field(default_factory=list)
    datasets: list[FlowDatasetNode] = Field(default_factory=list)
    analyses: list[FlowAnalysisNode] = Field(default_factory=list)
    links: list[FlowLink] = Field(default_factory=list)


class LineageGraph(BaseModel):
    artifact_version_id: str
    generated_at: str
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    lineage_schema_version: int = 1
    semantic_flow: SemanticDataFlow | None = None


class RunEvent(BaseModel):
    event_id: str
    run_id: str
    workspace_id: str
    agent_name: str
    stage: RunStage
    status: RunStatus
    timestamp: str
    concise_summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    optional_artifact_refs: list[str] = Field(default_factory=list)
    optional_error: str | None = None


class TaskRun(BaseModel):
    run_id: str
    workspace_id: str
    task_group_id: str
    prompt: str
    status: RunStatus
    stage: RunStage
    produced_version_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None
    retry_count: int = 0
    created_at: str
    updated_at: str


class FollowUpOption(BaseModel):
    label: str
    prompt: str


class FollowUpSuggestion(BaseModel):
    question: str
    options: list[FollowUpOption] = Field(default_factory=list)


class AttachedCommentSummary(BaseModel):
    comment_id: str
    artifact_version_id: str
    artifact_title: str = ""
    text: str


class ChatMessage(BaseModel):
    message_id: str
    workspace_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    run_id: str | None = None
    artifact_version_ids: list[str] = Field(default_factory=list)
    follow_up: FollowUpSuggestion | None = None
    attached_comments: list[AttachedCommentSummary] = Field(default_factory=list)
    created_at: str


class Workspace(BaseModel):
    workspace_id: str
    title: str
    created_at: str
    updated_at: str


class CreateWorkspaceRequest(BaseModel):
    title: str = "New Research Workspace"


class UploadedFileItem(BaseModel):
    relative_path: str = Field(min_length=1, max_length=400)
    content_base64: str


class CreateUploadRequest(BaseModel):
    label: str = Field(default="Uploaded Files", max_length=180)
    files: list[UploadedFileItem] = Field(min_length=1, max_length=200)


class CreateUrlUploadRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


class UploadSummaryResponse(BaseModel):
    upload_id: str
    label: str
    file_count: int
    files: list[str]


class CreateTaskRequest(BaseModel):
    prompt: str = Field(default="", max_length=6000)
    target_artifact_version_id: str | None = None
    selected_artifact_version_ids: list[str] = Field(default_factory=list)
    apply_pending_comments: bool = True
    inject_validation_fault_once: bool = False
    upload_id: str | None = None


class WorkspaceDetailResponse(BaseModel):
    workspace: Workspace
    messages: list[ChatMessage]
    runs: list[TaskRun]
    events_by_run: dict[str, list[RunEvent]]
    artifact_versions: list[ArtifactVersion]
    comments_by_version: dict[str, list[VisualComment]]


class CanvasResponse(BaseModel):
    workspace_id: str
    artifact_versions: list[ArtifactVersion]
    edges: list[dict[str, str]]


ThinkingEffort = Literal["low", "medium", "high"]


class LLMSettingsResponse(BaseModel):
    configured: bool
    provider: str = "deepseek"
    base_url: str
    model: str
    thinking_effort: ThinkingEffort = "medium"
    available_models: list[str] = Field(default_factory=list)
    api_key_masked: str = ""
    has_env_fallback: bool = False


class UpdateLLMSettingsRequest(BaseModel):
    provider: str = Field(default="deepseek", max_length=60)
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    thinking_effort: ThinkingEffort = "medium"
    available_models: list[str] = Field(default_factory=list)


class UpdateLLMSelectionRequest(BaseModel):
    model: str | None = Field(default=None, max_length=160)
    thinking_effort: ThinkingEffort | None = None


class FetchModelsRequest(BaseModel):
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(min_length=8, max_length=500)


class FetchModelsResponse(BaseModel):
    models: list[str] = Field(default_factory=list)
    count: int = 0


TrendingChartIconKind = Literal[
    "trend",
    "network",
    "bundling",
    "heatmap",
    "flow",
    "hierarchy",
    "scatter",
    "radial",
]


class TrendingChartItem(BaseModel):
    id: str
    title: str
    icon_kind: TrendingChartIconKind
    summary: str
    interactions: list[str] = Field(default_factory=list)
    prompt: str
    source_label: str
    source_url: str = ""
    schematic_svg: str = ""


class WeeklyTrendingChartsResponse(BaseModel):
    iso_week: str
    fetched_at: str
    expires_at: str
    cached: bool
    sources: list[str] = Field(default_factory=list)
    items: list[TrendingChartItem] = Field(default_factory=list)
