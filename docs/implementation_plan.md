# Libra Research · Project 2 (Reproduce V2) Implementation Plan

**Date**: 2026-11-26  
**Authority Hierarchy**: Frozen L1.5 (`REPRODUCE_FULLSTACK_L1_5_TO_L4_V2.md`) > V1 Scientific & Harness Baseline (`FULL_SPEC_L1-L4.md`) > Local Implementation Defaults (recorded as ADRs).  
**Dual Deployment Target**:
1. **Integrated Fullstack Mode** inside `Coding_test` (`http://127.0.0.1:5173` + `http://127.0.0.1:8000`), preserving Project 1 (`Data` mode) and the completed `Upload Papers to Reproduce` homepage (`Reproduce` mode) with zero regressions.
2. **Standalone Backend-Only Mode & Independent Git Repository** (`/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`), exposing the exact OpenAPI 3.1 V2 HTTP+SSE service (`uvicorn libra_reproduce.main:app`) and headless CLI (`uv run python -m libra_reproduce.cli ...`) with zero frontend or Project 1 runtime coupling.

---

## 1. Repository Reconnaissance (Verified Ground Truth)

### 1.1 Existing Frontend (`Coding_test/frontend/`)
- **Stack**: `react@^19.0.0`, `react-dom@^19.0.0`, `typescript@~5.6.2`, `vite@^6.0.0`, `lucide-react@^0.460.0`, `react-markdown@^10.1.0`, `remark-gfm@^4.0.1`, `@playwright/test@^1.50.0`.
- **Completed Reproduce Homepage & Switch** (preserve untouched in layout/typography):
  - `frontend/src/components/LandingHero.tsx`: Implements `Ask Any Questions of Data` ↔ `Upload Papers to Reproduce` inline `<h1>` switch (`data-testid="landing-mode-switch"`) with View Transition morphing, per-mode prompt buffers, 2×2 hot recommended paper chips (no hover thumbnail popovers), mode-filtered `Existing Workspaces`, and reused `AttachmentControls` + `ModelThinkingSelector` + `ApiConfigModal`.
  - `frontend/src/App.tsx`: Persists `homeMode` (`data` | `reproduce`), hides the bottom-right `debug` button whenever `homeMode === 'reproduce'` or viewing a `reproduce` workspace, and currently routes all open workspaces into the Project 1 split view (`ChatSidebar` + `InfiniteCanvas`).
  - **What is static vs. live right now**: `LandingHero` (`Reproduce` mode), `GET /api/recommended-papers`, and `workspaces.workspace_type` separation are **LIVE and tested**. Entering a `reproduce` workspace currently falls back to Project 1's `InfiniteCanvas` / `ResearchOrchestrator` and does **not** yet mount the Reproduce V2 `ResearchWorkspace` (`ReproduceChatSidebar` + `LivingResearchCanvas`) or `/api/runs/*` Reproduce V2 harness.
- **Reusable UI Primitives & Theme Tokens**:
  - `frontend/src/styles.css`: Shared light surface palette (`--bg-page`, `--bg-surface`, `--bg-subtle`, `--ink-primary`, `--accent`, `--emerald`, `--amber`, `--crimson`), `.workspace-split`, `.workspace-resizer`, `.chat-sidebar`, `.chat-header`, `.chat-composer`, `.markdown-body`, `.visually-hidden`, and `WaveProgressText`.
  - `frontend/src/components/AttachmentControls.tsx` & `ApiConfigModal.tsx`: Directly reusable for safe paper/dataset file/URL attachment and runtime LLM configuration.
  - `/Users/zhich/Documents/项目/HCI/agent_motion/agent.js` & `styles.css` (identical to handoff `reference/agent_motion/`): Verified 6 persona role palettes (`main #FFD24A`, `paper #FF9FD8`, `data #7CC8FF`, `analysis #4FF5C8`, `validation #FF9A6B`, `support #B9A6FF`), hair/nose SVG paths, Main orbit stroke, and isomorphic facial expression morphing (`EXPR`, `STATES`).

### 1.2 Existing Backend (`Coding_test/backend/`)
- **Stack (`pyproject.toml`, Python 3.13 + `uv`)**:
  - Installed & locked: `fastapi`, `uvicorn`, `pydantic`, `openai==3.58.0`, `httpx`, `pandas==3.1.1`, `numpy`, `networkx`, `scipy==1.18.1`, `matplotlib==3.11.2`, `sse-starlette`, `claude-agent-sdk==0.2.189`, `python-docx==1.2.0`, `reportlab==5.1.2`, `pyarrow==26.0.1`, `jsonschema==4.26.0`, `pypdf==7.1.1`, `pyyaml==6.0.4`.
- **Route Namespace Audit (`backend/app/api/routes.py`)**:
  - Project 1 defines `GET /api/runs/{run_id}` (looking up `task_runs`) and `GET /api/runs/{run_id}/events` (Project 1 `run_event` SSE).
  - Project 1 does **not** define `POST /api/runs`, `POST /api/runs/{run_id}/chat`, `GET /api/runs/{run_id}/snapshot`, `GET /api/runs/{run_id}/messages`, `GET /api/runs/{run_id}/artifacts`, `GET /api/artifacts/{artifact_id}`, `GET /api/runs/{run_id}/report`, or `POST /api/runs/{run_id}/attachments`.
  - Adapter strategy: `GET /api/runs/{run_id}` and `GET /api/runs/{run_id}/events` check `reproduce_runs` first (returning OpenAPI V2 `Run` and `HarnessEvent` SSE with `Last-Event-ID`/`after` support) and fall through to Project 1 `task_runs` for Data workspaces. Zero route conflicts, zero Project 1 regressions.

---

## 2. Empirical Data Source & SDK Verification

### 2.1 Target Paper A & Verified Data Sources
- **Target Paper**: Lingfei Wu, Dashun Wang & James A. Evans (2019), *"Large teams develop and small teams disrupt science and technology"*, *Nature*, 566, 378–382, DOI `10.1038/s41586-019-0941-9` (OpenAlex ID `W2913773162`, verified 2019, 3 authors, 34 references, 1,381+ citations).
- **Author Harvard Dataverse (`https://doi.org/10.7910/DVN/JPWNNK`, Version 4.0, License `CC0-1.0` Public Domain)**:
  - Verified live via Dataverse API (`datasetId=4471484`) and HTTP `206 Partial Content` `Range` inspection on File ID `6925921` (`Aggregated_20210521.txt`, `850,713,255` bytes, MD5 `8dea725aa83835d57232dc96b50b75a7`).
  - **Critical Empirical Schema Finding**: Although the Dataverse prose description mentions 7 labels (`MAGPaperId, Year, Field, Team size, Multi-Institution or not, Citation, Disruption`), byte-level inspection at 0 MB, 50 MB, 200 MB, 400 MB, 600 MB, and 800 MB proves `Aggregated_20210521.txt` is headerless 6-column tab-separated text sorted chronologically by year (`1830`..`2021`):
    1. `mag_paper_id` (`int64`)
    2. `year` (`int32`, `1830..2021`)
    3. `field_code` (`str`/`int`, MAG Level-0 FOS code `0..18`, e.g., `5=physics`, `7=biology`, `10=engineering`, `14=computer science`)
    4. `team_size` (`int32`, distinct author count $\ge 1$)
    5. `citation_count` (`int32`, forward citations)
    6. `disruption` (`float64`, author-precomputed Funk & Owen-Smith disruption index $D \in [-1, 1]$)
- **Reproduction Levels Achieved Honestly (`S1` Primary + Bounded `S2` Verification)**:
  - **S1 (Primary Empirical Analysis — Author Precomputed $D$ on MAG)**: Deterministic HTTP Range stratified block sampling across `Aggregated_20210521.txt` (`1954–2014` historical window matching Nature 2019's WoS `1954–2014` cohort vs. `2015–2021` right-truncated window), persisted locally as compressed CSV/Parquet with exact byte-range manifest, SHA-256, raw/filtered counts, team-size bins (`1`, `2`, `3`, `4`, `5`, `6–10`, `11+`), mean/median/percentile curves, bootstrap 95% CIs, and discipline fixed-demean checks. Explicitly labeled **`S1` (reanalysis of author-published precomputed MAG disruption scores)** — never misrepresented as full-graph WoS recomputation.
  - **S2 (Bounded Independent $D$ Metric Recomputation + Citation-Window Sensitivity)**: Exact implementation of $D = \frac{n_i - n_j}{n_i + n_j + n_k}$ (`0` denominator $\rightarrow$ `NaN`/missing), verified against hand-calculated toy citation graphs (`SCI-02`) **and** executed on a documented bounded OpenAlex empirical citation-graph subset (`referenced_works` + forward `cites:` works comparing 5-year vs. 10-year/all-year observation windows), providing genuine empirical evidence for the `Analysis` vs. `Validation` citation-window debate!

### 2.2 Claude Agent SDK & Live Tool Harness Verification
- **Locked SDK Version**: `claude-agent-sdk==0.2.189` (`Claude Code CLI 2.1.169`, `loggedIn: True`, `firstParty` OAuth verified).
- **Live Smoke Test Passed**: Verified `ClaudeSDKClient` with `create_sdk_mcp_server(name="science", tools=[...])`, `allowed_tools=["mcp__science__*"]`, `disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookEdit", "Task", "Agent"]`, and `cwd` jailed to run workspace — real tool invocation `TOOL_CALLED: [{'paper_id': 'paper_A'}]` succeeded cleanly.
- **Portable Live Fallback for Standalone Repo**: `SDKAdapter` supports:
  1. `claude_agent_sdk` (`ClaudeSDKClient` + custom restricted MCP tools + `can_use_tool` permission callback + Hooks) when Claude auth / `ANTHROPIC_API_KEY` is available;
  2. `openai_tool_harness` (OpenAI-compatible tool-calling loop over the exact same restricted science tools & `ToolPermissionGate` + `Scheduler`) when running with user-configured DeepSeek/OpenAI keys (`LLM_API_KEY` / `user_api_config.json`) on machines without Claude CLI auth;
  3. `simulation` (`MockSimulationProvider`) **only** when explicitly requested (`mode="simulation"`) or for deterministic CI tests — never marked `live`.

---

## 3. Planned Vertical Slices & File Manifest

### Shared / Standalone-Ready Engine (`backend/libra_reproduce/` + mounted in `backend/app/reproduce/`)
To satisfy the requirement that Project 2 runs **both** with the Libra Research website frontend **and** as a standalone Git repository (`Libra-Reproduce-Backend`), the entire Project 2 backend lives in a self-contained package `libra_reproduce` inside `Coding_test/backend/libra_reproduce/` (with zero imports from `Coding_test/backend/app/agents/*`), mirrored cleanly into `/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`:

- `libra_reproduce/config.py` — Standalone env & path config (`REPRODUCE_RUNTIME_DIR`, `REPRODUCE_DB_PATH`, resource limits: `max_active_agent_tasks=4`, `max_parallel_experiments=2`, `max_subagent_depth=2`, `max_debate_turns=6`, `exploration_budget_fraction=0.20`, `max_task_retries=2`, `experiment_timeout_seconds=180`).
- `libra_reproduce/schemas.py` — Strict Pydantic v2 models generated from `contracts/openapi.yaml`, `contracts/event.schema.json`, `contracts/canvas-projection.schema.json`, and `contracts/collaboration-payload.schema.json`.
- `libra_reproduce/storage/`
  - `db.py` — Atomic SQLite schema (`reproduce_runs`, `reproduce_commands`, `reproduce_events`, `reproduce_tasks`, `reproduce_debates`, `reproduce_pods`, `reproduce_collaborations`, `reproduce_messages`, `reproduce_artifacts`, `reproduce_evidence`, `reproduce_leases`, `workspace_reproduce_links`).
  - `event_store.py` — Transactional state + append-only `(run_id, seq)` `EventStore`, `request_id` idempotency, `Last-Event-ID` / `after` SSE pub/sub, and zero-execution `RunSnapshot` + `CanvasProjection` projector.
  - `artifact_store.py` — Scoped artifact storage (`runs/{run_id}/artifacts/{artifact_id}/...`), SHA-256 hashing, MIME/path-traversal guards, safe download URLs (never leaking host paths).
- `libra_reproduce/security/`
  - `tool_permissions.py` — `ToolPermissionGate` (`can_use_tool` callback + explicit tool allowlist + path jail + secret-redacted audit traces).
  - `experiment_runner.py` — `SafeExperimentRunner` executing restricted Python analysis scripts in `-I` isolated subprocesses with stripped env (no API keys/HOME/SSH), AST policy check, timeout, and `Scheduler` experiment-slot lease enforcement.
- `libra_reproduce/science/`
  - `dataverse_loader.py` — Verified Harvard Dataverse `doi:10.7910/DVN/JPWNNK` (`Aggregated_20210521.txt`, `CC0-1.0`) stratified HTTP Range loader + local deterministic cache + `data_manifest.json` generator + user uploaded CSV/PDF paper parser.
  - `cd_index.py` — Independent S2 Funk & Owen-Smith Disruption Index $D = (n_i - n_j)/(n_i + n_j + n_k)$ (`0` denom $\rightarrow$ `NaN`), toy graph verifier (`SCI-02`), and bounded OpenAlex citation-window (`5y` vs `10y`) empirical evaluator.
  - `nature2019_analysis.py` — Team-size vs. disruption statistical pipeline (`analysis_team_size.csv`, `analysis_team_size.parquet`, `window_sensitivity.csv`, publication-grade Matplotlib figures `figure_team_size_disruption.png` and `figure_window_sensitivity.png`, reproducible standalone script `reproduce_analysis.py`).
  - `evidence_gate.py` — Independent `EvidenceGate` checking manifest provenance, row conservation, figure-vs-table numeric agreement, S1 vs. S2 claim boundaries, and rejecting consensus-without-artifact claims (`passed | failed | inconclusive`).
  - `report_builder.py` — Generates real, readable `reproduction_report.docx` (`python-docx`) and `reproduction_report.pdf` (`reportlab`) embedding real figures, sample $N$, methodology, S1/S2 scope, debate/sensitivity findings, limitations, and reproduction commands.
- `libra_reproduce/harness/`
  - `persona_catalog.py` — Default 6 visual personas (`persona_main`, `persona_paper`, `persona_data`, `persona_analysis`, `persona_validation`, `persona_support`) decoupled from `runtime_agent_id`.
  - `scheduler.py` — Dual-pool `ResourceScheduler` (`max_active_agent_tasks=4` independent from `max_parallel_experiments=2`, exploration budget cap `0.20`, queue/grant/deny/release on failure/cancel).
  - `collaboration_manager.py` — State machines for `Debate`, `Pod`, and `Collaboration` (`collaboration.started`, `collaboration.members_changed`, `collaboration.ended` with monotonic `revision` and `source_ref` deduplication).
  - `sdk_adapter.py` — Live `ClaudeSDKClient` (`claude-agent-sdk==0.2.189`) + `OpenAIToolHarness` adapter with restricted MCP science tools, `ToolPermissionGate`, and redacted trace capture.
  - `coordinator.py` & `main_gateway.py` — `MainAgentGateway` (handling read-only `answered` inquiries vs. `accepted/queued/requires_approval/rejected` research plans) + `ResearchCoordinator` orchestrating `Paper`, `Data`, `Analysis`, `Validation`, and `Support` tasks, authentic citation-window validation debate/pod, `EvidenceGate`, and `ReportBuilder`.
- `libra_reproduce/api.py`, `libra_reproduce/main.py`, `libra_reproduce/cli.py` — OpenAPI 3.1 V2 FastAPI router, standalone ASGI entrypoint, and pure-backend CLI runner.

### Frontend Reproduce V2 Modules (`Coding_test/frontend/src/reproduce/`)
- `reproduce/types.ts` & `reproduce/api.ts` — Typed OpenAPI V2 client + resumable SSE consumer (`after=N`, `(run_id, seq)` deduplication, gap-triggered snapshot resync).
- `reproduce/canvas/PersonaSvg.tsx` — Pure React/TypeScript port of `agent.js` (`ROLES`, hair/nose strokes, Main orbit ring, isomorphic facial morphing, 1–2% asynchronous idle breathing & blinking, `prefers-reduced-motion` support, **zero reaction on click/hover**).
- `reproduce/canvas/ResearchTableSvg.tsx` — 64-point equal-sampled seeded hand-drawn pen outline renderer smoothly morphing in-place between 2-member rounded rectangle (`160×74`), 3–4-member circle (`r≈74`), and 5–6-member horizontal ellipse (`132×72`), keeping seats outside table perimeter (`r_agent + 12`) and Main's left Boss zone (`x ∈ [0, 260]`) clear.
- `reproduce/canvas/CollaborationProjection.ts` — Pure deterministic projection (`snapshot + tail events` → persona states & active tables, max 2 prominent tables, monotonic `revision` guard, late-join instant table state without fake approach replay).
- `reproduce/canvas/LivingResearchCanvas.tsx` — Read-only `1000×600` world SVG canvas with pan/zoom + pause motion toggle, zero task/spawn/debate POSTs, silent click forwarding to Chat navigator.
- `reproduce/chat/InChatDocumentViewer.tsx` — Left-pane in-chat preview drawer/dialog for `.pdf`, `.png`, `.py`, `.csv`, `.json`, `.md`, and `.docx` summary/download with strict HTML sanitization and scroll-position preservation.
- `reproduce/workspace/ReproduceWorkspace.tsx` — Left `ReproduceChatSidebar` (reusing Libra Research header/composer/attachments/model selector + collapsible activity stream, Research Gain / Evidence / Resources summary disclosure, and deduplicated Artifact file cards) + resizer + right `LivingResearchCanvas`.

---

## 4. Five Key Risks & Concrete Fallbacks

1. **850 MB Harvard Dataverse File Size (`Aggregated_20210521.txt`)**:
   - *Mitigation*: Use verified HTTP `206 Range` byte-window sampling across chronological offsets (`1954–2021`, ~3.2 MB transfer yielding ~75,000 real MAG records), cache locally with SHA-256 verification, and bundle a verified CC0 subset (`data/samples/wu2019_mag_stratified_sample.csv.gz`) so offline/clean-repo runs succeed deterministically in seconds.
2. **Dataverse Metadata vs. Actual Column Count Mismatch (7 described vs. 6 actual)**:
   - *Mitigation*: Parser validates exact 6-column tab structure (`mag_paper_id, year, field_code, team_size, citation_count, disruption`), rejects malformed boundary lines at HTTP Range edges, and documents the missing `Multi-Institution` column explicitly in `data_manifest.json` and the final report.
3. **Claude SDK Auth Portability Across Machines**:
   - *Mitigation*: On this machine `ClaudeSDKClient` (`0.2.189`) is verified LIVE; for standalone Git reviewers who only have an OpenAI/DeepSeek key (`LLM_API_KEY`) or no key at all, `SDKAdapter` cleanly supports `ClaudeSDKClient` → `OpenAIToolHarness` → explicit `BLOCKED` / `SIMULATION` (never faking `LIVE`).
4. **OpenAlex Citation Completeness for S2 Recomputation**:
   - *Mitigation*: Primary reproduction claim is anchored at **S1** on the authors' official MAG dataset (`N > 50,000`), while **S2** ($D = (n_i - n_j)/(n_i + n_j + n_k)$) is reported strictly as a bounded citation-subgraph verification + window-truncation sensitivity check with explicit missing-edge caveats.
5. **Project 1 (`Data` Mode) & Homepage Regression Risk**:
   - *Mitigation*: Branch workspace rendering in `App.tsx` strictly on `(detail.workspace.workspace_type ?? 'data') === 'reproduce'`; keep `LandingHero.tsx`, `InfiniteCanvas.tsx`, `ResearchOrchestrator`, and all 20 existing Project 1 tests untouched.

---

## 5. Architecture Decision Records (ADRs)
Recorded in `docs/decisions/ADR-V2-01-to-07.md`:
- **ADR-V2-01**: Lightweight SVG + rAF/CSS Compositor `LivingResearchCanvas` (no React Flow graph edges).
- **ADR-V2-02**: Decoupled `persona_id` (6 always-present visual personas) vs. `runtime_agent_id` (on-demand SDK execution instances).
- **ADR-V2-03**: Unified `collaboration.started / members_changed / ended` table lifecycle with 64-point seeded path morphing.
- **ADR-V2-04**: All artifacts, figures, reports, Evidence Gate & Research Gain details consolidated inside Left Chat + In-Chat Viewer.
- **ADR-V2-05**: Append-only SQLite `EventStore` (`UNIQUE(run_id, seq)`) + SSE (`Last-Event-ID`/`after`) + read-only `RunSnapshot`.
- **ADR-V2-06**: `claude-agent-sdk==0.2.189` (`ClaudeSDKClient`) with `disallowed_tools` + `ToolPermissionGate` + dual-pool `ResourceScheduler` + portable OpenAI-compatible live tool fallback.
- **ADR-V2-07**: Self-contained `libra_reproduce` Python package powering both the integrated Libra Research web app and the standalone backend-only Git repository (`/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`).
