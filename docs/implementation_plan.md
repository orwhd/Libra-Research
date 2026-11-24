# Implementation Plan — Project 1: Research Canvas (L2–L4 Technical Specification)

**Document Version:** v1.0  
**Reference Specification:** `Project1_Research_Canvas_Handoff_v1.0.md` & `docs/Coding_Design_Test.pdf`

---

## 1. Boundary Classification

1. **Immutable L1.5 Product Constraints (Locked per Handoff §2–§4):**
   - Conversation-first landing -> split view (Left Chat + Right Infinite Research Canvas; no permanent Preview panel, no Artifact Shelf).
   - One Workspace = persistent Chat + Infinite Canvas; tasks create distinct Task Groups.
   - Vertical ordering for multiple outputs of the same initial task; rightward branching (`revision_column + 1`) for regenerated artifact versions; zero card overlap; directed version edges.
   - Thumbnails generated exclusively from real headless browser rendering by the Validation Agent (never static placeholders or PNG-for-preview substitutes).
   - Centered `80vw × 80vh` Artifact Modal with unified bottom Dock: `Preview | Data Map | Comments | [sep] | View Source | Download | Minimize`.
   - Normal interactive exploration inside Preview (hover, drag, zoom, filters) NEVER triggers Agent runs.
   - Visual comments save anchor `(x_norm, y_norm)`, contextual screenshot, preview view state, optional hint, and text *without* automatically rebuilding; pending count displays in card top-right badge; later Chat triggers Orchestrator revision.
   - Strict immutability of old `ArtifactVersion`, `DatasetSnapshot`, `AnalysisRun`, and `VisualComment`.
   - Blank-canvas left-button long-press + drag marquee selection -> right-click menu `Compare` -> dual side-by-side independent `ArtifactWindow`s with full Docks (`Compare` disabled with explanation if `selected.length !== 2`).
   - 6-node-type Data Map (`Source -> Collection -> Transformation -> Dataset Snapshot -> Analysis Run -> Visualization Artifact`) with no internal diff page inside Data Map.

2. **Original Coding Test Requirements (Mandatory per PDF):**
   - **T1**: Multi-agent workflow + Harness (orchestration, sandboxed execution, intermediate artifacts, bounded retry/error recovery).
   - **T2**: Web UI with natural-language Chat, interactive visualizations, and iterative follow-up modification.
   - **T3**: All 3 visualization classes driven by real OpenAlex data: (1) interactive statistical chart, (2) algorithmic node-link network (e.g., force-directed collaboration/citation network), (3) advanced network layout (e.g., hierarchical edge bundling with real taxonomic hierarchy).
   - **Generalizability**: Zero hardcoded prompt-to-template routing; demonstrated via a 4th unseen chart type (e.g., topic-by-year heatmap or treemap) generated through the exact same capability catalog + Python + D3 + Validation loop.

3. **Negotiable L2–L4 Engineering Choices (Implemented Below):**
   - FastAPI + SQLite + SSE backend with Python 3.13 (`uv`) + isolated subprocess (`python -I`) code execution sandbox + Playwright Chromium validation.
   - React 19 + TypeScript + Vite frontend with native `<dialog>` (`showModal()`), `popover` context menu, `role="toolbar"` Dock, custom smooth infinite Canvas pan/zoom/marquee, and `<iframe sandbox="allow-scripts">` preview isolation.

---

## 2. L2 Module Boundaries

- **Frontend (`frontend/src/`)**:
  - `App.tsx` / `LandingHero.tsx` / `ChatSidebar.tsx`: Workspace session management, Chat stream, expandable Agent Task Cards (`Planning -> Data -> Analysis -> Visualization -> Validation -> Ready/Failed`) fed by SSE.
  - `InfiniteCanvas.tsx` / `CanvasCard.tsx` / `CanvasContextMenu.tsx`: Pan/zoom transform engine, blank-space long-press marquee disambiguation, parent->child SVG edges, right-click `popover` menu.
  - `ArtifactModal.tsx` / `ArtifactWindow.tsx` / `ArtifactDock.tsx`: Native `<dialog>` container supporting Single (`80vw × 80vh`) and Dual Compare (`94vw × 84vh`) modes with unified bottom `role="toolbar"`.
  - `PreviewFrame.tsx` / `CommentsOverlay.tsx` / `DataMapView.tsx` / `SourceCodeView.tsx`: Sandboxed iframe host + normalized coordinate capture + 6-stage interactive Lineage DAG inspector + read-only exact HTML source inspector.
- **Backend (`backend/app/`)**:
  - `api/routes.py`: REST endpoints (`/workspaces`, `/tasks`, `/runs/:id/events` SSE, `/canvas`, `/versions/:id/{html,thumbnail,lineage,source,download,comments}`, `/comments/:id`).
  - `agents/orchestrator.py`: Intent parsing, ambiguity guard, task decomposition, bounded retry orchestration, SSE event emission.
  - `agents/data_agent.py` & `services/openalex_client.py`: OpenAlex live filter query + verified snapshot fallback + LLM-written Python filtering/cleaning inside sandbox -> immutable `DatasetSnapshot`.
  - `agents/analysis_agent.py`: LLM-written Python statistical / NetworkX / hierarchical clustering computation inside sandbox -> immutable `AnalysisRun`.
  - `agents/visualization_agent.py` & `agents/capability_catalog.py`: Declarative D3 v7 / algorithm capability metadata -> dynamically generated self-contained HTML/CSS/JS referencing `/vendor/d3.v7.min.js`.
  - `agents/validation_agent.py` & `services/browser_validator.py`: Headless Chromium runtime verification (JS errors, SVG/Canvas bounding box, rendered element count, `NaN` coordinate check, simulated hover/control check, real `preview.png` screenshot).
  - `services/lineage_manager.py` & `services/artifact_manager.py`: Deterministic provenance graph assembly (no LLM hallucination) and non-overlapping `(row, revision_column, x, y)` layout solver.

---

## 3. L3 System Architecture & Required Decision Table (§8)

| Decision Area | Candidates & Trade-offs | Recommended Choice | Risk & Failure Degradation | Relation to Product Constraints |
|---|---|---|---|---|
| **1. Infinite Canvas Framework & Input Disambiguation** | A) React Flow / tldraw (heavy opinionated node-editor chrome, overrides right-click/drag gestures);<br>B) Custom SVG/HTML CSS-transform Canvas with explicit pointer state machine. | **B) Custom Transform Canvas (`InfiniteCanvas.tsx`)** using GPU `translate3d + scale`, `pointerdown` timer (`320ms`, `<6px` movement) for marquee vs immediate drag for pan. | Risk: Touchpad zoom/pan jitter across browsers.<br>Mitigation: Normalize `ctrlKey` pinch-zoom vs scroll, expose `Fit All`, `100%`, and `Esc` HUD controls + deterministic keyboard/click fallback. | Preserves exact blank-canvas long-press marquee -> right-click `Compare` without library drag conflicts, and guarantees vertical task / rightward evolution layout invariants. |
| **2. Generated HTML Isolation & Rendering** | A) Inline DOM injection (XSS/CSS leakage into host);<br>B) Same-origin iframe (shared storage/cookies);<br>C) `<iframe sandbox="allow-scripts">` opaque origin + server-served `/api/versions/:id/html` + Playwright screenshot thumbnails. | **C) `<iframe sandbox="allow-scripts">`** (no `allow-same-origin`) + Origin/Window-pinned `postMessage` view-state bridge + Playwright headless Chromium thumbnails. | Risk: LLM-generated HTML depends on flaky external CDN.<br>Mitigation: Serve local `/vendor/d3.v7.min.js` from backend; inline D3 on standalone HTML Download; `Origin` API + `event.source` check for `postMessage`. | Guarantees Preview truly runs interactive HTML safely isolated from host app, and Canvas thumbnails are real Validation screenshots. |
| **3. Multi-Agent Harness & Code Execution** | A) Heavy external orchestration daemon;<br>B) Custom async Python Harness (`Orchestrator` + specialized Agents + SSE bus + `python -I` sandbox). | **B) Lightweight Async Harness + SQLite + SSE** + AST-checked isolated `python -I` subprocess (env stripped of `LLM_API_KEY`, 25s timeout, `RLIMIT_CPU`/`RLIMIT_FSIZE`). | Risk: LLM generates buggy Python/JS or hits rate limit.<br>Mitigation: Up to 2 automatic retries with exact stderr/Chromium traceback fed back; explicit `failed` state while preserving prior validated versions. | Directly satisfies T1 (orchestration, intermediate artifacts, bounded retry, error trace) without opaque framework overhead. |
| **4. Algorithm Discovery, Generation & Verification** | A) Keyword `if/elif` router to 3 prebuilt HTML templates (FORBIDDEN);<br>B) Pure unconstrained prompt with no API docs (high D3 v6/v7 syntax breakage);<br>C) Capability Catalog (dataset schema + Python/NetworkX + D3 v7 primitives) + Code-Gen + Chromium Validation. | **C) Capability Catalog + Dynamic Code Generation + Browser QA**. | Risk: Subtle D3 layout failures (NaN paths, zero marks, overlapping labels).<br>Mitigation: Validation Agent runs headless Chromium checks (`NaN` attribute scan, bounding box, interaction smoke test) and drives revision loop. | Satisfies T3 (3 chart classes) AND Generalizability (S10 unseen heatmap/treemap) without hardcoded routes. |
| **5. Lineage & Artifact Version Data Model** | A) Mutable single artifact row overwritten on edit;<br>B) Immutable `ArtifactVersion` + `DatasetSnapshot` + `AnalysisRun` + `LineageGraph` stored in SQLite + immutable artifact folders. | **B) Immutable Versioned Store** (`av_xxx`, `ds_xxx`, `ar_xxx`, `comment_xxx`). | Risk: Disk growth across many revisions.<br>Mitigation: Reuse `DatasetSnapshot` (`ds_xxx`) whenever visual-only revision is performed; store compact trimmed OpenAlex JSON. | Enables rightward version lineage, non-destructive old comments/anchors, instant dual-window Compare, and authentic Data Maps. |

---

## 4. L4 Implementation Details

- **Canvas Pointer State Machine**:
  - Click on card (`< 6px` movement) -> open Single Artifact Modal.
  - `pointerdown` on blank canvas (`e.button === 0`): starts a `320ms` long-press timer and shows subtle radial hold ring at pointer.
    - If pointer moves `>= 6px` *before* `320ms` elapses -> cancels timer and enters `panning` mode (`panX`, `panY`).
    - If `320ms` elapses with `< 6px` movement -> enters `marquee` mode (`marqueeRect`) and highlights intersecting cards on drag!
  - `contextmenu` on canvas -> opens native `<div popover="auto">` positioned at pointer with `Compare` (`aria-disabled="true"` unless `selectedIds.length === 2`).
- **Iframe Coordinate Mapping & Contextual Screenshot**:
  - Preview iframe fills `100%` of the modal's Preview stage.
  - In `Comments` pin-placement mode, a transparent overlay captures click `(clientX - rect.left) / rect.width` and `(clientY - rect.top) / rect.height` as `(x_norm, y_norm)` in `[0, 1]`.
  - Host posts `{ type: "RC_REQUEST_VIEW_STATE", x_norm, y_norm }` to `iframe.contentWindow`; iframe replies via `postMessage` (validated via `Origin.from(event)` / `event.source`) with `{ filter_state, element_hint, viewport_width, viewport_height, scroll_x, scroll_y, zoom }`.
  - Backend renders that exact `ArtifactVersion` HTML in headless Chromium at matching viewport, applies `filter_state` if supported, draws a visual anchor reticle at `(x_norm, y_norm)`, and saves `comments/<comment_id>.png`.
- **LLM API Configuration & Token Safety**:
  - Uses OpenAI-compatible Async client against `LLM_BASE_URL` (`https://api.deepseek.com`) with `LLM_MODEL` (`deepseek-flash`).
  - Sets `max_tokens=24576` for code/HTML generation so DeepSeek reasoning tokens never truncate output content; extracts clean Python/HTML blocks deterministically.
