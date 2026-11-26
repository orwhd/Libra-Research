export type ReproduceRunMode = 'live' | 'simulation';
export type ReproduceRunStatus = 'created' | 'running' | 'paused' | 'completed' | 'failed';

export type AgentRole = 'main' | 'paper' | 'data' | 'analysis' | 'validation' | 'support' | string;

export type VisualState =
  | 'idle'
  | 'assigned'
  | 'working'
  | 'waiting'
  | 'debating'
  | 'reporting'
  | 'handoff'
  | 'paused'
  | 'failed'
  | 'completed';

export type CollaborationKind = 'debate' | 'pod' | 'collaboration';

export type SelectedContextKind =
  | 'agent'
  | 'task'
  | 'pod'
  | 'debate'
  | 'artifact'
  | 'paper'
  | 'collaboration';

export interface SelectedContext {
  kind: SelectedContextKind;
  id: string;
}

export interface SourceRef {
  kind: 'debate' | 'pod' | 'task' | 'collaboration';
  id: string;
}

export interface Persona {
  persona_id: string;
  role: AgentRole;
  label: string;
  runtime_agent_id: string | null;
  state: VisualState;
  is_main: boolean;
  task_ids: string[];
}

export interface Collaboration {
  collaboration_id: string;
  kind: CollaborationKind;
  status: 'active' | 'ending' | 'ended';
  topic: string;
  participant_persona_ids: string[];
  ref: SourceRef;
  last_event_seq: number;
  revision?: number;
}

export interface CanvasProjection {
  run_id: string;
  at_seq: number;
  mode: ReproduceRunMode;
  personas: Persona[];
  collaborations: Collaboration[];
}

export interface ReproduceRun {
  run_id: string;
  status: ReproduceRunStatus;
  created_at: string;
  mode: ReproduceRunMode;
  title?: string | null;
}

export interface ChatCommand {
  request_id: string;
  text: string;
  selected_context?: SelectedContext | null;
  attachment_ids?: string[];
}

export interface ChatResponse {
  command_id: string;
  status: 'answered' | 'accepted' | 'queued' | 'requires_approval' | 'rejected';
  message: string;
  reason_code?: string | null;
  last_seq?: number | null;
}

export interface RunSnapshot {
  run_id: string;
  at_seq: number;
  agents: Record<string, unknown>[];
  tasks: {
    id: string;
    persona_id?: string;
    role: string;
    objective: string;
    status: string;
    is_exploration?: boolean;
    artifact_ids?: string[];
  }[];
  debates: {
    id: string;
    claim_id?: string;
    claim: string;
    participants?: string[];
    turns?: {
      speaker: string;
      stance: string;
      public_summary: string;
      evidence_ref?: string | null;
    }[];
    status: string;
    resolution_summary?: string;
  }[];
  pods: {
    id: string;
    research_question: string;
    members?: string[];
    budget_fraction?: number;
    status: string;
    outcome_summary?: string;
  }[];
  evidence: {
    id?: string;
    claim_id: string;
    dataset_manifest_id?: string;
    script_hash?: string | null;
    result_artifact_ids?: string[];
    status: string;
    reviewer_id?: string | null;
    details?: {
      checks?: Record<string, boolean>;
      reasons?: string[];
      reproduction_level?: string;
      research_gains?: {
        verified_insights?: string[];
        ruled_out_explanations?: string[];
        open_questions?: string[];
        methodology_lessons?: string[];
      };
    };
  }[];
  resources: {
    limits?: {
      max_active_agent_tasks?: number;
      max_parallel_experiments?: number;
      exploration_budget_fraction?: number;
    };
    active_agent_tasks?: number;
    queued_agent_tasks?: number;
    active_experiments?: number;
    queued_experiments?: number;
    exploration_used_fraction?: number;
  };
  canvas?: CanvasProjection | null;
}

export interface ReproduceArtifact {
  artifact_id: string;
  run_id: string;
  kind: 'dataset' | 'script' | 'figure' | 'report' | 'log' | 'other';
  sha256: string;
  mime_type?: string | null;
  validation_status: 'pending' | 'passed' | 'failed' | 'inconclusive';
  download_url?: string | null;
  filename?: string;
  size_bytes?: number;
  reproduction_level?: string | null;
  description?: string;
}

export interface ChatActivityMessage {
  message_id: string;
  run_id: string;
  kind: 'message' | 'activity' | 'file' | 'system';
  role: 'user' | 'main' | 'activity' | 'system';
  created_at: string;
  text: string;
  related_refs: SelectedContext[];
  artifact_ids: string[];
  seq?: number | null;
}

export interface ChatMessagePage {
  messages: ChatActivityMessage[];
  next_cursor: string | null;
}

export interface UploadReceipt {
  artifact_id: string;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  filename?: string;
}

export interface HarnessEvent {
  event_id: string;
  run_id: string;
  seq: number;
  occurred_at: string;
  type: string;
  actor_id?: string | null;
  target_ids: string[];
  subject?: {
    kind: string;
    id: string;
  } | null;
  status?: string | null;
  summary: string;
  payload: Record<string, unknown>;
  evidence_ids: string[];
  artifact_ids: string[];
  mode: ReproduceRunMode;
  source?: 'harness' | 'sdk' | 'runner' | 'validator' | 'user' | null;
}

export interface ArtifactPreviewPayload {
  artifact_id: string;
  filename: string;
  mime_type: string;
  preview_kind: 'text' | 'docx_outline' | 'pdf' | 'image' | 'download_only';
  text?: string;
  inline_url?: string;
  download_url?: string;
}

export type TimelineStage = 'setup' | 'briefing' | 'debate' | 'intervention' | 'synthesis';

export interface TimelineSnapshotNode {
  snapshot_id: string;
  seq: number;
  kind:
    | 'run_start'
    | 'briefing'
    | 'intervention'
    | 'huddle_start'
    | 'huddle_expand'
    | 'huddle_end'
    | 'run_completed';
  stage?: TimelineStage;
  duration_sec?: number;
  label: string;
  reason?: string;
  report?: string;
  collaboration_id?: string | null;
  briefing_id?: string | null;
  intervention_id?: string | null;
  participants: string[];
  linked_step_ids: string[];
}

export interface BriefingRecord {
  briefing_id: string;
  seq: number;
  presenter_ids: string[];
  presenter_labels: string[];
  topic: string;
  summary: string;
  turns?: TableDialogueTurn[];
  artifact_ids: string[];
  linked_step_ids: string[];
}

export interface HumanInterventionRecord {
  intervention_id: string;
  seq: number;
  resolved_seq?: number | null;
  status: 'pending' | 'resolved';
  debate_id: string;
  collaboration_id?: string | null;
  from_persona_ids: string[];
  from_labels: string[];
  to_persona_id: string;
  dissent_summary: string;
  question: string;
  options: { label: string; prompt: string }[];
  user_decision?: string | null;
  linked_step_ids: string[];
}

export interface TableDialogueTurn {
  seq: number;
  speaker_id: string;
  speaker_label: string;
  stance: string;
  summary: string;
}

export interface TableDialogueRecord {
  collaboration_id: string;
  kind: string;
  topic: string;
  participants: string[];
  start_reason: string;
  end_report: string;
  turns: TableDialogueTurn[];
}

export interface ChatTimelineStep {
  step_id: string;
  status: 'working' | 'done';
  actor_label: string;
  text: string;
  artifact_ids: string[];
  collaboration_id?: string | null;
  debate_summary?: string | null;
  snapshot_seq?: number | null;
}

export interface RunTimelineResponse {
  run_id: string;
  latest_seq: number;
  snapshots: TimelineSnapshotNode[];
  table_dialogues: Record<string, TableDialogueRecord>;
  briefings?: Record<string, BriefingRecord>;
  interventions?: Record<string, HumanInterventionRecord>;
  active_intervention?: HumanInterventionRecord | null;
  chat_steps: ChatTimelineStep[];
}
