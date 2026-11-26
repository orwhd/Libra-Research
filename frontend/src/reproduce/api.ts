import type {
  ArtifactPreviewPayload,
  ChatCommand,
  ChatMessagePage,
  ChatResponse,
  HarnessEvent,
  ReproduceArtifact,
  ReproduceRun,
  ReproduceRunMode,
  RunSnapshot,
  RunTimelineResponse,
  UploadReceipt,
} from './types';

async function repFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`HTTP ${res.status}: ${txt}`);
  }
  return res.json() as Promise<T>;
}

export function bindWorkspaceReproduceRun(
  workspaceId: string,
  title?: string,
  mode: ReproduceRunMode = 'live'
): Promise<ReproduceRun> {
  const params = new URLSearchParams({ mode });
  if (title) params.set('title', title);
  return repFetch<ReproduceRun>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/reproduce-run?${params.toString()}`,
    { method: 'POST' }
  );
}

export function getReproduceRun(runId: string): Promise<ReproduceRun> {
  return repFetch<ReproduceRun>(`/api/runs/${encodeURIComponent(runId)}`);
}

export function getReproduceSnapshot(runId: string, atSeq?: number): Promise<RunSnapshot> {
  const qs = typeof atSeq === 'number' ? `?at_seq=${encodeURIComponent(String(atSeq))}` : '';
  return repFetch<RunSnapshot>(`/api/runs/${encodeURIComponent(runId)}/snapshot${qs}`);
}

export function submitReproduceChat(
  runId: string,
  command: ChatCommand
): Promise<ChatResponse> {
  return repFetch<ChatResponse>(`/api/runs/${encodeURIComponent(runId)}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(command),
  });
}

export function cancelReproduceRun(runId: string): Promise<ReproduceRun> {
  return repFetch<ReproduceRun>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  });
}

export function listReproduceMessages(
  runId: string,
  filter?: { ref_kind?: string; ref_id?: string; limit?: number }
): Promise<ChatMessagePage> {
  const params = new URLSearchParams();
  if (filter?.ref_kind) params.set('ref_kind', filter.ref_kind);
  if (filter?.ref_id) params.set('ref_id', filter.ref_id);
  if (filter?.limit) params.set('limit', String(filter.limit));
  const qs = params.toString() ? `?${params.toString()}` : '';
  return repFetch<ChatMessagePage>(`/api/runs/${encodeURIComponent(runId)}/messages${qs}`);
}

export function listReproduceArtifactsDetail(runId: string): Promise<ReproduceArtifact[]> {
  return repFetch<ReproduceArtifact[]>(
    `/api/runs/${encodeURIComponent(runId)}/artifacts-detail`
  );
}

export function getArtifactPreview(artifactId: string): Promise<ArtifactPreviewPayload> {
  return repFetch<ArtifactPreviewPayload>(
    `/api/artifacts/${encodeURIComponent(artifactId)}/preview`
  );
}

export function getReproduceTimeline(runId: string): Promise<RunTimelineResponse> {
  return repFetch<RunTimelineResponse>(`/api/runs/${encodeURIComponent(runId)}/timeline`);
}

export async function uploadRunAttachment(
  runId: string,
  file: File
): Promise<UploadReceipt> {
  const fd = new FormData();
  fd.append('file', file);
  return repFetch<UploadReceipt>(`/api/runs/${encodeURIComponent(runId)}/attachments`, {
    method: 'POST',
    body: fd,
  });
}

export function importUploadToRun(
  runId: string,
  uploadId: string
): Promise<UploadReceipt[]> {
  return repFetch<UploadReceipt[]>(
    `/api/runs/${encodeURIComponent(runId)}/import-upload/${encodeURIComponent(uploadId)}`,
    { method: 'POST' }
  );
}

/**
 * Read-only SSE subscription with automatic `after=N` reconnect, `(run_id, seq)` deduplication,
 * and gap detection callback (`onGapDetected` -> resync snapshot without re-running tools).
 */
export function subscribeReproduceEvents(
  runId: string,
  initialAfterSeq: number,
  callbacks: {
    onEvent: (ev: HarnessEvent) => void;
    onConnectionChange?: (connected: boolean) => void;
    onGapDetected?: () => void;
  }
): () => void {
  let closed = false;
  let lastSeq = initialAfterSeq;
  let es: EventSource | null = null;
  let reconnectTimer: number | null = null;
  const seenSeqs = new Set<number>();

  const connect = () => {
    if (closed) return;
    const url = `/api/runs/${encodeURIComponent(runId)}/events?after=${lastSeq}`;
    es = new EventSource(url);

    es.onopen = () => {
      callbacks.onConnectionChange?.(true);
    };

    const handleMessage = (e: MessageEvent) => {
      if (closed) return;
      try {
        const ev = JSON.parse(e.data) as HarnessEvent;
        if (ev.run_id !== runId || ev.seq <= initialAfterSeq && seenSeqs.has(ev.seq)) {
          return;
        }
        if (ev.seq <= lastSeq || seenSeqs.has(ev.seq)) {
          return;
        }
        if (lastSeq > 0 && ev.seq > lastSeq + 1) {
          callbacks.onGapDetected?.();
        }
        seenSeqs.add(ev.seq);
        lastSeq = ev.seq;
        callbacks.onEvent(ev);
        if (ev.type === 'run.completed' || ev.type === 'run.failed') {
          es?.close();
          es = null;
        }
      } catch {
        /* ignore malformed frame */
      }
    };

    es.addEventListener('harness_event', handleMessage as EventListener);
    es.onmessage = handleMessage;

    es.onerror = () => {
      callbacks.onConnectionChange?.(false);
      es?.close();
      es = null;
      if (!closed) {
        reconnectTimer = window.setTimeout(connect, 1400);
      }
    };
  };

  connect();

  return () => {
    closed = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
    }
    es?.close();
  };
}
