# Architecture Draft — Project 1: Research Canvas

## 1. End-to-End Multi-Agent Harness & UI Architecture

```mermaid
flowchart TB
  subgraph Client["Frontend: React 19 + TypeScript (Conversation-First -> Split Workspace)"]
    CHAT["Left Chat & Expandable Agent Task Cards\n(SSE Stream + Prompt Composer)"]
    CANVAS["Right Infinite Research Canvas\n(Pan / Wheel Zoom / 320ms Long-Press Marquee / Right-Click Popover)"]
    MODAL["Centered Artifact Modal (<dialog> showModal)\nSingle 80vw×80vh OR Dual Side-by-Side Compare 94vw×84vh"]
    DOCK["Unified Bottom Dock (role=toolbar)\nPreview | Data Map | Comments | Sep | View Source | Download | Minimize"]
    MODAL --> DOCK
  end

  subgraph Harness["Backend: FastAPI + Async Multi-Agent Harness"]
    API["REST + SSE API (/workspaces, /tasks, /runs/:id/events, /versions/:id/*)"]
    ORCH["Orchestrator Agent\n(Intent + Target Disambiguation + Task Decomposition + Retry Policy)"]
    CAT["Capability Catalog\n(Dataset Schema + Python/NetworkX + D3 v7 Primitives)"]
    DATA["Data Agent\n(OpenAlex Filter / Snapshot Fallback + Cleaning Script)"]
    ANALYSIS["Analysis Agent\n(Python Stat / Network / Hierarchy Code-Gen)"]
    VIS["Visualization / HTML Agent\n(D3 v7 Interactive HTML Code-Gen + Comment Context)"]
    VAL["Validation Agent\n(Headless Chromium QA + Bounds/NaN/Runtime Checks + Thumbnail)"]
  end

  subgraph Sandbox["Isolated Execution & Persistence"]
    PY["Python -I Sandbox\n(AST Filter + Stripped Env + 25s Timeout)"]
    PW["Playwright Chromium\n(1280×800 Isolated Context)"]
    STORE["Immutable Artifact Store + SQLite\n(DatasetSnapshot | AnalysisRun | ArtifactVersion | VisualComment)"]
    LINEAGE["Deterministic Data Lineage Manager\n(6-Node Provenance DAG)"]
  end

  CHAT -- "POST /tasks (natural language + apply_pending_comments)" --> API
  API -- "SSE RunEvents" --> CHAT
  API --> ORCH
  ORCH --> CAT
  ORCH --> DATA --> PY --> STORE
  ORCH --> ANALYSIS --> PY --> STORE
  ORCH --> VIS --> PW
  VIS --> VAL --> PW
  VAL -- "JS error / empty SVG / NaN (retry <= 2)" --> VIS
  VAL -- "Pass: preview.png + validated HTML" --> STORE
  DATA --> LINEAGE
  ANALYSIS --> LINEAGE
  VAL --> LINEAGE
  LINEAGE --> STORE
  STORE --> CANVAS
  CANVAS -- "Click card (1) / Marquee+Compare (2)" --> MODAL
  MODAL -- "Save Comment ONLY (no rebuild)" --> STORE
```

## 2. Deferred Comment-to-Revision State Machine

```mermaid
stateDiagram-v2
  [*] --> ValidatedV1: Validation Agent passes HTML & saves preview.png
  ValidatedV1 --> ModalPreview: User clicks thumbnail (80vw x 80vh)
  ModalPreview --> ModalPreview: Native hover/drag/zoom/filter (NO Agent call)
  ModalPreview --> CommentPinned: User switches to Comments, clicks (x_norm, y_norm), submits text
  CommentPinned --> ValidatedV1: Minimize -> card badge shows pending_count++, NO v2 created
  ValidatedV1 --> Revising: User instructs Chat to apply pending comments
  Revising --> ValidationCheck: Orchestrator reuses DatasetSnapshot (unless data scope changed), runs Vis Agent
  ValidationCheck --> Revising: JS/DOM failure (attempt <= 2)
  ValidationCheck --> FailedRetryingExhausted: Max retries exceeded (v1 stays intact!)
  ValidationCheck --> ValidatedV2: Pass -> v2 placed RIGHT of v1, comments marked addressed
```

## 3. Immutable 6-Stage Data Map Provenance Model

```text
[1. Source] (OpenAlex API / Verified Snapshot)
     │
     ▼
[2. Collection] (Filter query, UTC timestamp, raw count, API vs cache mode)
     │
     ▼
[3. Transformation] (Data Agent Python script, filtering/cleaning rules, missingness)
     │
     ▼
[4. Dataset Snapshot] (ds_xxx — immutable records.json + schema; reused on visual-only edits)
     │
     ▼
[5. Analysis Run] (ar_xxx — Analysis Agent Python script, metrics/graph/hierarchy JSON)
     │
     ▼
[6. Visualization Artifact] (av_xxx — D3 v7 HTML + Chromium validation trace + preview.png)
```
