from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import docx
import pandas as pd
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .evidence_gate import EvidenceReviewResult


def _read_png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        raw = path.read_bytes()
        if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
            w, h = struct.unpack(">II", raw[16:24])
            if w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        pass
    return None


def _extract_table_preview(csv_path: Path, max_rows: int = 12, max_cols: int = 7) -> tuple[list[str], list[list[str]]]:
    df = pd.read_csv(csv_path).iloc[:max_rows, :max_cols]
    headers = [str(c)[:28] for c in df.columns]
    rows: list[list[str]] = []
    for _, r in df.iterrows():
        formatted: list[str] = []
        for val in r.tolist():
            if isinstance(val, float):
                formatted.append(f"{val:.4g}")
            else:
                formatted.append(str(val)[:32])
        rows.append(formatted)
    return headers, rows


def build_reproduction_reports(
    *,
    output_dir: Path,
    run_id: str,
    user_prompt: str,
    paper_spec: dict[str, Any],
    data_manifest: dict[str, Any],
    executed_trials: list[dict[str, Any]],
    recorded_findings: list[dict[str, Any]],
    table_paths: list[Path],
    figure_paths: list[Path],
    evidence_result: EvidenceReviewResult,
    debate_records: list[dict[str, Any]],
    sdk_provider_used: str,
) -> dict[str, Path]:
    """
    Universal, paper-agnostic DOCX & PDF scientific reproduction report generator.
    Dynamically formats title, methodology, provenance, multi-round experiments, tables, figures,
    and Evidence Gate findings for ANY target paper.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    level_badge = (
        f"{evidence_result.reproduction_level_verified} "
        f"({data_manifest.get('dataset_kind', 'controlled_experiment')})"
    )

    docx_path = output_dir / "reproduction_report.docx"
    pdf_path = output_dir / "reproduction_report.pdf"

    _write_docx_report(
        docx_path=docx_path,
        run_id=run_id,
        generated_ts=generated_ts,
        level_badge=level_badge,
        user_prompt=user_prompt,
        paper_spec=paper_spec,
        data_manifest=data_manifest,
        executed_trials=executed_trials,
        recorded_findings=recorded_findings,
        table_paths=table_paths,
        figure_paths=figure_paths,
        evidence_result=evidence_result,
        debate_records=debate_records,
        sdk_provider_used=sdk_provider_used,
    )
    _write_pdf_report(
        pdf_path=pdf_path,
        run_id=run_id,
        generated_ts=generated_ts,
        level_badge=level_badge,
        user_prompt=user_prompt,
        paper_spec=paper_spec,
        data_manifest=data_manifest,
        executed_trials=executed_trials,
        recorded_findings=recorded_findings,
        table_paths=table_paths,
        figure_paths=figure_paths,
        evidence_result=evidence_result,
        debate_records=debate_records,
        sdk_provider_used=sdk_provider_used,
    )
    return {"docx_path": docx_path, "pdf_path": pdf_path}


def _write_docx_report(
    *,
    docx_path: Path,
    run_id: str,
    generated_ts: str,
    level_badge: str,
    user_prompt: str,
    paper_spec: dict[str, Any],
    data_manifest: dict[str, Any],
    executed_trials: list[dict[str, Any]],
    recorded_findings: list[dict[str, Any]],
    table_paths: list[Path],
    figure_paths: list[Path],
    evidence_result: EvidenceReviewResult,
    debate_records: list[dict[str, Any]],
    sdk_provider_used: str,
) -> None:
    doc = docx.Document()
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(10.5)

    paper_title = str(paper_spec.get("paper_title") or "Scientific Paper Reproduction")
    title = doc.add_heading(f"Scientific Paper Reproduction Report: {paper_title}", level=1)
    for run in title.runs:
        run.font.color.rgb = RGBColor(15, 23, 42)

    meta_p = doc.add_paragraph()
    meta_p.add_run("Target Paper: ").bold = True
    meta_p.add_run(
        f"{paper_title} ({paper_spec.get('authors_year', 'Unspecified')}) | Domain: {paper_spec.get('domain', 'General Science')}\n"
    )
    if paper_spec.get("doi_or_url"):
        meta_p.add_run("Reference: ").bold = True
        meta_p.add_run(f"{paper_spec['doi_or_url']}\n")
    meta_p.add_run("Run ID: ").bold = True
    meta_p.add_run(f"{run_id}   |   ")
    meta_p.add_run("Generated: ").bold = True
    meta_p.add_run(f"{generated_ts}\n")
    meta_p.add_run("Verified Reproduction Scope: ").bold = True
    meta_p.add_run(f"{level_badge}\n")
    meta_p.add_run("Evidence Gate Verdict: ").bold = True
    meta_p.add_run(f"{evidence_result.result.upper()}   |   Harness Provider: {sdk_provider_used}\n")
    meta_p.add_run("User Objective: ").bold = True
    meta_p.add_run(user_prompt)

    doc.add_heading("1. Methodology & Core Claims Investigated", level=2)
    doc.add_paragraph(str(paper_spec.get("methodology_summary") or user_prompt))
    for claim in paper_spec.get("core_claims") or []:
        doc.add_paragraph(f"Core Claim: {claim}", style="List Bullet")
    for rival in paper_spec.get("rival_hypotheses") or []:
        doc.add_paragraph(f"Rival / Confounding Hypothesis Tested: {rival}", style="List Bullet")

    doc.add_heading("2. Data & Experimental Environment Provenance", level=2)
    prov_items = [
        f"Source / Benchmark: {data_manifest.get('source_doi_or_url', 'User-provided')}",
        f"Dataset / Protocol Kind: {data_manifest.get('dataset_kind', 'controlled')} (License: {data_manifest.get('license', 'N/A')})",
        f"Primary File: {data_manifest.get('primary_data_file', 'N/A')} | SHA-256: {data_manifest.get('file_sha256', '')}",
        f"Schema / Protocol Audit: {data_manifest.get('schema_verification_note', '')}",
    ]
    for item in prov_items:
        doc.add_paragraph(item, style="List Bullet")

    doc.add_heading("3. Multi-Round Experimental Trials & Findings", level=2)
    for t in executed_trials:
        if not t.get("ok"):
            continue
        doc.add_paragraph(
            f"[{t.get('round_label', 'trial')}] {t.get('script_name')} (SHA-256 {str(t.get('script_sha256', ''))[:12]}, {t.get('duration_ms', 0)} ms) — "
            f"{t.get('hypothesis', '')} | Output: {str(t.get('stdout', '')).strip()[:240]}",
            style="List Bullet",
        )
    for f in recorded_findings:
        doc.add_paragraph(
            f"Finding [{f.get('round_id', 'exp')}] ({f.get('verdict', '')}): {f.get('quantitative_result', '')}",
            style="List Bullet",
        )

    csv_tables = [p for p in table_paths if p.exists() and p.suffix.lower() == ".csv"][:3]
    if csv_tables:
        doc.add_heading("4. Quantitative Result Tables", level=2)
        for tp in csv_tables:
            try:
                headers, rows = _extract_table_preview(tp)
            except Exception:
                continue
            if not headers or not rows:
                continue
            doc.add_paragraph(f"Table: {tp.name}").runs[0].bold = True
            tbl = doc.add_table(rows=1, cols=len(headers))
            tbl.style = "Light Grid Accent 1"
            for idx, h in enumerate(headers):
                tbl.rows[0].cells[idx].text = h
            for row in rows:
                cells = tbl.add_row().cells
                for idx, val in enumerate(row):
                    cells[idx].text = val

    valid_figs = [p for p in figure_paths if _read_png_dimensions(p) is not None][:6]
    if valid_figs:
        doc.add_heading("5. Generated Experimental Figures", level=2)
        for idx, fp in enumerate(valid_figs, start=1):
            doc.add_picture(str(fp), width=Inches(6.2))
            doc.add_paragraph(f"Figure {idx}: {fp.name}")

    doc.add_heading("6. Validation Debate, Evidence Gate & Research Gain", level=2)
    for drec in debate_records:
        doc.add_paragraph(
            f"Debate [{drec.get('id', 'debate')}] ({drec.get('status', 'resolved')}): {drec.get('claim', '')} — "
            f"{drec.get('resolution_summary', '')}",
            style="List Bullet",
        )
    for cat_title, key in [
        ("Verified Insights", "verified_insights"),
        ("Ruled-Out Explanations", "ruled_out_explanations"),
        ("Open Questions & Limitations", "open_questions"),
        ("Methodological Lessons", "methodology_lessons"),
    ]:
        items = evidence_result.research_gains.get(key, [])
        if items:
            p = doc.add_paragraph()
            p.add_run(f"{cat_title}:").bold = True
            for it in items:
                doc.add_paragraph(it, style="List Bullet")

    doc.save(str(docx_path))


def _write_pdf_report(
    *,
    pdf_path: Path,
    run_id: str,
    generated_ts: str,
    level_badge: str,
    user_prompt: str,
    paper_spec: dict[str, Any],
    data_manifest: dict[str, Any],
    executed_trials: list[dict[str, Any]],
    recorded_findings: list[dict[str, Any]],
    table_paths: list[Path],
    figure_paths: list[Path],
    evidence_result: EvidenceReviewResult,
    debate_records: list[dict[str, Any]],
    sdk_provider_used: str,
) -> None:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RepTitle",
        parent=styles["Heading1"],
        fontSize=14.5,
        leading=18.5,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "RepH2",
        parent=styles["Heading2"],
        fontSize=11.5,
        leading=15,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=10,
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "RepBody",
        parent=styles["BodyText"],
        fontSize=9.2,
        leading=13,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=5,
    )
    caption_style = ParagraphStyle(
        "RepCap",
        parent=body_style,
        fontSize=8.2,
        leading=11,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )

    e = xml_escape
    paper_title = str(paper_spec.get("paper_title") or "Scientific Paper Reproduction")
    story: list[Any] = [
        Paragraph(f"Paper Reproduction Report: {e(paper_title)}", title_style),
        Paragraph(
            f"<b>Authors / Year:</b> {e(str(paper_spec.get('authors_year', 'Unspecified')))} &nbsp;|&nbsp; "
            f"<b>Domain:</b> {e(str(paper_spec.get('domain', 'General Science')))}<br/>"
            f"<b>Run ID:</b> {e(run_id)} &nbsp;|&nbsp; <b>Generated:</b> {e(generated_ts)}<br/>"
            f"<b>Verified Scope:</b> {e(level_badge)} &nbsp;|&nbsp; "
            f"<b>Evidence Gate:</b> {e(evidence_result.result.upper())} &nbsp;|&nbsp; "
            f"<b>Harness:</b> {e(sdk_provider_used)}<br/>"
            f"<b>Query:</b> {e(user_prompt)}",
            body_style,
        ),
    ]

    story.append(Paragraph("1. Methodology &amp; Claims", h2_style))
    story.append(Paragraph(e(str(paper_spec.get("methodology_summary") or user_prompt)), body_style))
    for c in (paper_spec.get("core_claims") or [])[:5]:
        story.append(Paragraph(f"&bull; <b>Claim:</b> {e(str(c))}", body_style))
    for r in (paper_spec.get("rival_hypotheses") or [])[:4]:
        story.append(Paragraph(f"&bull; <b>Rival Hypothesis:</b> {e(str(r))}", body_style))

    story.append(Paragraph("2. Data &amp; Environment Provenance", h2_style))
    story.append(
        Paragraph(
            f"<b>Source:</b> {e(str(data_manifest.get('source_doi_or_url', 'User-provided')))}<br/>"
            f"<b>Dataset Kind:</b> {e(str(data_manifest.get('dataset_kind', 'controlled')))} &nbsp;|&nbsp; "
            f"<b>License:</b> {e(str(data_manifest.get('license', 'N/A')))}<br/>"
            f"<b>Primary SHA-256:</b> <font name='Courier' size='8'>{e(str(data_manifest.get('file_sha256', '')))}</font><br/>"
            f"<b>Audit Note:</b> {e(str(data_manifest.get('schema_verification_note', '')))}",
            body_style,
        )
    )

    story.append(Paragraph("3. Multi-Round Experimental Trials", h2_style))
    for t in executed_trials:
        if not t.get("ok"):
            continue
        story.append(
            Paragraph(
                f"&bull; <b>[{e(str(t.get('round_label', 'trial')))}] {e(str(t.get('script_name')))}</b> "
                f"(SHA-256 <font name='Courier' size='7.5'>{e(str(t.get('script_sha256', ''))[:12])}</font>): "
                f"{e(str(t.get('hypothesis', '')))} &rarr; {e(str(t.get('stdout', '')).strip()[:200])}",
                body_style,
            )
        )
    for f in recorded_findings:
        story.append(
            Paragraph(
                f"&bull; <b>Finding ({e(str(f.get('round_id', 'exp')))} &middot; {e(str(f.get('verdict', '')))}):</b> "
                f"{e(str(f.get('quantitative_result', '')))}",
                body_style,
            )
        )

    csv_tables = [p for p in table_paths if p.exists() and p.suffix.lower() == ".csv"][:3]
    if csv_tables:
        story.append(Paragraph("4. Quantitative Result Tables", h2_style))
        for tp in csv_tables:
            try:
                headers, rows = _extract_table_preview(tp)
            except Exception:
                continue
            if not headers or not rows:
                continue
            story.append(Paragraph(f"<b>{e(tp.name)}</b>", body_style))
            col_w = max(0.75 * inch, (6.9 * inch) / max(1, len(headers)))
            tbl = Table([headers, *rows], colWidths=[col_w] * len(headers))
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                    ]
                )
            )
            story.append(tbl)
            story.append(Spacer(1, 8))

    valid_figs = [(p, _read_png_dimensions(p)) for p in figure_paths]
    valid_figs = [(p, dims) for p, dims in valid_figs if dims is not None][:6]
    if valid_figs:
        story.append(Paragraph("5. Experimental Figures", h2_style))
        for idx, (fp, dims) in enumerate(valid_figs, start=1):
            w_px, h_px = dims
            target_w = 6.3 * inch
            target_h = min(4.5 * inch, target_w * (h_px / max(1, w_px)))
            story.append(RLImage(str(fp), width=target_w, height=target_h))
            story.append(Paragraph(f"Figure {idx}: {e(fp.name)}", caption_style))

    story.append(Paragraph("6. Validation Debate, Limitations &amp; Research Gain", h2_style))
    for drec in debate_records:
        story.append(
            Paragraph(
                f"&bull; <b>Debate ({e(str(drec.get('status', 'resolved')))}):</b> "
                f"{e(str(drec.get('claim', '')))} &mdash; {e(str(drec.get('resolution_summary', '')))}",
                body_style,
            )
        )
    for key, label in [
        ("verified_insights", "Verified"),
        ("ruled_out_explanations", "Ruled Out"),
        ("open_questions", "Open Question"),
        ("methodology_lessons", "Method Lesson"),
    ]:
        for it in (evidence_result.research_gains.get(key) or [])[:4]:
            story.append(Paragraph(f"&bull; <b>{label}:</b> {e(str(it))}", body_style))

    doc_pdf = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )
    doc_pdf.build(story)
