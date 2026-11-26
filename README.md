# Libra Research — Multi-Agent Platform for Interactive Data Science & Scientific Paper Reproduction

**WHALES Lab Coding / Design Test — Integrated Project 1 (`Data`) + Project 2 (`Reproduce` V2) Deliverable**

Libra Research is an end-to-end **harness-engineered multi-agent research platform** combining two complementary modes accessible via a single inline title toggle (`Ask Any Questions of Data` ↔ `Upload Papers to Reproduce`):

1. **`Data` Mode (Project 1)**: Exploratory scholarly data mining, sandboxed statistical/network analysis, multi-library interactive visualization (`D3.js v7`, `ECharts 5`, `Chart.js 4`), headless Chromium QA, **Infinite Research Canvas** version evolution (`v1 -> v2 -> v3`), scroll-locked pinpoint visual comments, and a **5-stage Semantic Data Flow Map**.
2. **`Reproduce` Mode (Project 2 V2)**: Controlled multi-agent scientific paper reproduction powered by `claude-agent-sdk==0.2.189` (`ClaudeSDKClient`, with portable OpenAI-compatible live tool fallback), dual-pool `ResourceScheduler`, AST/env-isolated `SafeExperimentRunner`, independent `EvidenceGate`, readable **PDF & Word (`.docx`) reports**, Left **Research Chat + In-Chat Document Viewer**, and a read-only **Living Research Canvas** featuring 6 hand-drawn Agent personas and morphing temporary Research Tables (`2 -> rectangle`, `3–4 -> circle`, `5–6 -> ellipse`).
   - **Standalone Pure-Backend Interface**: The entire Project 2 engine (`backend/libra_reproduce/`) runs both inside this fullstack web app **and** completely standalone via ASGI (`uvicorn libra_reproduce.main:app`) or headless CLI (`python -m libra_reproduce.cli`), and is also packaged as a dedicated standalone Git repository at `../Libra-Reproduce-Backend`.

---

## Architecture Figures & Design Documentation (Part II — Option A)

- **Project 1 Architecture Figure**: [design/project1_system_architecture.pdf](design/project1_system_architecture.pdf) ([PNG](design/project1_system_architecture.png))
- **Project 2 Architecture Figure**: [design/project2_system_architecture.pdf](design/project2_system_architecture.pdf) ([PNG](design/project2_system_architecture.png))
- **Project 2 V2 Plan, Status & ADRs**: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) · [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) · [docs/decisions/ADR-V2-01-to-07.md](docs/decisions/ADR-V2-01-to-07.md) · [contracts/openapi.yaml](contracts/openapi.yaml)
- **Project 1 Design Docs**: [docs/implementation_plan.md](docs/implementation_plan.md) · [docs/architecture_draft.md](docs/architecture_draft.md) · [docs/limitations.md](docs/limitations.md)

---

## 1. Project 2 (`Reproduce` Mode) Highlights

### Target Empirical Reproduction: Wu, Wang & Evans (*Nature*, 2019)
- **Paper**: *"Large teams develop and small teams disrupt science and technology"*, *Nature* 566, 378–382 (`https://doi.org/10.1038/s41586-019-0941-9`).
- **Verified Empirical Data**: Stratified cohort (`N = 60,120` records; `45,335` in `1954–2014`) from the authors' official Harvard Dataverse repository (`https://doi.org/10.7910/DVN/JPWNNK`, `CC0-1.0`) with SHA-256 provenance (`data_manifest.json`), plus bounded OpenAlex `S2` citation subgraphs (`5y` vs. `10y` forward citation windows) testing $D = (n_i - n_j)/(n_i + n_j + n_k)$.
- **Harness Engineering & Safety**:
  - **Decoupled Visual Personas vs. Runtime Sessions**: 6 persistent visual personas (`Main`, `Paper`, `Data`, `Analysis`, `Validation`, `Support`) decoupled from on-demand `runtime_agent_id` sessions.
  - **Dual-Pool `ResourceScheduler`**: Enforces `max_active_agent_tasks = 4` independently from `max_parallel_experiments = 2` and `exploration_budget_fraction <= 0.20`, releasing leases deterministically on failure/cancellation.
  - **`ToolPermissionGate` & `SafeExperimentRunner`**: Blocks unrestricted `Bash`/`Write`/`Edit`/`Task` tools, jails paths to the run sandbox, redacts secret keys from traces, and executes Python analysis in `-I` isolated subprocesses with stripped environments.
  - **Authentic Methodological Debate -> Pod -> `EvidenceGate`**: `Validation` audits forward citation-window truncation and `S1` vs. `S2` claim scope, triggering a 2-agent review table that expands smoothly into a 3-agent sensitivity Pod (`Analysis` + `Validation` + `Data`) before `EvidenceGate` permits report synthesis (`reproduction_report.pdf` & `reproduction_report.docx`).
- **Frozen L1.5 Workspace UX**:
  - **Left `Research Chat`**: Sole command write path (`POST /api/runs/{id}/chat` with `request_id` idempotency), collapsible process activity, Evidence Gate / Research Gain disclosure, deduplicated Artifact cards, and `InChatDocumentViewer`.
  - **Right `Living Research Canvas`**: Strictly read-only (`0` permanent edges, `0` document cards, `0` write POSTs, **zero Agent reaction** on click/hover, seeded 64-point SVG table morphing without `<path>` remounting, `prefers-reduced-motion` + `Pause motion` support).

---

## 2. Project 1 (`Data` Mode) Highlights

- **Adaptive Multi-Agent Pipeline**: `Orchestrator` -> parallel OpenAlex `MinerCoordinator`/`MinerWorkers` (`2–6` shards with offline seed fallback) -> `DataAgent` + `SemanticProfiler` (`python -I` sandbox) -> `AnalysisAgent` -> `VisualizationAgent` (`D3 v7` / `ECharts 5` / `Chart.js 4`) -> headless Chromium `ValidationAgent` (`<= 2` self-healing retries).
- **Infinite Canvas & Provenance**: Rightward version branching (`v1 -> v2 -> v3`), 80% modal with **5-column Semantic Data Flow Map**, side-by-side Dual-Pane Compare, and scroll-locked pinpoint visual comments.

---

## 3. Quick Start & Setup Instructions

### Prerequisites
- **Python 3.11+ / 3.13+** with [`uv`](https://docs.astral.sh/uv/)
- **Node.js 20+** with [`pnpm`](https://pnpm.io/)

### 1. Install Dependencies

```bash
cd backend && uv sync
cd ../frontend && pnpm install
cd ..
```

### 2. Configure Credentials Safely (Zero Hardcoded `sk-` Keys)
- **In-App UI**: Click **`Libra Research`** / **`click me to complete API config!`** on the homepage to configure an OpenAI-compatible key, model, thinking effort, and concurrency cap (saved locally in gitignored `data/runtime/user_api_config.json`).
- **Environment / Claude SDK**: Copy `cp .env.example .env` (set `ANTHROPIC_API_KEY` or log into `claude` CLI for `ClaudeSDKClient`, and/or set `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`).

### 3. Run Fullstack Web App (`Data` + `Reproduce`)

```bash
./start.sh
# Backend:  http://127.0.0.1:8000
# Frontend: http://127.0.0.1:5173
```

### 4. Run Project 2 Pure-Backend Interface Standalone (No Frontend Required)

```bash
# Headless CLI live reproduction (exports events.jsonl, CSVs, PNG figures, PDF & DOCX reports)
cd backend
uv run python -m libra_reproduce.cli run --paper-id paper_A --mode live --output-dir ../outputs/nature2019_live_verified

# Standalone OpenAPI V2 ASGI server (mounts ONLY Project 2 routes)
uv run uvicorn libra_reproduce.main:app --host 127.0.0.1 --port 8001
```

---

## 4. Automated Verification Suite

```bash
# 1. Validate OpenAPI 3.1 & JSON Schema 2020-12 contracts
cd backend && uv run python ../scripts/validate_contracts.py

# 2. Run Project 1 + Project 2 backend test suite (24 tests)
cd backend && uv run pytest -k "not s10_s11" -v

# 3. Frontend TypeScript + Vite production build
cd frontend && pnpm build

# 4. Playwright E2E acceptance suite for Reproduce V2
cd frontend && pnpm exec playwright test e2e/reproduce_v2.spec.ts
```
