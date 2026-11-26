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

export type FlowOutcomeKind = 'retained' | 'excluded' | 'transformed' | 'unclassified';

export interface FlowSubcategory {
  id: string;
  label: string;
  raw_count: number;
  retained_count: number;
  excluded_count: number;
  rule_summary: string;
}

export interface FlowSourceNode {
  id: string;
  label: string;
  provider: string;
  source_url: string;
  retrieval_mode: string;
  raw_count: number;
  record_unit: string;
  snapshot_id: string;
}

export interface FlowCategoryNode {
  id: string;
  label: string;
  description: string;
  field_basis: string;
  rule_summary: string;
  confidence: 'high' | 'medium' | 'low';
  verifiable_status: 'verified_field' | 'derived_rule' | 'unverified';
  dataset_version: string;
  raw_count: number;
  retained_count: number;
  excluded_count: number;
  transformed_count: number;
  is_unclassified: boolean;
  subcategories: FlowSubcategory[];
  secondary_tags: { label: string; count: number }[];
}

export interface FlowOutcomeCategoryRef {
  category_id: string;
  category_label: string;
  count: number;
}

export interface FlowOutcomeNode {
  id: string;
  outcome_kind: FlowOutcomeKind;
  label: string;
  count: number;
  stage: string;
  reason_code: string;
  reason_label: string;
  filter_rule: string;
  affected_categories: FlowOutcomeCategoryRef[];
}

export interface FlowDatasetNode {
  id: string;
  snapshot_id: string;
  label: string;
  version_label: string;
  retained_count: number;
  record_unit: string;
  schema_field_count: number;
  transform_steps: string[];
  created_at: string;
}

export interface FlowAnalysisNode {
  id: string;
  analysis_run_id: string;
  label: string;
  algorithm: string;
  input_count: number;
  input_unit: string;
  output_count: number;
  output_unit: string;
  unit_changed: boolean;
  conversion_explanation: string;
  summary_headline: string;
  key_metrics: Record<string, unknown>;
  artifact_version_id: string;
  artifact_title: string;
  chart_kind: string;
}

export interface FlowLink {
  id: string;
  source_id: string;
  target_id: string;
  source_col: number;
  target_col: number;
  value: number;
  outcome_kind: FlowOutcomeKind;
  category_id: string | null;
  label: string;
  unit_changed: boolean;
  conversion_note: string;
}

export interface ConservationReport {
  passed: boolean;
  is_exact_conservation: boolean;
  unit_conversion_explained: boolean;
  input_count: number;
  retained_count: number;
  excluded_count: number;
  delta: number;
  notes: string[];
}

export interface SemanticDataFlow {
  status: 'complete' | 'partial' | 'unavailable';
  unavailable_reason: string | null;
  record_unit: string;
  total_input_count: number;
  total_retained_count: number;
  total_excluded_count: number;
  conservation_check: ConservationReport;
  sources: FlowSourceNode[];
  categories: FlowCategoryNode[];
  outcomes: FlowOutcomeNode[];
  datasets: FlowDatasetNode[];
  analyses: FlowAnalysisNode[];
  links: FlowLink[];
}

export interface LineageGraph {
  artifact_version_id: string;
  generated_at: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
  lineage_schema_version?: number;
  semantic_flow?: SemanticDataFlow | null;
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

export type WorkspaceMode = 'data' | 'reproduce';

export interface Workspace {
  workspace_id: string;
  title: string;
  workspace_type?: WorkspaceMode;
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

export type ThinkingEffort = 'low' | 'medium' | 'high';

export interface LLMSettings {
  configured: boolean;
  provider: string;
  base_url: string;
  model: string;
  thinking_effort: ThinkingEffort;
  max_concurrency: number;
  available_models: string[];
  api_key_masked: string;
  has_env_fallback: boolean;
}

export interface UpdateLLMSettingsPayload {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
  thinking_effort: ThinkingEffort;
  max_concurrency?: number;
  available_models: string[];
}

export type TrendingChartIconKind =
  | 'trend'
  | 'network'
  | 'bundling'
  | 'heatmap'
  | 'flow'
  | 'hierarchy'
  | 'scatter'
  | 'radial';

export interface TrendingChartItem {
  id: string;
  title: string;
  icon_kind: TrendingChartIconKind;
  summary: string;
  interactions: string[];
  prompt: string;
  source_label: string;
  source_url: string;
  schematic_svg: string;
}

export interface WeeklyTrendingChartsResponse {
  iso_week: string;
  fetched_at: string;
  expires_at: string;
  cached: boolean;
  sources: string[];
  items: TrendingChartItem[];
}

export type RecommendedPaperIconKind = 'paper' | 'code' | 'experiment' | 'model';

export interface RecommendedPaperItem {
  id: string;
  title: string;
  full_title: string;
  icon_kind: RecommendedPaperIconKind;
  year: number | null;
  venue: string;
  citations: number;
  topic_tag: string;
  prompt: string;
  source_url: string;
}

export interface RecommendedPapersResponse {
  fingerprint: string;
  fetched_at: string;
  expires_at: string;
  cached: boolean;
  workspace_count: number;
  topics: string[];
  sources: string[];
  items: RecommendedPaperItem[];
}
