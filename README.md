# Libra Research — Multi-Agent LLM Framework for Interactive Data Science

**WHALES Lab Coding / Design Test — Project 1 Deliverable (`Data` Mode + Part II Option A Architecture Figure)**

Libra Research is a **harness-engineered multi-agent LLM data science application** that automates end-to-end scholarly data analysis—from adaptive data acquisition and sandboxed Python preprocessing/statistical computation to generalizable interactive visualization generation, headless Chromium validation, and iterative refinement on an **Infinite Research Canvas**.

> **Project 2 (`Reproduce` Mode) Integration**: This web application also includes the frontend adaptation for **Project 2 (Scientific Paper Reproduction)**. On the homepage, simply click **`Data`** in the title (**`Ask Any Questions of Data`** ↔ **`Upload Papers to Reproduce`**) to switch seamlessly into `Reproduce` mode (`Research Chat` + `Living Research Canvas`).  
> For full Project 2 documentation, pure-backend CLI/OpenAPI architecture, Harvard Dataverse & OpenAlex data provenance, and verified *Nature* 2019 reproduction outputs (PDF/DOCX reports, scripts, figures, and CSVs), see the dedicated standalone Project 2 backend repository:  
> **👉 [https://github.com/orwhd/Libra-Reproduce-Backend](https://github.com/orwhd/Libra-Reproduce-Backend)**

---

## System Architecture Figure (Part II Design Test — Option A)

![Project 1 System Architecture](design/project1_system_architecture.png)

- Vector PDF: [`design/project1_system_architecture.pdf`](design/project1_system_architecture.pdf)
- Detailed design & limitations docs: [`docs/implementation_plan.md`](docs/implementation_plan.md) · [`docs/architecture_draft.md`](docs/architecture_draft.md) · [`docs/limitations.md`](docs/limitations.md)

---

## 1. Project 1 Core Capabilities (`T1` – `T3` & Generalizability)

### Dataset (`OpenAlex` + Custom Uploads)
- **Live Adaptive OpenAlex Mining**: `MinerCoordinator` launches `2–6` parallel `MinerWorker` shards against the open [OpenAlex](https://openalex.org/) scholarly API with query decomposition, relevance screening, and cross-shard deduplication.
- **Deterministic Offline Seed Fallback**: Includes a verified 400-work AI scholarly snapshot ([`data/seed/openalex_ai_works_2018_2025.json`](data/seed/openalex_ai_works_2018_2025.json), documented in [`data/seed/README_DATA.md`](data/seed/README_DATA.md)) so analysis remains reproducible offline.
- **User Uploads**: Supports multi-file CSV, TSV, JSON, JSONL, and URL dataset uploads alongside scholarly queries.

### `T1` — Harness-Engineered Multi-Agent Workflow
- **Orchestration Pipeline**: `Orchestrator` → `MinerCoordinator` / `MinerWorkers` → `DataAgent` + `SemanticProfiler` → `AnalysisAgent` → `VisualizationAgent` → `ValidationAgent` → `InteractionAgent`.
- **Isolated Python Execution Sandbox (`backend/app/services/sandbox_runner.py`)**: Executes agent-generated preprocessing and statistical Python scripts in `python -I` subprocesses with static AST import/builtin allowlisting, stripped environment variables (`0` API keys), 25-second wall-clock limits, and POSIX `RLIMIT_CPU`/`RLIMIT_FSIZE` bounds.
- **Bounded Self-Healing QA (`MAX_RETRIES = 2`)**: `ValidationAgent` renders every generated artifact inside headless Chromium, inspects console/page errors, SVG bounding boxes, rendered marks, `NaN` values, and lineage conservation, and loops diagnostic feedback back to `VisualizationAgent` or `AnalysisAgent` automatically.
- **Immutable Lineage & Streaming**: Persists `DatasetSnapshot`, `AnalysisRun`, and `ArtifactVersion` records in SQLite with real-time SSE `RunEvent` streaming (`POST /api/workspaces/.../chat`).

### `T2` — Interactive Web Interface & Infinite Research Canvas
- **Natural-Language Chat & Follow-Up Iteration**: Submit analytical questions, attach uploaded files or existing canvas versions, and issue follow-up refinements that reuse verified snapshots when only visual edits are needed.
- **Infinite Research Canvas**: Displays version evolution horizontally (`v1 → v2 → v3`) per task group with directed lineage edges, smooth pan/zoom + horizontal scrub-zoom, marquee selection, right-click actions (`Attach to Chat`, `Compare`, `Delete`, `Fit to View`), and **Dual-Pane Side-by-Side Compare**.
- **5-Column Semantic Data Flow Map (`Data Map v2`)**: Interactive Sankey provenance inspector (`Data Sources → Semantic Categories → Processing Outcomes → Processed Dataset → Analysis`) verifying record-level conservation and unit transformations.
- **Scroll-Locked Pinpoint Visual Comments**: Click anywhere on a chart or data map to leave spatially anchored annotations with scroll-synchronized pins (`RC_SCROLL_STATE`) and annotated Chromium screenshots, batched cleanly for deferred revision via Chat (zero unwanted auto-rebuilds).

### `T3` — Generalizable Visualization Capabilities (Zero Hardcoded Templates)
Instead of hardcoding chart templates or visualization-specific workflows, agents inspect declarative runtime library capabilities (`D3.js v7`, `ECharts 5`, `Chart.js 4`, `Pandas`, `NumPy`, `SciPy`, `NetworkX`) and synthesize bespoke Python computation + interactive HTML/JS visualization code for any analytical request, including:
1. **Standard Statistical Charts**: Interactive bar, line, area, histogram, scatter, and distribution charts with metric toggles, hover tooltips, and zoom/brush controls.
2. **Network Visualizations**: Algorithmic force-directed collaboration and citation node-link graphs with community detection, ego-neighborhood highlighting, and edge-weight sliders.
3. **Advanced Network Layouts**: Hierarchical edge bundling over taxonomic hierarchies with live bundle-tension controls.
4. **Generalizability Beyond the 3 Examples**: Supports unseen visualization paradigms (e.g., TF-IDF/NMF topic-by-year heatmaps, Sankey flows, multi-panel dashboards) through the same generalizable capability-discovery and sandboxed code-generation loop.

---

## 2. Quick Start & Setup Instructions

### Prerequisites
- **Python 3.11+** with [`uv`](https://docs.astral.sh/uv/)
- **Node.js 20+** with [`pnpm`](https://pnpm.io/)

### 1. Install Dependencies

```bash
git clone https://github.com/orwhd/Libra-Research.git
cd Libra-Research

cd backend && uv sync
cd ../frontend && pnpm install
cd ..
```

### 2. Configure LLM API Credentials (Zero Hardcoded Keys)
- **In-App Modal (Recommended)**: Launch the app and click **`Libra Research`** / **`click me to complete API config!`** on the homepage to enter an OpenAI-compatible API key, base URL, model, and thinking effort (saved locally in gitignored `data/runtime/user_api_config.json`).
- **Environment File**: Alternatively copy `cp .env.example .env` and set `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.

### 3. Run the Web Application

```bash
./start.sh
# Backend API:  http://127.0.0.1:8000
# Frontend App: http://127.0.0.1:5173
```

---

## 3. Automated Verification Suite

```bash
# 1. Backend tests (Project 1 harness, sandbox, lineage, Data Map v2, persistence + Reproduce V2)
cd backend && uv run pytest -k "not s10_s11" -v

# 2. Validate V2 OpenAPI & JSON Schema contracts
cd backend && uv run python ../scripts/validate_contracts.py

# 3. Frontend TypeScript typecheck + production Vite bundle
cd frontend && pnpm build

# 4. Playwright browser E2E suite
cd frontend && pnpm exec playwright test e2e/reproduce_v2.spec.ts
```

---

## 4. Implementation Accounting (`code_test.pdf` Required Documentation)

### What Works (Implemented & Verified)
- End-to-end **Project 1 multi-agent data science pipeline** (`Orchestrator` → parallel OpenAlex `Miner` → sandboxed `Data` & `Analysis` → generalizable `Visualization` → headless Chromium `Validation` with `<= 2` retries).
- All three `T3` visualization families (**standard statistical charts**, **force-directed networks**, **hierarchical edge bundling**) plus unseen paradigms (heatmaps, Sankey diagrams) generated without hardcoded chart workflows.
- Full **Infinite Research Canvas** web UI: version branching, Dual-Pane Compare, 5-column Semantic Data Flow Map, scroll-locked pinpoint comments, View Source, and HTML/ZIP export.
- Integrated **Project 2 (`Reproduce` Mode)** frontend adaptation accessible by clicking **`Data`** on the homepage title (`Ask Any Questions of Data` ↔ `Upload Papers to Reproduce`), backed by the harness detailed in [`orwhd/Libra-Reproduce-Backend`](https://github.com/orwhd/Libra-Reproduce-Backend).

### What Is Incomplete / Scope Boundaries
- **Single-User Local Sandboxing**: `sandbox_runner.py` uses `python -I` subprocesses with AST checks, stripped env vars, and POSIX resource limits suited for local single-user research; multi-tenant cloud deployment would require container/microVM isolation (e.g., Firecracker/gVisor).
- **Internal Chart Pan/Zoom Matrix Capture**: Comment screenshots preserve DOM `<input>`/`<select>` filter states, viewport scroll positions, and hovered tooltips, but arbitrary internal D3/WebGL transform matrices are not serialized unless exposed via DOM controls.
- **Local Single-User Persistence**: Workspaces and artifacts are stored in local SQLite + `data/runtime/` without multi-user auth or cloud object storage.

### Main Technical Decisions
1. **Declarative Capability Catalog Instead of Hardcoded Chart Workflows**: Agents discover available Python analytics and frontend visualization libraries declaratively at runtime so new visualization types work without modifying harness orchestration code.
2. **Immutable Provenance & Deferred Visual Comments**: Every data transformation, analysis run, and visualization version is stored immutably (`DatasetSnapshot` → `AnalysisRun` → `ArtifactVersion`), and pinpoint comments are saved instantaneously without triggering costly unsolicited LLM rebuilds until the user explicitly requests a revision in Chat.
3. **Separated Pure-Backend Repository for Project 2**: While this repository integrates the interactive frontend for Project 2 (`Data` ↔ `Reproduce` switch), the standalone pure-backend paper reproduction engine and pre-verified *Nature* 2019 deliverables are cleanly packaged at [https://github.com/orwhd/Libra-Reproduce-Backend](https://github.com/orwhd/Libra-Reproduce-Backend).
