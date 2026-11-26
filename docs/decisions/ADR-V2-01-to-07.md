# Architecture Decision Records — Reproduce V2 (ADR-V2-01 .. ADR-V2-07)

## ADR-V2-01 — Canvas Renderer: Lightweight SVG + Compositor Transforms (No React Flow Graph)
- **Level**: L3
- **Current proposal**: Render `LivingResearchCanvas` using a `1000×600` world-coordinate SVG table layer + lightweight persona components porting `agent.js` strokes/morphing, without React Flow node-edge graphs.
- **Alternative**: `@xyflow/react` node-link canvas from V1.
- **Evidence**: Frozen L1.5 forbids permanent edges, workflow nodes, and canvas editing; existing `Coding_test/frontend` already runs cleanly without `@xyflow/react`.
- **Impact on frozen L1/L1.5**: Enforces L1.5 read-only, zero-edge, temporary-table visual grammar.
- **Needs user approval?**: No.

## ADR-V2-02 — Agent Identity: Visual Persona Roster Decoupled from Runtime Agent Instances
- **Level**: L2 / L3
- **Current proposal**: Render all 6 configured personas (`Main`, `Paper`, `Data`, `Analysis`, `Validation`, `Support`) at stable home anchors immediately on workspace load (`runtime_agent_id: null`), binding `runtime_agent_id` only when Coordinator/Scheduler dispatches real work.
- **Alternative**: Progressive `agent.spawned` node creation from V1 demo scenes.
- **Evidence**: Frozen L1.5 §2 mandates initial full-roster presence while L1 forbids faking `agent.spawned` or auto-starting paid LLM calls on `POST /api/runs`.
- **Needs user approval?**: No.

## ADR-V2-03 — Temporary Research Table Events & 64-Point Seeded Morphing
- **Level**: L3 / L4
- **Current proposal**: Drive all temporary tables strictly from `collaboration.started`, `collaboration.members_changed`, and `collaboration.ended` (with monotonic `revision` and `source_ref` linking back to `debate`/`pod`/`task`), sampling 64 perimeter points seeded by `collaboration_id` to morph continuously across 2-member rounded rectangle, 3–4-member circle, and 5–6-member ellipse.
- **Alternative**: Separate visual zones for debates vs. pods or remounting SVG paths on member count changes.
- **Evidence**: Validated against `contracts/collaboration-payload.schema.json` and `UI-04..UI-08`.
- **Needs user approval?**: No.

## ADR-V2-04 — Left-Chat Consolidation & In-Chat Document Viewer
- **Level**: L2 / L4
- **Current proposal**: Place all research communication, collapsible process activity, Research Gain / Evidence / Scheduler resource disclosures, deduplicated Artifact file cards, and the `InChatDocumentViewer` inside the left Chat column; right Canvas carries zero document cards or side inspectors.
- **Alternative**: Right-hand Inspector, Timeline, and Research Gain panels from V1.
- **Evidence**: Frozen L1.5 §1 & §6.
- **Needs user approval?**: No.

## ADR-V2-05 — Append-Only SQLite EventStore + Resumable SSE + Non-Executing Snapshot/Replay
- **Level**: L3 / L4
- **Current proposal**: Commit domain mutations and `HarnessEvent` rows atomically in SQLite with `UNIQUE(run_id, seq)` and `(run_id, request_id)` command idempotency; stream via SSE (`id: seq`, `after` / `Last-Event-ID`) and reconstruct `RunSnapshot` + `CanvasProjection` purely from stored state/events without invoking SDK or Python runners.
- **Alternative**: WebSockets or in-memory-only event queues.
- **Evidence**: Matches `contracts/openapi.yaml` & `contracts/event.schema.json` (`API-01..API-04`).
- **Needs user approval?**: No.

## ADR-V2-06 — Controlled `ClaudeSDKClient` (`0.2.189`) + Dual-Pool Scheduler + Portable Live Tool Fallback
- **Level**: L3 / L4
- **Current proposal**: Lock `claude-agent-sdk==0.2.189` using `ClaudeSDKClient` + restricted `create_sdk_mcp_server("science", ...)` + `disallowed_tools` (blocking built-in `Bash`, `Write`, `Edit`, `Read`, `Task`, `Agent`, `WebFetch`, etc.) + `can_use_tool` `ToolPermissionGate` + `SafeExperimentRunner` + `ResourceScheduler` (`max_active_agent_tasks=4`, `max_parallel_experiments=2`). Provide an OpenAI-compatible tool-calling live runner over the identical `ToolPermissionGate` & `ResourceScheduler` when a user runs on a machine with `LLM_API_KEY` (e.g., DeepSeek/OpenAI) instead of Claude CLI OAuth, and isolate `MockSimulationProvider` strictly to `mode="simulation"`.
- **Alternative**: Relying on `allowed_tools` alone or hard-failing when switching between Claude OAuth and configured API keys.
- **Evidence**: Live smoke test verified `claude-agent-sdk==0.2.189` custom MCP tool execution on this host; `api_settings.py` also holds user-configured `deepseek-flash`.
- **Needs user approval?**: No.

## ADR-V2-07 — Dual-Mode Packaging (`libra_reproduce` Standalone Backend + Fullstack Integration)
- **Level**: L3
- **Current proposal**: Implement the complete Project 2 backend as a self-contained package (`libra_reproduce`) with zero imports from Project 1's orchestrator/UI, mount its router into `Coding_test/backend/app/main.py` for fullstack operation, and package it as a standalone Git repository (`/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`) with its own `pyproject.toml`, `uvicorn` server, headless CLI, tests, contracts, and generated Nature 2019 artifacts/reports.
- **Alternative**: Tightly coupling Project 2 logic inside Project 1's `routes.py` and `orchestrator.py`.
- **Evidence**: Directly fulfills user requirement for a pure-backend runnable interface usable both with the website frontend and standalone in its own Git repo.
- **Needs user approval?**: No.
