import type {
  AnchorPoint,
  ArtifactVersion,
  LineageGraph,
  PreviewViewState,
  RunEvent,
  TaskRun,
  VisualComment,
  Workspace,
  WorkspaceDetailResponse,
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

export function createWorkspace(title = 'New Research Workspace'): Promise<Workspace> {
  return jsonFetch<Workspace>('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
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

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

export async function uploadDatasetFiles(
  label: string,
  fileList: FileList
): Promise<{ upload_id: string; label: string; file_count: number; files: string[] }> {
  const items: { relative_path: string; content_base64: string }[] = [];
  for (const file of Array.from(fileList)) {
    const rel =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    if (!rel || rel.split('/').some((seg) => seg.startsWith('.'))) continue;
    if (file.size > 15 * 1024 * 1024) continue;
    const buf = await file.arrayBuffer();
    items.push({
      relative_path: rel,
      content_base64: arrayBufferToBase64(buf),
    });
    if (items.length >= 150) break;
  }
  if (items.length === 0) {
    throw new Error('No readable files selected (hidden/oversized files skipped).');
  }
  return jsonFetch('/api/uploads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, files: items }),
  });
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
