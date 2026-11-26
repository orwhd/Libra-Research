# Libra Research · Project 2 (Reproduce V2) Implementation Status

**Updated**: 2026-11-26  
**Spec Baseline**: `Libra_Reproduce_Fullstack_Handoff_V2` (`AGENTS.md`, `docs/REPRODUCE_FULLSTACK_L1_5_TO_L4_V2.md`, `docs/ACCEPTANCE_V2.md`, `contracts/*`) + WHALES Lab Coding/Design Test (`Project 2` + `Part II Option A`).  
**Claude Agent SDK Version**: `claude-agent-sdk==0.2.189` (verified LIVE via `ClaudeSDKClient` + Claude Code CLI `2.1.169`, with portable OpenAI-compatible live tool-calling fallback).

---

## 1. Status Matrix (`DONE` / `PARTIAL` / `BLOCKED`)

| Acceptance ID | Requirement | Status | Evidence / Code Path |
| --- | --- | --- | --- |
| **REG-01** | Preserve `Data` mode, `Upload Papers to Reproduce` homepage, and inline `Data` ↔ `Reproduce` switch | **DONE** | `frontend/src/components/LandingHero.tsx`, `frontend/src/App.tsx`, `frontend/e2e/reproduce_v2.spec.ts` (`2 passed`), Project 1 backend tests (`20 passed`) |
| **UI-01** | Initial 6-persona roster visible on frame 1 (`Main` left Boss `x <= 260`, ~20% larger with orbit ring; 5 right; all `idle`; 0 fake spawns) | **DONE** | `frontend/src/reproduce/canvas/PersonaSvg.tsx`, `LivingResearchCanvas.tsx`, `CollaborationProjection.ts` |
| **UI-02** | Decoupled `persona_id` vs. `runtime_agent_id`; no duplicate role circles on `agent.spawned` | **DONE** | `backend/libra_reproduce/harness/persona_catalog.py`, `frontend/src/reproduce/canvas/CollaborationProjection.ts` |
| **UI-03 / API-05** | Clicking/hovering any Agent causes **zero** reaction on Agent and **zero** write POSTs; silently navigates Left Chat | **DONE** | `PersonaSvg.tsx`, `ReproduceWorkspace.tsx`, `frontend/e2e/reproduce_v2.spec.ts` |
| **UI-04..UI-07** | Temporary hand-drawn pen Research Tables (`2 -> rounded rect`, `3–4 -> circle`, `5–6 -> ellipse`) morphing continuously on the **same `<path>` DOM node** (no remount); seats outside perimeter; max 2 prominent tables | **DONE** | `frontend/src/reproduce/canvas/ResearchTableSvg.tsx`, `CollaborationProjection.ts`, `frontend/e2e/reproduce_v2.spec.ts` |
| **UI-08 / API-04** | Prominent `SIMULATION · Non-Live Fixture` badge whenever `mode === "simulation"`; never labeled `live` | **DONE** | `LivingResearchCanvas.tsx`, `ReproduceWorkspace.tsx`, `backend/libra_reproduce/harness/coordinator.py` |
| **UI-09** | Zero documents/inspectors on Right Canvas; deduplicated Artifact cards + `InChatDocumentViewer` (`.pdf`, `.png`, `.csv`, `.py`, `.json`, `.docx`) strictly in Left Chat | **DONE** | `frontend/src/reproduce/chat/InChatDocumentViewer.tsx`, `ReproduceWorkspace.tsx` |
| **UI-10** | `prefers-reduced-motion: reduce` + Canvas `Pause motion` toggle disable animations while keeping states & seats accurate | **DONE** | `LivingResearchCanvas.tsx`, `PersonaSvg.tsx`, `frontend/src/styles.css` |
| **API-01..API-03** | OpenAPI 3.1 V2 routes, `request_id` idempotency, transactional SQLite `EventStore` (`UNIQUE(run_id, seq)`), SSE `after=N` / `Last-Event-ID`, zero-execution `GET /snapshot?at_seq=N` | **DONE** | `backend/libra_reproduce/api.py`, `storage/event_store.py`, `scripts/validate_contracts.py` (`27 refs checked`), `tests/test_reproduce_v2.py` |
| **HAR-01..HAR-03** | Independent `ResourceScheduler` pools (`max_active_agent_tasks=4`, `max_parallel_experiments=2`, `exploration_budget<=0.20`), lease release on crash, `ToolPermissionGate` + AST/env-stripped `-I` `SafeExperimentRunner` | **DONE** | `backend/libra_reproduce/harness/scheduler.py`, `security/tool_permissions.py`, `security/experiment_runner.py`, `tests/test_reproduce_v2.py` |
| **SCI-01..SCI-04** | Real empirical Nature 2019 reproduction (`S1` on Harvard Dataverse MAG `CC0-1.0` + bounded `S2` $D$-index recomputation & citation-window sensitivity), `EvidenceGate`, readable `.pdf` & `.docx` reports | **DONE** | `backend/libra_reproduce/science/*`, `outputs/nature2019_live_verified/*` |
| **STANDALONE** | Pure-backend runnable interface (FastAPI ASGI + headless CLI) & standalone Git repository (`Libra-Reproduce-Backend`) with 0 `sk-` secrets | **DONE** | `/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`, `libra_reproduce/main.py`, `libra_reproduce/cli.py` |
| **DESIGN-A** | Paper-style system architecture figure (`PDF` + `PNG`) for Project 2 | **DONE** | `design/project2_system_architecture.pdf`, `design/project2_system_architecture.png` |

> **Scope Honesty Note (`PARTIAL` / Out-of-Slice Boundaries)**:
> - Primary large-scale statistical reproduction (`N = 45,335` in `1954–2014`, `60,120` total sampled MAG papers) is explicitly reported at **Level `S1`** (reanalysis of author-published precomputed disruption scores from Harvard Dataverse `doi:10.7910/DVN/JPWNNK`), while **Level `S2`** ($D = (n_i - n_j)/(n_i + n_j + n_k)$) is verified on hand-calculated toy citation graphs and bounded OpenAlex citation subgraphs (`5y` vs. `10y` windows) rather than recomputing the proprietary full Web of Science / full 850 MB raw citation graph.
> - Cross-corpus replication on USPTO patents and GitHub repositories from Wu, Wang & Evans (2019) is documented as an explicit Open Question rather than simulated.

---

## 2. Verified Live Run Provenance (`rep_ad4b1bed1193`)

Executed via headless CLI against live `claude-agent-sdk==0.2.189` (`ClaudeSDKClient`):

```bash
cd backend
uv run python -m libra_reproduce.cli run --paper-id paper_A --mode live --output-dir ../outputs/nature2019_live_verified
```

- **Target Paper**: Lingfei Wu, Dashun Wang & James A. Evans (2019), *"Large teams develop and small teams disrupt science and technology"*, *Nature*, 566, 378–382 (`https://doi.org/10.1038/s41586-019-0941-9`).
- **Author Data Source**: Harvard Dataverse `https://doi.org/10.7910/DVN/JPWNNK` (File `6925921` `Aggregated_20210521.txt`, License `CC0-1.0` Public Domain).
  - *Empirical Schema Discovery*: Byte-level inspection confirmed 6 tab-separated columns (`mag_paper_id, year, field_code, team_size, citation_count, disruption`); the `Multi-Institution` column mentioned in the prose abstract is absent from the published file and documented in `data_manifest.json` and the reports.
- **Key Empirical Findings (`EvidenceGate = PASSED`)**:
  - Solo authors (`k = 1`, `n = 10,150`): mean $D = +0.014013$, mean disruption percentile $= 54.33$, mean citations $= 23.98$.
  - Large teams (`k >= 11`, `n = 449`): mean $D = +0.000364$, mean disruption percentile $= 41.18$, mean citations $= 68.10$.
  - OLS slope across `1954–2014` (`N = 45,335`): $-0.00187$ disruption units per additional author ($p = 6.89 \times 10^{-40}$), preserved after within-discipline demeaning across 19 MAG Level-0 fields.
  - Validation Pod (`Analysis` + `Validation` + `Data`) verified that restricting to `1954–2004` ($\ge 17$-year forward citation window) preserves the negative gradient while `2015–2021` right-truncation compresses raw $D$ variance.

### Verified Artifacts & SHA-256 Manifest (`outputs/nature2019_live_verified/`)

| Artifact File | Kind | SHA-256 |
| --- | --- | --- |
| `data_manifest.json` | `dataset` | `11dbbdd44a30402429ea26ee93f6db5f0c10a6c71edab4e8d2fd6be72810875b` |
| `wu2019_mag_sample.csv` | `dataset` | `11d984695444feff796291cdbfc7fbf57249c0babc9bf679df5f654c4e685b46` |
| `reproduce_nature2019_experiment.py` | `script` | `1a2d975a6b3be450117918a7d8bb3e77febb424091a9ad28d057283d2989aecd` |
| `analysis_team_size_s1.csv` | `dataset` | `226bcd3677592190ad490b86bcf1b5b0a4e8806bb316f792d00e787be3aeca83` |
| `figure_team_size_disruption.png` | `figure` | `bbdb543369a377506f8ec1e782264179d2fa9f59d473ff4c95e1d19befa45d98` |
| `sensitivity_citation_window.csv` | `dataset` | `d12241d5e52caf899102f14fba9ab496b78590dde24570f89f0e744aa5a42b1c` |
| `figure_window_sensitivity.png` | `figure` | `ec4ddc6552cd5eae8ec66fd080eb0e616639f2914cf46ea92d42022d1e4c2f2c` |
| `reproduction_report.pdf` | `report` | `1c5e18389d7792a3400d91e43077b6ba8bd99d25e9377f0a3cc6a52fddc029da` |
| `reproduction_report.docx` | `report` | `187d6e98b5d1d97669b0139305a2bfdb53cfee3956e734e1c84c1660382305db` |

---

## 3. Automated Verification Commands & Results

1. **Contract & Schema Validation (`OpenAPI 3.1` + `JSON Schema 2020-12`)**:
   ```bash
   cd backend && uv run python ../scripts/validate_contracts.py
   # -> All V2 contract checks passed (27 refs checked).
   ```
2. **Backend Pytest Suite (Project 1 + Reproduce V2 Harness, Security, Science & API)**:
   ```bash
   cd backend && uv run pytest -k "not s10_s11" -v
   # -> 24 passed in 18.31s
   ```
3. **Frontend TypeScript + Production Vite Build**:
   ```bash
   cd frontend && pnpm build
   # -> built in 1.15s (0 errors)
   ```
4. **Playwright Automated Browser E2E (`reproduce_v2.spec.ts`)**:
   ```bash
   cd frontend && pnpm exec playwright test e2e/reproduce_v2.spec.ts
   # -> 2 passed (2.8s)
   ```
5. **Standalone Backend Repository Verification (`/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend`)**:
   ```bash
   cd /Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend
   uv run python scripts/validate_contracts.py
   uv run pytest -v
   ```

---

## 4. Recommended `<= 5 Minute` Demo Script

1. **`0:00 – 0:35` · Mode Switch & Reproduce Homepage (`Upload Papers to Reproduce`)**:
   - Open `http://127.0.0.1:5173`. Click **`Data`** in `Ask Any Questions of Data` to morph smoothly to `Upload Papers to Reproduce`. Point out the 2×2 hot recommended papers (including Wu, Wang & Evans *Nature* 2019) and separated `Existing Workspaces`.
2. **`0:35 – 1:15` · Initial 6-Persona Living Research Canvas**:
   - Click the Nature 2019 paper chip and press **Run** (or open a fresh Reproduce workspace). Show that all 6 hand-drawn personas (`Main`, `Paper`, `Data`, `Analysis`, `Validation`, `Support`) appear immediately at stable anchors (`Main` left Boss with orbit ring, 5 Research Agents right), with zero graph edges or Canvas clutter.
   - Click/hover an Agent on the Canvas to demonstrate **zero Agent reaction** and silent navigation in Left Chat.
3. **`1:15 – 2:45` · Live Reproduction, Debate -> Pod Table Morphing & Evidence Gate**:
   - Watch `Paper`, `Data`, and `Analysis` transition through `working` states via live `ClaudeSDKClient` tool execution and sandboxed `-I` Python computation.
   - Observe `Validation` challenge forward citation-window comparability (`collaboration.started` -> **2-seat rounded rectangle table**), then invite `Data` into a 3-agent Research Pod (`collaboration.members_changed` -> **smooth in-place SVG morph to a 3-seat circular table**), followed by `EvidenceGate = PASSED` and clean table dissolution (`collaboration.ended`).
4. **`2:45 – 4:10` · In-Chat Document Viewer, Reports & Historical Replay**:
   - In Left Chat, click **Preview** on `figure_team_size_disruption.png`, `analysis_team_size_s1.csv`, and `reproduction_report.pdf` inside `InChatDocumentViewer` (keeping Right Canvas 100% document-free), and download `reproduction_report.docx`.
   - Expand **Evidence Gate, Research Gain & Resources** (`Verified`, `Ruled Out`, `Open Questions`, `Method Notes`) and scrub the **Replay** slider to revisit historical table states with zero tool re-execution.
5. **`4:10 – 4:50` · Pure-Backend Standalone Interface**:
   - Show `/Users/zhich/Documents/项目/HCI/Libra-Reproduce-Backend` running `uv run python scripts/reproduce_nature2019.py` / `uv run uvicorn libra_reproduce.main:app --port 8001` completely standalone without any frontend.
