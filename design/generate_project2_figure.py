#!/usr/bin/env python3
"""
Generate WHALES Lab Part II Design Test (Option A) Paper-Style System & Algorithm Figure for Project 2:
  - design/project2_system_architecture.pdf (vector)
  - design/project2_system_architecture.png (300 DPI)
Accurately reflects the implemented Reproduce V2 (L1.5 + V1) architecture.
"""
from __future__ import annotations

from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

OUT_DIR = Path(__file__).resolve().parent


def draw_box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    subtitle: str = "",
    fc: str = "#ffffff",
    ec: str = "#334155",
    title_color: str = "#0f172a",
    sub_color: str = "#475569",
    lw: float = 1.35,
    title_size: float = 9.1,
    sub_size: float = 7.3,
) -> None:
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.003,rounding_size=0.010",
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
        zorder=3,
    )
    ax.add_patch(patch)
    if subtitle:
        ax.text(
            x + w / 2,
            y + h * 0.72,
            title,
            ha="center",
            va="center",
            fontsize=title_size,
            fontweight="bold",
            color=title_color,
            zorder=4,
            family="DejaVu Sans",
        )
        ax.text(
            x + w / 2,
            y + h * 0.33,
            subtitle,
            ha="center",
            va="center",
            fontsize=sub_size,
            color=sub_color,
            linespacing=1.26,
            zorder=4,
            family="DejaVu Sans",
        )
    else:
        ax.text(
            x + w / 2,
            y + h / 2,
            title,
            ha="center",
            va="center",
            fontsize=title_size,
            fontweight="bold",
            color=title_color,
            zorder=4,
            family="DejaVu Sans",
        )


def draw_region(ax, x: float, y: float, w: float, h: float, label: str, fc: str, ec: str) -> None:
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.004,rounding_size=0.012",
        facecolor=fc,
        edgecolor=ec,
        linewidth=1.15,
        linestyle="--",
        zorder=1,
    )
    ax.add_patch(patch)
    ax.text(
        x + 0.010,
        y + h - 0.012,
        label,
        ha="left",
        va="top",
        fontsize=8.3,
        fontweight="bold",
        color=ec,
        zorder=2,
        family="DejaVu Sans",
    )


def draw_arrow(
    ax,
    start: tuple[float, float],
    end: tuple[float, float],
    label: str = "",
    color: str = "#334155",
    ls: str = "-",
    lx: float | None = None,
    ly: float | None = None,
) -> None:
    arr = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>,head_length=5.5,head_width=3.2",
        color=color,
        linewidth=1.35,
        linestyle=ls,
        zorder=5,
    )
    ax.add_patch(arr)
    if label:
        mx = lx if lx is not None else (start[0] + end[0]) / 2
        my = ly if ly is not None else (start[1] + end[1]) / 2
        ax.text(
            mx,
            my,
            label,
            ha="center",
            va="center",
            fontsize=7.0,
            fontweight="bold",
            color=color,
            bbox={"boxstyle": "round,pad=0.16", "fc": "#ffffff", "ec": "none", "alpha": 0.94},
            zorder=6,
            family="DejaVu Sans",
        )


def main() -> None:
    fig, ax = plt.subplots(figsize=(16.5, 9.5), dpi=300)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")

    ax.text(
        0.5,
        0.968,
        "Mission-Anchored Scientific Reproduction Harness: Governance, Evidence Gate & Living Observation",
        ha="center",
        va="center",
        fontsize=13.0,
        fontweight="bold",
        color="#0f172a",
        family="DejaVu Sans",
    )
    ax.text(
        0.5,
        0.942,
        "WHALES Lab · Project 2 System Architecture (Option A) — Sole Chat Command Path, Dual-Pool Scheduler, Claude SDK MCP Tools, S1/S2 Provenance & Read-Only Living Canvas",
        ha="center",
        va="center",
        fontsize=8.3,
        color="#475569",
        family="DejaVu Sans",
    )

    # Region A: Researcher & Left Chat
    draw_region(ax, 0.012, 0.085, 0.212, 0.825, "A. Command & Document Hub (Chat)", "#f8fafc", "#2563eb")
    draw_box(
        ax, 0.024, 0.735, 0.181, 0.130,
        "Research Chat Gateway",
        "POST /api/runs/{id}/chat\n• Sole user write entry\n• (run_id, request_id) idempotent\n• Optional selected_context ref",
        fc="#eff6ff", ec="#2563eb", title_color="#1d4ed8",
    )
    draw_box(
        ax, 0.024, 0.545, 0.181, 0.135,
        "Collapsible Activity & Gain",
        "• Process timeline (#seq)\n• Evidence Gate verdict\n• 4 Research Gain buckets\n• Dual-pool slot readouts",
        fc="#ffffff", ec="#475569",
    )
    draw_box(
        ax, 0.024, 0.335, 0.181, 0.155,
        "Artifact Cards & Viewer",
        "Deduplicated by artifact_id:\n• PDF / Word (.docx) Reports\n• PNG Figures (220 DPI)\n• CSV/Parquet + Python scripts\n• In-Chat Viewer (0 Canvas clutter)",
        fc="#ecfdf5", ec="#059669", title_color="#047857",
    )
    draw_box(
        ax, 0.024, 0.115, 0.181, 0.165,
        "Headless CLI & Standalone API",
        "libra_reproduce.cli / main:app\nRuns backend-only in separate\nGit repo without frontend;\nregenerates reports from disk",
        fc="#f5f3ff", ec="#7c3aed", title_color="#6d28d9",
    )

    # Region B: Multi-Agent Governance & Controlled Science Execution
    draw_region(ax, 0.252, 0.315, 0.488, 0.595, "B. Governance Harness, Controlled SDK & Empirical Science", "#fbfdff", "#0f172a")
    draw_box(
        ax, 0.268, 0.745, 0.212, 0.120,
        "MainAgentGateway & Coordinator",
        "Classifies 200 answered vs.\n202 accepted; orchestrates\nPaper/Data/Analysis/Validation",
        fc="#fffbeb", ec="#d97706", title_color="#b45309",
    )
    draw_box(
        ax, 0.512, 0.745, 0.212, 0.120,
        "Dual-Pool ResourceScheduler",
        "Pool 1: Active Agent Tasks (<=4)\nPool 2: Parallel Experiments (<=2)\nExploration Pod cap (<=20%)",
        fc="#fef2f2", ec="#dc2626", title_color="#b91c1c",
    )
    draw_box(
        ax, 0.268, 0.545, 0.212, 0.130,
        "ClaudeSDKClient (0.2.189)",
        "Restricted MCP 'science' tools;\ndisallowed_tools blocks Bash/Write;\ncan_use_tool PermissionGate",
        fc="#eff6ff", ec="#1d4ed8", title_color="#1e40af",
    )
    draw_box(
        ax, 0.512, 0.545, 0.212, 0.130,
        "SafeExperimentRunner (-E)",
        "Isolated Python subprocess;\nAST static check + stripped env\n(0 API keys) + POSIX rlimits",
        fc="#1e293b", ec="#0f172a", title_color="#f8fafc", sub_color="#cbd5e1",
    )
    draw_box(
        ax, 0.268, 0.340, 0.212, 0.135,
        "Empirical Science (Nature 2019)",
        "• S1: Harvard Dataverse CC0 MAG\n  (N=60,120; 1954–2014 + 2015–21)\n• S2: D=(ni-nj)/(ni+nj+nk)\n  toy graph + OpenAlex 5y/10y",
        fc="#ecfdf5", ec="#059669", title_color="#047857",
    )
    draw_box(
        ax, 0.512, 0.340, 0.212, 0.135,
        "Independent Evidence Gate",
        "Rejects consensus-only claims;\nverifies SHA-256, N conservation,\nPNG headers & S1/S2 honesty\n-> ReportBuilder (PDF + DOCX)",
        fc="#f5f3ff", ec="#7c3aed", title_color="#6d28d9",
    )

    # Region C: Append-Only EventStore
    draw_region(ax, 0.252, 0.085, 0.488, 0.195, "C. Transactional SQLite EventStore & Scoped ArtifactStore", "#f8fafc", "#475569")
    draw_box(
        ax, 0.268, 0.115, 0.215, 0.115,
        "Append-Only Event Ledger",
        "Single-tx state + HarnessEvent\nUNIQUE(run_id, seq); SSE resume\nvia Last-Event-ID / ?after=N",
        fc="#ffffff", ec="#334155",
    )
    draw_box(
        ax, 0.508, 0.115, 0.216, 0.115,
        "Snapshot & Scoped Artifacts",
        "RunSnapshot + CanvasProjection\n(?at_seq=N zero-exec replay);\nno raw host paths exposed",
        fc="#ffffff", ec="#059669",
    )

    # Region D: Read-Only Living Research Canvas
    draw_region(ax, 0.772, 0.085, 0.216, 0.825, "D. Read-Only Living Research Canvas (L1.5)", "#fbfaf6", "#1b1b1f")
    draw_box(
        ax, 0.786, 0.655, 0.188, 0.205,
        "Initial 6-Persona Roster",
        "• Frame 1: all 6 personas idle\n  (persona_id != runtime_agent_id)\n• Main fixed at left Boss anchor\n• Subtle 1–2% async breathing\n• Zero permanent graph edges",
        fc="#ffffff", ec="#1b1b1f",
    )
    # Mini visual illustration of Main + 2/3-4/5-6 hand-drawn table morphing
    draw_box(
        ax, 0.786, 0.365, 0.188, 0.240,
        "Temporary Research Tables",
        "64-pt seeded SVG path morphing:\n• 2 agents -> Rounded Rectangle\n• 3–4 agents -> Circular Table\n• 5–6 agents -> Elliptical Table\n\nErases & returns to home anchors\non collaboration.ended",
        fc="#fffdf7", ec="#d97706", title_color="#92400e",
    )
    for idx_c, col in enumerate(["#FFD24A", "#FF9FD8", "#7CC8FF", "#4FF5C8", "#FF9A6B", "#B9A6FF"]):
        circ = Circle((0.808 + idx_c * 0.028, 0.632), 0.0085, facecolor=col, edgecolor="#1b1b1f", linewidth=1.1, zorder=5)
        ax.add_patch(circ)

    draw_box(
        ax, 0.786, 0.115, 0.188, 0.200,
        "Observation != Interruption",
        "• Clicking Agent triggers ZERO\n  body/face reaction & 0 POSTs\n• Silently scrolls Left Chat to\n  existing activity records\n• Explicit SIMULATION / REPLAY\n  badges & WCAG Pause Motion",
        fc="#ffffff", ec="#334155",
    )

    # Arrows
    draw_arrow(ax, (0.205, 0.805), (0.268, 0.805), "ChatCommand", color="#2563eb", ly=0.826)
    draw_arrow(ax, (0.480, 0.805), (0.512, 0.805), "Lease", color="#dc2626", ly=0.826)
    draw_arrow(ax, (0.374, 0.745), (0.374, 0.675), "Dispatch", color="#0f172a", lx=0.335)
    draw_arrow(ax, (0.480, 0.610), (0.512, 0.610), "MCP Tool", color="#1d4ed8", ly=0.630)
    draw_arrow(ax, (0.374, 0.545), (0.374, 0.475), "Data/S2", color="#059669", lx=0.335)
    draw_arrow(ax, (0.618, 0.545), (0.618, 0.475), "CSV + PNG", color="#0f172a", lx=0.662)
    draw_arrow(ax, (0.480, 0.408), (0.512, 0.408), "Verify", color="#7c3aed", ly=0.428)

    draw_arrow(ax, (0.374, 0.340), (0.374, 0.230), "Commit Events", color="#334155", lx=0.320)
    draw_arrow(ax, (0.618, 0.340), (0.618, 0.230), "Reports & Hash", color="#059669", lx=0.672)

    draw_arrow(ax, (0.724, 0.172), (0.786, 0.460), "Read-Only SSE +\nCanvasProjection", color="#1b1b1f", lx=0.755, ly=0.290)
    draw_arrow(ax, (0.268, 0.172), (0.205, 0.405), "File Cards &\nMessages", color="#059669", lx=0.232, ly=0.275)
    # Orthogonal bottom-gutter read-only navigation path (zero crossing over interior boxes)
    pts_nav = [(0.880, 0.115), (0.880, 0.042), (0.114, 0.042), (0.114, 0.085)]
    ax.plot([p[0] for p in pts_nav[:-1]], [p[1] for p in pts_nav[:-1]], color="#64748b", lw=1.35, ls="--", zorder=5)
    ax.add_patch(
        FancyArrowPatch(
            pts_nav[-2],
            pts_nav[-1],
            arrowstyle="-|>,head_length=5.5,head_width=3.2",
            color="#64748b",
            linewidth=1.35,
            linestyle="--",
            zorder=5,
        )
    )
    ax.text(
        0.500,
        0.042,
        "Silent Canvas -> Chat Activity Scroll Only (Zero Body/Face Reaction · Zero Write POSTs)",
        ha="center",
        va="center",
        fontsize=7.2,
        fontweight="bold",
        color="#475569",
        bbox={"boxstyle": "round,pad=0.18", "fc": "#ffffff", "ec": "none", "alpha": 0.96},
        zorder=6,
        family="DejaVu Sans",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_DIR / "project2_system_architecture.pdf"
    png_path = OUT_DIR / "project2_system_architecture.png"
    fig.savefig(pdf_path, bbox_inches="tight", pad_inches=0.14)
    fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0.14)
    plt.close(fig)
    print(f"Generated {pdf_path} and {png_path}")


if __name__ == "__main__":
    main()
