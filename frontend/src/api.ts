import type {
  AnchorPoint,
  ArtifactVersion,
  LineageGraph,
  LLMSettings,
  PreviewViewState,
  RecommendedPapersResponse,
  RunEvent,
  TaskRun,
  ThinkingEffort,
  UpdateLLMSettingsPayload,
  VisualComment,
  WeeklyTrendingChartsResponse,
  Workspace,
  WorkspaceDetailResponse,
  WorkspaceMode,
} from './types';

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`HTTP ${res.status}: ${txt}`);
  }
  return res.json() as Promise<T>;
}

export function listWorkspaces(): Promise<Workspace[]> {
  return jsonFetch<Workspace[]>('/api/workspaces');
}

export function createWorkspace(
  title = 'New Research Workspace',
  workspaceType: WorkspaceMode = 'data'
): Promise<Workspace> {
  return jsonFetch<Workspace>('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, workspace_type: workspaceType }),
  });
}

export function getWorkspaceDetail(workspaceId: string): Promise<WorkspaceDetailResponse> {
  return jsonFetch<WorkspaceDetailResponse>(`/api/workspaces/${encodeURIComponent(workspaceId)}`);
}

export function submitTask(
  workspaceId: string,
  payload: {
    prompt: string;
    target_artifact_version_id?: string | null;
    selected_artifact_version_ids?: string[];
    apply_pending_comments?: boolean;
    upload_id?: string | null;
  }
): Promise<TaskRun> {
  return jsonFetch<TaskRun>(`/api/workspaces/${encodeURIComponent(workspaceId)}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

const MAX_UPLOAD_FILE_BYTES = 64 * 1024 * 1024;
const MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024;

export async function uploadDatasetFiles(
  label: string,
  fileList: FileList
): Promise<{ upload_id: string; label: string; file_count: number; files: string[] }> {
  const rawFiles = Array.from(fileList);
  const isSingle = rawFiles.length === 1;
  const formData = new FormData();
  formData.append('label', label);

  const oversizedNames: string[] = [];
  const hiddenNames: string[] = [];
  let includedCount = 0;
  let totalBytes = 0;

  for (const file of rawFiles) {
    let rel =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    rel = rel.replace(/\\/g, '/').replace(/^\/+/, '');
    if (!rel) continue;

    const segments = rel.split('/');
    if (segments.some((seg) => seg === '..')) continue;
    if (segments.some((seg) => seg === 'node_modules' || seg === '__pycache__' || seg === '.git' || seg === 'dist')) {
      hiddenNames.push(rel);
      continue;
    }
    if (segments.some((seg) => seg.startsWith('.'))) {
      if (isSingle) {
        rel = segments.map((seg) => seg.replace(/^\.+/, '') || 'uploaded_file').join('/');
      } else {
        hiddenNames.push(rel);
        continue;
      }
    }

    if (file.size > MAX_UPLOAD_FILE_BYTES) {
      oversizedNames.push(`${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`);
      continue;
    }
    totalBytes += file.size;
    if (totalBytes > MAX_UPLOAD_TOTAL_BYTES) {
      oversizedNames.push(`${file.name} (batch exceeds 100 MB total limit)`);
      continue;
    }

    formData.append('files', file, file.name);
    formData.append('relative_paths', rel);
    includedCount += 1;
    if (includedCount >= 150) break;
  }

  if (includedCount === 0) {
    if (oversizedNames.length > 0) {
      throw new Error(
        `Selected file exceeds the 64 MB limit: ${oversizedNames.slice(0, 3).join(', ')}.`
      );
    }
    if (hiddenNames.length > 0) {
      throw new Error(
        `No readable non-hidden files found (skipped: ${hiddenNames.slice(0, 3).join(', ')}).`
      );
    }
    throw new Error('No readable files selected.');
  }

  const res = await fetch('/api/uploads/multipart', {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    let detailMsg = '';
    try {
      const parsed = (await res.json()) as { detail?: string };
      detailMsg = parsed.detail || '';
    } catch {
      detailMsg = await res.text();
    }
    throw new Error(detailMsg || `Upload failed (HTTP ${res.status}).`);
  }
  return res.json();
}

export function attachDatasetUrl(
  url: string
): Promise<{ upload_id: string; label: string; file_count: number; files: string[] }> {
  return jsonFetch('/api/uploads/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
}

export function subscribeToRunEvents(
  runId: string,
  onEvent: (ev: RunEvent) => void,
  onDone: (status: string) => void,
  onError: (err: unknown) => void
): () => void {
  const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  es.addEventListener('run_event', (e: MessageEvent) => {
    try {
      const ev = JSON.parse(e.data) as RunEvent;
      onEvent(ev);
    } catch (err) {
      onError(err);
    }
  });
  es.addEventListener('done', (e: MessageEvent) => {
    try {
      const d = JSON.parse(e.data) as { status?: string };
      onDone(d.status || 'completed');
    } catch {
      onDone('completed');
    } finally {
      es.close();
    }
  });
  es.onerror = (err) => {
    onError(err);
    es.close();
  };
  return () => es.close();
}

export function getVersionLineage(versionId: string): Promise<LineageGraph> {
  return jsonFetch<LineageGraph>(`/api/versions/${encodeURIComponent(versionId)}/lineage`);
}

export function getVersionSource(versionId: string): Promise<{
  artifact_version_id: string;
  title: string;
  version_number: number;
  filename: string;
  html: string;
  external_dependencies: string[];
}> {
  return jsonFetch(`/api/versions/${encodeURIComponent(versionId)}/source`);
}

export function listVersionComments(versionId: string): Promise<VisualComment[]> {
  return jsonFetch<VisualComment[]>(`/api/versions/${encodeURIComponent(versionId)}/comments`);
}

export function createVersionComment(
  versionId: string,
  payload: {
    text: string;
    anchor: AnchorPoint;
    preview_view_state: PreviewViewState;
    optional_element_hint?: string | null;
  }
): Promise<VisualComment> {
  return jsonFetch<VisualComment>(`/api/versions/${encodeURIComponent(versionId)}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function deleteComment(commentId: string): Promise<{ deleted: boolean; comment_id: string }> {
  return jsonFetch(`/api/comments/${encodeURIComponent(commentId)}`, {
    method: 'DELETE',
  });
}

export function updateVersionPosition(
  versionId: string,
  x: number,
  y: number
): Promise<ArtifactVersion> {
  return jsonFetch<ArtifactVersion>(`/api/versions/${encodeURIComponent(versionId)}/position`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x, y }),
  });
}

export function deleteArtifactVersion(
  versionId: string
): Promise<{ deleted: boolean; artifact_version_id: string }> {
  return jsonFetch(`/api/versions/${encodeURIComponent(versionId)}`, {
    method: 'DELETE',
  });
}

export function getLLMSettings(): Promise<LLMSettings> {
  return jsonFetch<LLMSettings>('/api/settings/llm');
}

export function updateLLMSettings(payload: UpdateLLMSettingsPayload): Promise<LLMSettings> {
  return jsonFetch<LLMSettings>('/api/settings/llm', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function resetLLMSettings(): Promise<LLMSettings> {
  return jsonFetch<LLMSettings>('/api/settings/llm', {
    method: 'DELETE',
  });
}

export function testLLMSettings(
  payload: UpdateLLMSettingsPayload
): Promise<{ ok: boolean; model: string }> {
  return jsonFetch('/api/settings/llm/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function fetchLLMModels(payload: {
  api_key: string;
  base_url: string;
}): Promise<{ models: string[]; count: number }> {
  return jsonFetch('/api/settings/llm/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function updateLLMSelection(payload: {
  model?: string;
  thinking_effort?: ThinkingEffort;
}): Promise<LLMSettings> {
  return jsonFetch<LLMSettings>('/api/settings/llm/selection', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function getWeeklyTrendingCharts(): Promise<WeeklyTrendingChartsResponse> {
  return jsonFetch<WeeklyTrendingChartsResponse>('/api/trending-charts');
}

export function getRecommendedPapers(force = false): Promise<RecommendedPapersResponse> {
  const qs = force ? '?force=true' : '';
  return jsonFetch<RecommendedPapersResponse>(`/api/recommended-papers${qs}`);
}
