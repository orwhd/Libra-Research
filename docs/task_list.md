# Task List — Project 1: Research Canvas (M0–M4)

## Critical Path
`M0 (OpenAlex Seed + SQLite + Sandbox + Chromium Validator)` -> `M1 (Orchestrator + Data/Analysis/Vis/Validation Vertical Loop + Basic UI)` -> `M2 (T3 3-Class Charts + Generalizability S10)` -> `M3 (Canvas Marquee/Compare + Comments Deferred Revision + Data Map + View Source/Download)` -> `M4 (Paper Figure Option A + E2E S01–S12 + Docs)`

---

## M0 — Reproducible Data & Engineering Foundation
- [x] Create directory skeleton, `.gitignore`, `.env.example`, copy Handoff Spec & PDF.
- [x] Write Stage-1 engineering docs (`implementation_plan.md`, `task_list.md`, `architecture_draft.md`, `questions_for_product_owner.md`).
- [x] Initialize `backend/` (`uv` Python 3.13 environment) with FastAPI, Pydantic, OpenAI SDK, Pandas, NetworkX, SciPy, Playwright, Matplotlib, Pytest.
- [x] Fetch and verify real OpenAlex AI works subset (2018–2025, `concepts.id:C154945302`, 400 works) via `data/seed/fetch_openalex_seed.py` -> save `data/seed/openalex_ai_works_2018_2025.json` + `data/seed/README_DATA.md`.
- [x] Implement SQLite schema (`db.py`, `models.py`), `openalex_client.py`, `sandbox_runner.py`, `lineage_manager.py`, `browser_validator.py`, and `artifact_manager.py`.
- [x] Bundle local `d3.v7.min.js` (v7.9.0) into `backend/app/static/vendor/d3.v7.min.js`.

## M1 — Minimal End-to-End Vertical Loop
- [x] Implement `llm_client.py` and `capability_catalog.py`.
- [x] Implement `Orchestrator`, `DataAgent`, `AnalysisAgent`, `VisualizationAgent`, `ValidationAgent` with bounded retries (`<= 2`) and real SSE `RunEvent` streaming.
- [x] Implement REST + SSE API endpoints in `backend/app/api/routes.py` & `main.py`.
- [x] Scaffold React 19 + TypeScript + Vite frontend (`ChatSidebar`, `InfiniteCanvas`, `ArtifactModal`, `PreviewFrame`, `ArtifactDock`).
- [x] Verify single query -> real data -> Python execution -> D3 HTML -> Chromium screenshot -> Canvas card -> 80vw × 80vh modal.

## M2 — Coding Test T3 & Generalizability (Anti-Hardcoding)
- [x] Verify statistical chart generation + interactive metric/view controls (S07).
- [x] Verify algorithmic force-directed network generation + hover/drag/zoom/community coloring (S08).
- [x] Verify advanced hierarchical edge bundling layout built from OpenAlex topic/author taxonomy + tension/domain controls (S09).
- [x] Verify unseen 4th chart query (topic-by-year heatmap, S10) works through the exact same pipeline with zero hardcoded prompt routing.
- [x] Verify fault-injection & bounded retry failure path (S11) in `test_harness_generalizability.py`.

## M3 — Full Research Canvas Product Interactions
- [x] Implement vertical task-group placement, rightward version branching, SVG parent-child connectors, and overlap prevention.
- [x] Implement blank-canvas long-press (`320ms`) marquee selection, right-click `popover` menu (`Compare` `aria-disabled="true"` guard when `!= 2`), and dual-pane independent Compare modal.
- [x] Implement `Comments` pin overlay, normalized coordinate + contextual screenshot capture, `pending_comment_count` badge, undo/delete, and deferred Chat-triggered revision (`S03`–`S04`).
- [x] Implement 6-stage interactive `Data Map` inspector (S06, snapshot reuse on visual edits), `View Source` copy, and `Download` (`.html` + `.zip`).
- [x] Implement workspace recovery across page refresh / server restart (S12).

## M4 — Stabilization, Paper Figure (Option A), & Submission
- [x] Generate paper-style system architecture diagram (`design/project1_system_architecture.pdf` & `.png`).
- [x] Run backend `pytest` unit & integration suites (`5/5` passed).
- [x] Run Playwright E2E suite verifying S01–S12 and saving evidence screenshots to `docs/evidence/`.
- [x] Author `README.md` (with startup commands & `./start.sh`) and honest `docs/limitations.md`.
