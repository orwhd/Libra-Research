#!/usr/bin/env python3
"""
Generate Design Test Option A paper-style system & algorithm architecture figure:
  - design/project1_system_architecture.pdf (vector)
  - design/project1_system_architecture.png (300 DPI)
"""
from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

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
    radius: float = 0.010,
    title_size: float = 9.3,
    sub_size: float = 7.4,
) -> None:
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.003,rounding_size={radius}",
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
            y + h * 0.34,
            subtitle,
            ha="center",
            va="center",
            fontsize=sub_size,
            color=sub_color,
            linespacing=1.28,
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
        linewidth=1.1,
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
        fontsize=8.4,
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
    rad: float = 0.0,
    ls: str = "-",
    lx: float | None = None,
    ly: float | None = None,
) -> None:
    arr = FancyArrowPatch(
        start,
        end,
        connectionstyle=f"arc3,rad={rad}",
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
            fontsize=7.1,
            fontweight="bold",
            color=color,
            bbox={"boxstyle": "round,pad=0.16", "fc": "#ffffff", "ec": "none", "alpha": 0.94},
            zorder=6,
            family="DejaVu Sans",
        )


def draw_ortho_polyline(
    ax,
    pts: list[tuple[float, float]],
    color: str = "#334155",
    ls: str = "-",
    label: str = "",
    label_pos: tuple[float, float] | None = None,
) -> None:
    xs = [p[0] for p in pts[:-1]]
    ys = [p[1] for p in pts[:-1]]
    ax.plot(xs, ys, color=color, linewidth=1.35, linestyle=ls, zorder=5)
    arr = FancyArrowPatch(
        pts[-2],
        pts[-1],
        arrowstyle="-|>,head_length=5.5,head_width=3.2",
        color=color,
        linewidth=1.35,
        linestyle=ls,
        zorder=5,
    )
    ax.add_patch(arr)
    if label and label_pos:
        ax.text(
            label_pos[0],
            label_pos[1],
            label,
            ha="center",
            va="center",
            fontsize=7.1,
            fontweight="bold",
            color=color,
            bbox={"boxstyle": "round,pad=0.16", "fc": "#ffffff", "ec": "none", "alpha": 0.95},
            zorder=6,
            family="DejaVu Sans",
        )


def main() -> None:
    fig, ax = plt.subplots(figsize=(16.5, 9.6), dpi=300)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")

    # Header
    ax.text(
        0.5,
        0.968,
        "Research Canvas: Capability-Driven Multi-Agent Harness with Immutable Lineage & Deferred Visual Revision",
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
        "WHALES Lab · Project 1 System & Algorithm Architecture (Option A) — Generalizable Code-Gen, Sandboxed Execution, Headless Chromium QA & Infinite Canvas",
        ha="center",
        va="center",
        fontsize=8.5,
        color="#475569",
        family="DejaVu Sans",
    )

    # Region A (Left)
    draw_region(ax, 0.012, 0.085, 0.190, 0.825, "A. User Context & Capabilities", "#f8fafc", "#2563eb")
    draw_box(
        ax, 0.022, 0.745, 0.170, 0.120,
        "Natural Language Chat",
        "Research queries, follow-ups,\nambiguity guard & SSE\nlive task progress cards",
        fc="#eff6ff", ec="#2563eb", title_color="#1d4ed8",
    )
    draw_box(
        ax, 0.022, 0.560, 0.170, 0.125,
        "Deferred Visual Comments",
        "Spatial anchor (x_norm, y_norm),\nview state & screenshot\n(Saved only; NO auto-rebuild)",
        fc="#fffbeb", ec="#d97706", title_color="#b45309",
    )
    draw_box(
        ax, 0.022, 0.365, 0.170, 0.130,
        "OpenAlex Works Corpus",
        "Real AI works (2018–2025)\nLive filter API + verified local\nsnapshot w/ provenance",
        fc="#ecfdf5", ec="#059669", title_color="#047857",
    )
    draw_box(
        ax, 0.022, 0.115, 0.170, 0.185,
        "Capability Catalog",
        "Declarative specs (ZERO\nhardcoded prompt templates):\n• Dataset JSON schema\n• Pandas / NetworkX / SciPy\n• D3 v7 primitives & bridge",
        fc="#f5f3ff", ec="#7c3aed", title_color="#6d28d9",
    )

    # Region B (Center Top): Multi-Agent Harness
    draw_region(ax, 0.245, 0.325, 0.495, 0.585, "B. Capability-Driven Multi-Agent Harness & Isolated Execution", "#fbfdff", "#0f172a")
    draw_box(
        ax, 0.262, 0.745, 0.205, 0.120,
        "1. Orchestrator Agent",
        "Decomposes tasks, resolves\ntargets, reuses snapshot on style\nedits, bounds retries (<=2)",
        fc="#eff6ff", ec="#1d4ed8", title_color="#1e40af",
    )
    draw_box(
        ax, 0.520, 0.745, 0.205, 0.120,
        "2. Data Agent",
        "Generates Python filter/clean\nscript & missingness audit\n-> Immutable DatasetSnapshot",
        fc="#ecfdf5", ec="#059669", title_color="#047857",
    )
    draw_box(
        ax, 0.262, 0.545, 0.205, 0.125,
        "3. Analysis Agent",
        "Generates bespoke Python stats /\nNetworkX modularity / taxonomy\nfor any visualization paradigm",
        fc="#f5f3ff", ec="#7c3aed", title_color="#6d28d9",
    )
    draw_box(
        ax, 0.520, 0.545, 0.205, 0.125,
        "Isolated Python -I Sandbox",
        "AST security check + stripped\nenv (no keys) + 25s CPU/file\nlimits -> Verified result.json",
        fc="#1e293b", ec="#0f172a", title_color="#f8fafc", sub_color="#cbd5e1",
    )
    draw_box(
        ax, 0.262, 0.345, 0.205, 0.125,
        "4. Visualization Agent",
        "Generates D3 v7 HTML/JS\nembedding real Analysis JSON\n+ comment pin screenshots",
        fc="#eff6ff", ec="#2563eb", title_color="#1d4ed8",
    )
    draw_box(
        ax, 0.520, 0.345, 0.205, 0.125,
        "5. Validation Agent",
        "Headless Chromium QA: JS errors,\nSVG bounds, marks, NaN check,\nhover -> Real preview.png",
        fc="#ecfdf5", ec="#059669", title_color="#047857",
    )

    # Region C (Center Bottom): Immutable Provenance Store
    draw_region(ax, 0.245, 0.085, 0.495, 0.210, "C. Immutable Provenance & Storage (SQLite + Artifact Store)", "#f8fafc", "#475569")
    draw_box(
        ax, 0.262, 0.145, 0.140, 0.100,
        "DatasetSnapshot",
        "ds_xxx · records.json\nschema & missingness",
        fc="#ffffff", ec="#059669", title_size=8.6, sub_size=7.1,
    )
    draw_box(
        ax, 0.422, 0.145, 0.140, 0.100,
        "AnalysisRun",
        "ar_xxx · script.py\n& result.json metrics",
        fc="#ffffff", ec="#7c3aed", title_size=8.6, sub_size=7.1,
    )
    draw_box(
        ax, 0.582, 0.145, 0.143, 0.100,
        "ArtifactVersion",
        "av_xxx · index.html\npreview.png & comments",
        fc="#ffffff", ec="#2563eb", title_size=8.6, sub_size=7.1,
    )
    ax.text(
        0.492,
        0.108,
        "6-Stage Data Map DAG: Source → Collection → Transformation → DatasetSnapshot → AnalysisRun → ArtifactVersion",
        ha="center",
        va="center",
        fontsize=7.3,
        fontweight="bold",
        color="#334155",
        bbox={"boxstyle": "round,pad=0.22", "fc": "#e2e8f0", "ec": "none"},
        zorder=4,
    )

    # Region D (Right Column): Canvas & Modal
    draw_region(ax, 0.780, 0.085, 0.208, 0.825, "D. Infinite Canvas & Modal", "#f8fafc", "#059669")
    draw_box(
        ax, 0.793, 0.640, 0.182, 0.225,
        "Infinite Research Canvas",
        "• Vertical stack per Task Group\n• Rightward version branching\n  (v1 → v2) w/ directed edges\n• Pan, zoom, 320ms long-press\n  marquee + right-click menu\n• Pending comment count badge",
        fc="#ffffff", ec="#059669", title_color="#047857",
    )
    draw_box(
        ax, 0.793, 0.360, 0.182, 0.215,
        "Artifact Modal (<dialog>)",
        "• Single: centered 80vw×80vh\n• Compare: 2 side-by-side\n  independent Artifact Windows\n• Preview in <iframe sandbox=\n  'allow-scripts'> (no Agent calls\n  on hover/drag/zoom/filter)",
        fc="#eff6ff", ec="#2563eb", title_color="#1d4ed8",
    )
    draw_box(
        ax, 0.793, 0.115, 0.182, 0.185,
        "Unified Bottom Dock",
        "Preview | Data Map | Comments\n| Sep | View Source | Download\n(.html / .zip) | Minimize\n\nSaved pin comments feed Chat\nfor deferred Chat-only revision",
        fc="#ffffff", ec="#334155", title_color="#0f172a",
    )

    # Clean gutter-routed arrows (Region A -> Region B)
    draw_arrow(ax, (0.192, 0.805), (0.262, 0.805), "Prompt", color="#2563eb", ly=0.825)
    draw_ortho_polyline(
        ax,
        [(0.192, 0.622), (0.220, 0.622), (0.220, 0.775), (0.262, 0.775)],
        color="#d97706",
        label="Pending pins",
        label_pos=(0.220, 0.700),
    )
    # OpenAlex corpus routed cleanly along top gutter above boxes -> Data Agent top!
    draw_ortho_polyline(
        ax,
        [(0.192, 0.430), (0.232, 0.430), (0.232, 0.922), (0.622, 0.922), (0.622, 0.865)],
        color="#059669",
        label="OpenAlex Works JSON",
        label_pos=(0.430, 0.922),
    )
    draw_ortho_polyline(
        ax,
        [(0.192, 0.205), (0.220, 0.205), (0.220, 0.585), (0.262, 0.585)],
        color="#7c3aed",
        label="Capabilities",
        label_pos=(0.220, 0.340),
    )

    # Internal Region B orchestration & data flows
    draw_arrow(ax, (0.467, 0.805), (0.520, 0.805), "Scope", color="#0f172a", ly=0.823)
    draw_arrow(ax, (0.364, 0.745), (0.364, 0.670), "Plan", color="#0f172a", lx=0.338)
    draw_arrow(ax, (0.467, 0.625), (0.520, 0.625), "Run .py", color="#7c3aed", ly=0.643)
    draw_arrow(ax, (0.520, 0.585), (0.467, 0.585), "JSON", color="#0f172a", ly=0.567)
    draw_arrow(ax, (0.364, 0.545), (0.364, 0.470), "Result Schema", color="#2563eb", lx=0.415)
    draw_arrow(ax, (0.467, 0.425), (0.520, 0.425), "HTML", color="#2563eb", ly=0.443)
    draw_arrow(
        ax,
        (0.520, 0.375),
        (0.467, 0.375),
        "Retry <= 2",
        color="#dc2626",
        ls="--",
        ly=0.354,
    )

    # Data Agent / Analysis Agent / Validation Agent -> Region C (routed in vertical gap lanes)
    draw_ortho_polyline(
        ax,
        [(0.725, 0.805), (0.752, 0.805), (0.752, 0.310), (0.332, 0.310), (0.332, 0.245)],
        color="#059669",
        ls=":",
    )
    draw_ortho_polyline(
        ax,
        [(0.364, 0.545), (0.364, 0.505), (0.492, 0.505), (0.492, 0.245)],
        color="#7c3aed",
        ls=":",
    )
    draw_ortho_polyline(
        ax,
        [(0.652, 0.345), (0.652, 0.245)],
        color="#059669",
        label="Validated",
        label_pos=(0.684, 0.305),
    )

    # Store -> Region D Canvas
    draw_ortho_polyline(
        ax,
        [(0.725, 0.195), (0.760, 0.195), (0.760, 0.750), (0.793, 0.750)],
        color="#059669",
        label="Thumbnails\n& Layout",
        label_pos=(0.760, 0.480),
    )
    draw_arrow(ax, (0.884, 0.640), (0.884, 0.575), "Click / Compare", color="#2563eb", lx=0.935)
    draw_arrow(ax, (0.884, 0.360), (0.884, 0.300), "", color="#334155")

    # Deferred Comment Feedback loop strictly in dedicated bottom gutter (y = 0.042)
    draw_ortho_polyline(
        ax,
        [(0.884, 0.115), (0.884, 0.042), (0.107, 0.042), (0.107, 0.085), (0.016, 0.085), (0.016, 0.622), (0.022, 0.622)],
        color="#d97706",
        ls="--",
        label="Save Comment Only (Zero Auto-Rebuild; Revision Triggered Later via Chat)",
        label_pos=(0.495, 0.042),
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_DIR / "project1_system_architecture.pdf"
    png_path = OUT_DIR / "project1_system_architecture.png"
    fig.savefig(pdf_path, bbox_inches="tight", pad_inches=0.14)
    fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0.14)
    plt.close(fig)
    print(f"Generated clean figure {pdf_path} and {png_path}")


if __name__ == "__main__":
    main()
