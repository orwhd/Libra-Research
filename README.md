# Libra Research — Multi-Agent Research Canvas

An end-to-end **Harness-driven Multi-Agent Research Canvas** built on real OpenAlex scholarly data (2018–2025). Users ask natural-language analytical questions, inspect live Multi-Agent planning/execution/validation streams, explore independently running D3.js v7 HTML artifacts on an infinite research canvas, pin deferred visual comments with captured screenshots, evolve versions rightward via Chat, trace 6-stage provenance in Data Map, and compare any two versions side-by-side.

---

## Architecture & Engineering Rationale

Full L2–L4 design rationale and decision trade-offs are documented in:
- [docs/implementation_plan.md](docs/implementation_plan.md)
- [docs/architecture_draft.md](docs/architecture_draft.md)
- Paper-style system diagram (Design Test Option A): [design/project1_system_architecture.pdf](design/project1_system_architecture.pdf) ([PNG preview](design/project1_system_architecture.png))

### Stack Choices
- **Frontend (`frontend/`)**: React 19 + TypeScript + Vite (`127.0.0.1:5173`). Chosen for clean state separation, native `<dialog>` modal top-layer semantics, `<div popover="auto">` right-click context menus, custom GPU-transformed Infinite Canvas (`translate3d` + `320ms` stationary long-press marquee vs immediate drag pan), and `<iframe sandbox="allow-scripts">` HTML preview isolation.
- **Backend (`backend/`)**: Python 3.13 (`uv`) + FastAPI + SQLite (`WAL` mode) + SSE (`sse-starlette`). Chosen so data manipulation (`pandas`, `numpy`, `networkx`, `scipy`), LLM orchestration (`openai` SDK targeting `deepseek-flash`), isolated subprocess execution (`python -I`), and headless Chromium validation (`playwright`) run natively inside one lightweight local service.
- **Visualization Runtime**: Self-hosted `d3.v7.min.js` (`backend/app/static/vendor/d3.v7.min.js`, v7.9.0) served from `/vendor/d3.v7.min.js` so previews run offline/deterministically in sandboxed iframes and inline cleanly when exported via `Download`.

---

## Dataset Provenance (`data/seed/`)

- **Source**: OpenAlex Works API (`https://api.openalex.org/works`)
- **Snapshot**: [data/seed/openalex_ai_works_2018_2025.json](data/seed/openalex_ai_works_2018_2025.json) (details in [data/seed/README_DATA.md](data/seed/README_DATA.md))
- **Filter**: `publication_year:2018-2025,concepts.id:C154945302` (Artificial Intelligence, 400 works sorted by age-adjusted citation count)
- **Live vs. Snapshot Policy**: If `OPENALEX_API_KEY` is provided (or live query is requested), `openalex_client.py` queries OpenAlex directly; on HTTP 429 budget exhaustion or offline mode, it falls back automatically to the verified local OpenAlex snapshot while explicitly disclosing `retrieval_mode: "verified_local_openalex_snapshot"` in `DatasetSnapshot` and `Data Map`.

---

## Multi-Agent Workflow & Generalizability (Anti-Hardcoding)

Zero prompt-to-template routing or keyword `if/elif` chart switches exist in the codebase:
1. **Orchestrator (`backend/app/agents/orchestrator.py`)**: Reads Chat context, selected cards, and pending `VisualComment`s; decomposes requests into `1..N` artifact workflows; enforces an **ambiguity guard** (asks for clarification rather than guessing); reuses `DatasetSnapshot` IDs on visual-only revisions; runs bounded retries (`<= 2`).
2. **Data Agent (`data_agent.py`)**: Writes Python filtering/cleaning code executed inside `sandbox_runner.py` to materialize immutable `DatasetSnapshot`s with schema and missingness audits.
3. **Analysis Agent (`analysis_agent.py`)**: Inspects snapshot schema + `capability_catalog.py` and writes bespoke Python/NetworkX/Pandas code executed inside the `python -I` sandbox (AST import/builtin blocking, stripped env with zero API keys, 25s timeout, POSIX CPU/file limits).
4. **Visualization / HTML Agent (`visualization_agent.py`)**: Inspects exact computed `result.json` structure + D3 v7 primitives + optional pinned comment screenshots (multimodal input), and dynamically generates self-contained interactive HTML/CSS/D3 v7 code embedding the computed JSON verbatim.
5. **Validation Agent (`validation_agent.py` + `browser_validator.py`)**: Launches headless Chromium, checks `pageerror` / `console.error`, SVG/Canvas bounds (`>= 200x150`), mark counts, `NaN` attributes, exercises hover/control interactions, and captures the genuine `preview.png` thumbnail displayed on the Canvas.

---

## Setup & Startup

### 1. Environment Variables
Copy `.env.example` to `.env` (or export variables in your shell; never commit real keys):

```bash
cp .env.example .env
# Edit .env to provide LLM_API_KEY (or DEEPSEEK_API), LLM_BASE_URL, LLM_MODEL
```

### 2. Install Dependencies (Clean Environment)

```bash
# Backend (Python 3.13 via uv)
cd backend && uv sync --python 3.13
cd ..

# Frontend (Node >= 20 via pnpm)
cd frontend && pnpm install
cd ..
```

*(Optional) Refresh the OpenAlex seed snapshot:*

```bash
uv run --project backend python data/seed/fetch_openalex_seed.py
```

### 3. Run Both Servers (One Command)

```bash
./start.sh
```

Or run manually in two terminals:

```bash
# Terminal 1: Backend API (http://127.0.0.1:8000)
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Frontend UI (http://127.0.0.1:5173)
cd frontend && pnpm dev --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173` in Chrome/Edge/Firefox/Safari.

---

## Reproducing T3 Visualizations & Generalizability

1. **T3 3-Chart Suite (S01 / S07 / S08 / S09)**:
   > *"Using the real research dataset, show AI publication trends, an author collaboration network, and a hierarchical bundled network view."*
2. **Deferred Comment & Rightward Evolution (S03 / S04)**:
   - Open any Canvas thumbnail -> `Comments` -> `Pin on Preview` -> click chart region -> `Save Comment` -> `Minimize` (card shows pending badge; no new version yet).
   - Send in Chat: *"请应用合作网络图尚未解决的评论，改善节点标签可读性并增大间距。"* -> new `v2` spawns to the right of `v1`.
3. **Long-Press Marquee & Dual-Window Compare (S05 / S06)**:
   - Hold left mouse button on blank canvas for `>= 320ms` and drag across 2 cards -> right-click -> `Compare Selected (2/2)`.
4. **Generalizability / Anti-Hardcoding (S10)**:
   > *"Create an interactive topic-by-year heatmap with hover explanations and year filtering."*

---

## Automated Tests

```bash
# Backend unit, persistence, sandbox security & live Harness S10/S11 retry tests
cd backend && uv run pytest -v

# Live Playwright E2E acceptance suite (with backend & frontend running)
cd frontend && pnpm test:e2e
```
