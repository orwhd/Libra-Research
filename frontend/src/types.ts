export type RunStage =
  | 'created'
  | 'planning'
  | 'collecting'
  | 'analyzing'
  | 'generating_html'
  | 'validating'
  | 'retrying'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'retrying'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface AnchorPoint {
  x_norm: number;
  y_norm: number;
}

export interface PreviewViewState {
  viewport_width: number;
  viewport_height: number;
  scroll_x: number;
  scroll_y: number;
  zoom: number;
  filter_state: Record<string, unknown>;
}

export interface CanvasPosition {
  group_id: string;
  row: number;
  revision_column: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ArtifactVersion {
  artifact_version_id: string;
  artifact_id: string;
  workspace_id: string;
  task_group_id: string;
  parent_version_id: string | null;
  version_number: number;
  title: string;
  chart_kind: string;
  kind: string;
  status: 'draft' | 'validating' | 'validated' | 'failed';
  html_ref: string;
  thumbnail_ref: string;
  dataset_snapshot_ids: string[];
  analysis_run_ids: string[];
  lineage_ref: string;
  pending_comment_count: number;
  addressed_comment_count: number;
  addressed_comment_ids: string[];
  canvas: CanvasPosition;
  validation_report: Record<string, unknown>;
  summary: string;
  created_at: string;
}

export interface VisualComment {
  comment_id: string;
  artifact_version_id: string;
  workspace_id: string;
  text: string;
  anchor: AnchorPoint;
  screenshot_ref: string;
  preview_view_state: PreviewViewState;
  optional_element_hint: string | null;
  status: 'pending' | 'addressed';
  resolved_in_version_id: string | null;
  created_at: string;
}

export interface LineageNode {
  node_id: string;
  stage:
    | 'source'
    | 'collection'
    | 'transformation'
    | 'dataset_snapshot'
    | 'analysis_run'
    | 'visualization_artifact';
  label: string;
  subtitle: string;
  status: string;
  metadata: Record<string, unknown>;
}

export interface LineageEdge {
  edge_id: string;
  source: string;
  target: string;
  relation: string;
}

export interface LineageGraph {
  artifact_version_id: string;
  generated_at: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface RunEvent {
  event_id: string;
  run_id: string;
  workspace_id: string;
  agent_name: string;
  stage: RunStage;
  status: RunStatus;
  timestamp: string;
  concise_summary: string;
  details: Record<string, unknown>;
  optional_artifact_refs: string[];
  optional_error: string | null;
}

export interface TaskRun {
  run_id: string;
  workspace_id: string;
  task_group_id: string;
  prompt: string;
  status: RunStatus;
  stage: RunStage;
  produced_version_ids: string[];
  error_message: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
}

export interface FollowUpOption {
  label: string;
  prompt: string;
}

export interface FollowUpSuggestion {
  question: string;
  options: FollowUpOption[];
}

export interface AttachedCommentSummary {
  comment_id: string;
  artifact_version_id: string;
  artifact_title?: string;
  text: string;
}

export interface ChatMessage {
  message_id: string;
  workspace_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  run_id: string | null;
  artifact_version_ids: string[];
  follow_up?: FollowUpSuggestion | null;
  attached_comments?: AttachedCommentSummary[];
  created_at: string;
}

export interface Workspace {
  workspace_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceDetailResponse {
  workspace: Workspace;
  messages: ChatMessage[];
  runs: TaskRun[];
  events_by_run: Record<string, RunEvent[]>;
  artifact_versions: ArtifactVersion[];
  comments_by_version: Record<string, VisualComment[]>;
}

export type DockTab = 'preview' | 'data_map' | 'source';
