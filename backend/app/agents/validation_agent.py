from __future__ import annotations

from pathlib import Path
from typing import Any

from ..services.browser_validator import validate_html_and_capture


class ValidationAgent:
    async def validate_artifact_html(
        self,
        html_text: str,
        thumbnail_path: Path,
    ) -> dict[str, Any]:
        """
        Executes generated HTML inside headless Chromium, verifies absence of JS
        runtime/console errors, checks SVG/Canvas bounds, mark count, absence of NaN
        attributes, exercises pointer interaction, and captures real thumbnail PNG.
        """
        report = await validate_html_and_capture(
            html_text=html_text,
            thumbnail_path=thumbnail_path,
            viewport_width=1280,
            viewport_height=800,
        )
        return report

    def validate_lineage_conservation(self, semantic_flow: dict[str, Any]) -> dict[str, Any]:
        """
        Verifies record-level quantity conservation across Sources -> Semantic Categories
        -> Retained / Excluded outcomes, and confirms any record-unit transformations in
        downstream Analysis nodes carry an explicit conversion explanation rather than
        appearing as unexplained data loss.
        """
        input_count = int(semantic_flow.get("total_input_count") or 0)
        retained_count = int(semantic_flow.get("total_retained_count") or 0)
        excluded_count = int(semantic_flow.get("total_excluded_count") or 0)
        sources = semantic_flow.get("sources") or []
        categories = semantic_flow.get("categories") or []
        analyses = semantic_flow.get("analyses") or []

        notes: list[str] = []
        src_sum = sum(int(s.get("raw_count") or 0) for s in sources)
        cat_raw_sum = sum(int(c.get("raw_count") or 0) for c in categories)
        cat_ret_sum = sum(int(c.get("retained_count") or 0) for c in categories)
        cat_exc_sum = sum(int(c.get("excluded_count") or 0) for c in categories)

        src_ok = src_sum == input_count
        cat_ok = cat_raw_sum == input_count
        split_ok = all(
            int(c.get("retained_count") or 0) + int(c.get("excluded_count") or 0)
            == int(c.get("raw_count") or 0)
            for c in categories
        )
        total_ok = (retained_count + excluded_count) == input_count
        delta = input_count - (retained_count + excluded_count)

        if not src_ok:
            notes.append(f"Source sum ({src_sum}) differs from raw input ({input_count}).")
        if not cat_ok:
            notes.append(f"Category sum ({cat_raw_sum}) differs from raw input ({input_count}).")
        if not split_ok:
            notes.append("At least one category has retained + excluded != raw.")
        if not total_ok:
            notes.append(
                f"Reconciliation delta={delta} (retained {retained_count} + excluded {excluded_count} vs input {input_count})."
            )

        unit_conversion_explained = True
        for ar in analyses:
            in_c = int(ar.get("input_count") or 0)
            out_c = int(ar.get("output_count") or 0)
            changed = bool(ar.get("unit_changed"))
            expl = str(ar.get("conversion_explanation") or "").strip()
            if (changed or (in_c > 0 and out_c != in_c)) and not expl:
                unit_conversion_explained = False
                notes.append(
                    f"Analysis `{ar.get('label', ar.get('analysis_run_id', ''))}` changed count ({in_c} -> {out_c}) without unit-conversion explanation."
                )
            elif changed and expl:
                notes.append(f"Unit conversion verified: {expl}")

        if src_ok and cat_ok and split_ok and total_ok:
            notes.insert(
                0,
                f"Exact conservation: {input_count} raw = {cat_ret_sum} retained + {cat_exc_sum} excluded across {len(categories)} category(ies).",
            )

        is_exact = src_ok and cat_ok and split_ok and total_ok
        passed = is_exact and unit_conversion_explained

        return {
            "passed": passed,
            "is_exact_conservation": is_exact,
            "unit_conversion_explained": unit_conversion_explained,
            "input_count": input_count,
            "retained_count": retained_count,
            "excluded_count": excluded_count,
            "delta": delta,
            "notes": notes,
        }
