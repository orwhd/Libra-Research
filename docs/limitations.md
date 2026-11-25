# Implementation Status & Honest Limitations (`docs/limitations.md`)

**Verification Date:** 2026-11-26  
**Verified Against:** `Coding_Design_Test.pdf` (Part I Project 1 & Part II Design Test Option A)

---

## 1. What Works (Verified End-to-End)

- **Multi-Agent Harness (`T1`)**: `Orchestrator` -> `MinerCoordinator` (`2..6` adaptive parallel `MinerWorker`s) -> `DataAgent + SemanticProfiler` (`python -I` sandbox) -> `AnalysisAgent` (`python -I` sandbox) -> `VisualizationAgent` (runtime-discovered `ECharts 5` / `D3 v7` / `Chart.js 4` / native SVG & Canvas) -> `ValidationAgent` (reusable headless Chromium QA + lineage conservation check) -> `InteractionAgent` with bounded self-healing retries (`MAX_RETRIES = 2`), real SSE `RunEvent` streaming, and SQLite persistence.
- **OpenAlex Live Parallel Mining + Verified Seed Provenance**: Parallel live shard retrieval in [miner_agent.py](../backend/app/agents/miner_agent.py) and [openalex_client.py](../backend/app/services/openalex_client.py) with relevance screening and cross-shard deduplication, plus automatic offline fallback to the 400-work verified snapshot [data/seed/openalex_ai_works_2018_2025.json](../data/seed/openalex_ai_works_2018_2025.json). Also supports multi-file CSV/TSV/JSON/JSONL/URL uploads.
- **T3 Visualization Capabilities & Generalizability**:
  - Interactive statistical charts (bar/line/area/scatter/distributions with metric toggles and brush/zoom).
  - Algorithmic force-directed collaboration/citation networks with community detection, ego-neighborhood highlighting, and tie-weight filtering.
  - Hierarchical edge bundling views with multi-level taxonomic trees and live bundle-tension controls.
  - Unseen visualization paradigms (e.g., TF-IDF/NMF topic-by-year heatmaps, Sankey flows) generated dynamically with **zero hardcoded prompt templates or chart-type switches**.
- **Semantic Data Flow Map (`Data Map v2`)**: 5-column interactive Sankey provenance diagram (`Data Sources -> Semantic Categories -> Processing Outcomes -> Processed Dataset -> Analysis`) with deterministic Python record-level conservation checks, unit-conversion explanations, pinpoint commenting, and Legacy Lineage toggle.
- **Scroll-Locked Pinpoint Comments & Iterative Canvas**:
  - Instant (`<15ms`) optimistic comment save, scroll-synchronized pin positioning (`RC_SCROLL_STATE`), overlay-piercing DOM/Canvas target inspection (`elementsFromPoint`), and background annotated Chromium screenshot capture.
  - Rightward version branching (`v1 -> v2 -> v3`), horizontal scrub-zoom, right-click `Attach to Chat` / `Compare` / `Delete` / `Fit to View`, and Dual-Pane Compare.
- **In-App API Config & Model Discovery**: Keyring-free local configuration modal supporting `/models` list fetching, connection testing, model switching, and `Low / Medium / High` thinking effort selection (stored strictly in gitignored `data/runtime/user_api_config.json`).
- **Part II Design Test (Option A)**: Paper-style system architecture diagram in [design/project1_system_architecture.pdf](../design/project1_system_architecture.pdf) and [PNG](../design/project1_system_architecture.png).

### Automated Test Status
- Backend `uv run pytest -v`: **19 / 19 passed** (`test_miner_and_fast_pipeline.py`, `test_semantic_data_map_v2.py`, `test_persistence_and_comments.py`, `test_sandbox_and_lineage.py`, `test_harness_generalizability.py`).
- Frontend `pnpm build` (`tsc -b && vite build`): **0 errors**.

---

## 2. Partial / Scope Trade-offs & Limitations

1. **Subprocess Sandbox Isolation Level**: `sandbox_runner.py` uses `python -I` isolated subprocesses with AST import/builtin allowlisting, stripped environment variables (zero API keys), 25s timeout, and POSIX `RLIMIT_CPU`/`RLIMIT_FSIZE`. This is tailored for single-user local research; multi-tenant untrusted cloud execution would require container/microVM sandboxing (e.g., gVisor / Firecracker).
2. **Custom Internal Canvas Zoom State in Comment Screenshots**: Comment screenshots restore standard `<input>`/`<select>` filter controls and `(scroll_x, scroll_y)` viewport offsets, and hover the target point to reveal tooltips, but arbitrary internal WebGL/D3 pan matrices inside a chart are not serialized unless exposed through DOM controls.
3. **Single-User Local Storage**: Workspaces and artifacts persist locally in SQLite and `data/runtime/` without multi-user authentication or cloud object storage.
4. **Out-of-Band Deliverables**: The `< 5 minute` narrated walkthrough video is recorded externally against `./start.sh`; Project 2 is not included in this repository.
