# Implementation Status & Honest Limitations (`docs/limitations.md`)

**Verification Date:** 2026-11-24  
**Verified Against:** `Project1_Research_Canvas_Handoff_v1.0.md` (§1–§11) and `Coding_Design_Test.pdf`

---

## 1. Works (Verified End-to-End with Real Data, LLM, Sandbox & Chromium)

- **Multi-Agent Harness (`T1`)**: `Orchestrator` -> `DataAgent` -> `AnalysisAgent` (`python -I` sandbox) -> `VisualizationAgent` (D3 v7 dynamic HTML/JS) -> `ValidationAgent` (headless Chromium QA) with bounded retries (`MAX_RETRIES = 2`), real SSE `RunEvent` streaming, and immutable SQLite + filesystem persistence.
- **OpenAlex Dataset Provenance**: Seeded with 400 real OpenAlex AI works (2018–2025, `concepts.id:C154945302`) via [data/seed/fetch_openalex_seed.py](../data/seed/fetch_openalex_seed.py). Live filter fetching is implemented in [openalex_client.py](../backend/app/services/openalex_client.py), with automatic fallback to the verified local snapshot when OpenAlex shared IP budget (HTTP 429) or network constraints trigger — transparently disclosed in `DatasetSnapshot` and `Data Map`.
- **T3 Visualization Capabilities (`S07`, `S08`, `S09`) & Generalizability (`S10`)**:
  - Statistical trend chart with interactive metrics/views/hover tooltips ([evidence 02](evidence/02_modal_statistical.png)).
  - Algorithmic force-directed author collaboration network with community coloring, weight filtering, and search ([evidence 03](evidence/03_modal_network.png)).
  - Hierarchical edge bundling network with multi-level taxonomy, tension slider, and domain filter ([evidence 04](evidence/04_modal_hierarchical_bundling.png)).
  - Unseen 4th visualization query (*Topic-by-Year Heatmap*) dynamically planned, computed in Python, rendered in D3, and validated in Chromium without hardcoded prompt templates ([evidence 09](evidence/09_s10_unseen_heatmap.png)).
- **Fault Injection & Bounded Retry (`S11`)**: Verified via `test_live_harness_unseen_heatmap_and_fault_retry_s10_s11` in `backend/tests/test_harness_generalizability.py`, where an injected JS error on attempt 1 was caught by Chromium `ValidationAgent` and repaired by `VisualizationAgent` on attempt 2.
- **Full Research Canvas Interactions (`S01`–`S06`, `S12`)**:
  - Conversation-first landing -> Split Chat + Infinite Canvas ([evidence 01](evidence/01_canvas_three_artifacts.png)).
  - Normal preview interaction never triggers Agent runs (`S02`).
  - Anchored `Comments` capture `(x_norm, y_norm)`, contextual screenshot, and view state without creating a new version (`S03`, [evidence 05](evidence/05_pinned_comment_saved.png)).
  - Deferred Chat revision spawns `v2` strictly to the right of `v1` with directed edge while preserving `v1` and historical comments (`S04`, [evidence 06](evidence/06_rightward_version_v2.png)).
  - Blank-canvas `320ms` long-press marquee selection + right-click `popover` `Compare` opens dual side-by-side independent `ArtifactWindow`s with full Docks (`S05`), 6-stage `Data Map` showing snapshot reuse (`S06`, [evidence 07](evidence/07_compare_dual_datamap.png)), and `View Source` ([evidence 08](evidence/08_view_source.png)).
  - Page reload recovery from SQLite (`S12`, [evidence 10](evidence/10_reloaded_persistence.png)).
- **Design Test Option A**: Paper-style system architecture figure generated in [design/project1_system_architecture.pdf](../design/project1_system_architecture.pdf) and [PNG](../design/project1_system_architecture.png).

### Observed Verification Runs
- Backend `uv run pytest -v`: **5 passed in 95.81s** (`test_sandbox_and_lineage.py`, `test_persistence_and_comments.py`, `test_harness_generalizability.py`).
- Frontend `pnpm test:e2e`: **1/1 full E2E scenario suite passed (4.4m)** against live FastAPI + Vite + Chrome.

---

## 2. Partial / Scope Trade-offs

1. **OpenAlex Sample Subset vs. Full API Pagination**: By default, `DataAgent` operates on a 400-work subset (`2018–2025`, `concepts.id:C154945302`) cached locally unless `OPENALEX_API_KEY` is configured for larger live pagination, avoiding shared-IP HTTP 429 budget exhaustion during demos.
2. **Code Sandbox Isolation Level**: `sandbox_runner.py` uses Python `-I` isolated subprocesses with AST import/builtin checks, stripped environment variables, 25s timeout, and POSIX `RLIMIT_CPU`/`RLIMIT_FSIZE`. It is suitable for trusted single-host research prototyping, not multi-tenant hostile code execution (which would require OS containers / gVisor / Firecracker).
3. **Preview View-State Restoration in Comments**: When capturing comment screenshots (`capture_comment_screenshot`), the backend replays `filter_state` against standard `<input>`/`<select>` elements via `RC_APPLY_VIEW_STATE`. Custom D3 zoom pan/transform state inside generated charts is not automatically serialized unless exposed as DOM controls.
4. **Rightward Layout Density in Deep Revision Trees**: If a single parent spawns multiple direct branches, sibling branches step down vertically (`CARD_HEIGHT + CARD_GAP_Y`) to avoid overlapping, which could overlap an unrelated lower row if many (>2) sibling branches are created from the top row simultaneously.

---

## 3. Not Implemented (Out of Scope for v1.0)

- **Multi-user Authentication & Cloud Object Storage**: Single-user local SQLite + filesystem artifact store (`data/runtime/`).
- **Automated Voice-Narrated Demo Video Capture**: Per §11.2, a `<= 5 minute` narrated walkthrough video should be recorded by the candidate demonstrating the live UI running locally via `./start.sh`.
- **Project 2 (Paper Reproduction)**: Explicitly out of scope per Handoff Spec §1 ("用户当前只选择实现 Project 1；Project 2 不在本交付范围内").
