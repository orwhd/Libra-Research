from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..config import RUNTIME_DIR
from .capability_catalog import D3_V7_CAPABILITY_GUIDE, IFRAME_VIEW_STATE_BRIDGE
from .llm_client import LLMClient, extract_code_block


def _summarize_structure(obj: Any, max_list_items: int = 3, depth: int = 0) -> Any:
    """Create a compact structural preview of analysis JSON so LLM knows exact keys & types."""
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return {k: _summarize_structure(v, max_list_items, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return {
            "_type": f"list(len={len(obj)})",
            "sample": [_summarize_structure(x, max_list_items, depth + 1) for x in obj[:max_list_items]],
        }
    return obj


_CHART_FIRST_LAYOUT_STYLE = """<style data-rc-chart-first="1">
:is(div, main, section):has(> header):has(> :has(svg), > svg) {
  display: flex !important;
  flex-direction: column !important;
  gap: 14px !important;
}
:is(div, main, section):has(> header):has(> :has(svg), > svg) > * {
  margin-top: 0 !important;
  order: 2;
}
:is(div, main, section):has(> header):has(> :has(svg), > svg) > :is(svg, :has(svg)) {
  order: 1 !important;
}
body {
  padding-bottom: 84px !important;
}
</style>"""


def _inject_data_and_bridge(html_code: str, analysis_result: dict[str, Any]) -> str:
    safe_json = json.dumps(analysis_result, ensure_ascii=False).replace("</script>", "<\\/script>")
    placeholder = "__ANALYSIS_DATA_JSON__"

    if placeholder in html_code:
        html_code = html_code.replace(placeholder, safe_json)
    else:
        # Fallback if model forgot placeholder: inject script#analysis-data inside <head> or <body>
        tag = f'<script id="analysis-data" type="application/json">{safe_json}</script>'
        if "</head>" in html_code:
            html_code = html_code.replace("</head>", f"{tag}\n</head>", 1)
        else:
            html_code = f"{tag}\n{html_code}"

    # Guarantee local D3 script path
    html_code = html_code.replace("https://d3js.org/d3.v7.min.js", "/vendor/d3.v7.min.js")
    html_code = html_code.replace("https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js", "/vendor/d3.v7.min.js")

    if 'data-rc-chart-first="1"' not in html_code:
        if "</head>" in html_code:
            html_code = html_code.replace("</head>", f"{_CHART_FIRST_LAYOUT_STYLE}\n</head>", 1)
        else:
            html_code = f"{_CHART_FIRST_LAYOUT_STYLE}\n{html_code}"

    if 'data-rc-bridge="1"' not in html_code:
        if "</body>" in html_code:
            html_code = html_code.replace("</body>", f"{IFRAME_VIEW_STATE_BRIDGE}\n</body>", 1)
        else:
            html_code = f"{html_code}\n{IFRAME_VIEW_STATE_BRIDGE}"
    return html_code


class VisualizationAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def generate_html(
        self,
        title: str,
        chart_kind: str,
        visualization_goal: str,
        analysis_run: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        parent_html: str | None = None,
        validation_feedback: str | None = None,
        previous_attempt_html: str | None = None,
        inject_fault: bool = False,
    ) -> str:
        result_path = RUNTIME_DIR / analysis_run["result_ref"]
        analysis_result = json.loads(result_path.read_text(encoding="utf-8"))
        structure_preview = _summarize_structure(analysis_result, max_list_items=4)

        comment_block = ""
        image_parts: list[dict[str, Any]] = []
        if comments:
            formatted = []
            for c in comments:
                formatted.append(
                    {
                        "comment_id": c["comment_id"],
                        "text": c["text"],
                        "anchor_norm": c.get("anchor"),
                        "element_hint": c.get("optional_element_hint"),
                        "preview_view_state": c.get("preview_view_state"),
                    }
                )
                shot_ref = c.get("screenshot_ref")
                if shot_ref:
                    shot_path = RUNTIME_DIR / shot_ref
                    if shot_path.exists() and len(image_parts) < 2:
                        b64 = base64.b64encode(shot_path.read_bytes()).decode("ascii")
                        image_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            }
                        )
            comment_block = f"""
USER VISUAL COMMENTS TO ADDRESS IN THIS REVISION:
{json.dumps(formatted, ensure_ascii=False, indent=2)}
Carefully examine the anchor locations, view states, and attached screenshots (red reticle marks where the user clicked), and directly solve every visual/layout/readability critique!
"""

        parent_block = ""
        if parent_html and not previous_attempt_html:
            # Strip large embedded JSON payload from parent HTML to save context
            import re
            compact_parent = re.sub(
                r'(<script id="analysis-data"[^>]*>)(.*?)(</script>)',
                r"\1__ANALYSIS_DATA_JSON__\3",
                parent_html,
                flags=re.DOTALL,
            )
            parent_block = f"""
EXISTING PARENT HTML (revise and improve this while preserving data fidelity & core interaction):
```html
{compact_parent[:14000]}
```
"""

        retry_block = ""
        if validation_feedback:
            import re
            compact_prev = ""
            if previous_attempt_html:
                compact_prev = re.sub(
                    r'(<script id="analysis-data"[^>]*>)(.*?)(</script>)',
                    r"\1__ANALYSIS_DATA_JSON__\3",
                    previous_attempt_html,
                    flags=re.DOTALL,
                )[:14000]
            retry_block = f"""
VALIDATION FAILURE FEEDBACK FROM HEADLESS CHROMIUM (MUST FIX ALL ISSUES):
{validation_feedback}

Previous broken HTML:
```html
{compact_prev}
```
"""

        text_prompt = f"""
You are the Visualization / HTML Agent. Design and implement a complete, self-contained, highly interactive, publication-grade HTML5 + CSS + D3.js v7 visualization page.

Artifact Title: {title}
Chart Paradigm: {chart_kind}
Visualization & Interaction Goal:
{visualization_goal}

Exact Schema & Sample of `ANALYSIS_DATA` (embedded at runtime in `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>`):
{json.dumps(structure_preview, ensure_ascii=False, indent=2)[:8000]}

{D3_V7_CAPABILITY_GUIDE}

{comment_block}
{parent_block}
{retry_block}

Critical Engineering Requirements:
1. Reference `<script src="/vendor/d3.v7.min.js"></script>` in `<head>`.
2. Include `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>` verbatim so the harness embeds the real computed JSON without mutation.
3. Read `const RAW = JSON.parse(document.getElementById('analysis-data').textContent); const DATA = RAW.data;` and `const SUMMARY = RAW.summary || {{}};` at startup. Inspect ONLY keys actually present in the schema above!
4. Render a responsive, legible light-mode interactive research interface:
   - Place the primary interactive D3 `<svg>` chart container FIRST at the top of the page, and place any summary KPI table/cards, empirical headline from `SUMMARY.headline`, and interactive controls (metric selector, year range / threshold slider, search/highlight input, bundle tension slider, reset zoom button — tailored to THIS chart!) BELOW the chart container.
   - Rich floating tooltip on hover showing exact scholarly details (paper count, citations, author/institution, degree, community, bundle paths, exact metric values) clamped inside the viewport.
   - Support pan/zoom or drag/highlight where appropriate for network & hierarchical layouts.
   - Ensure initial render happens immediately inside `DOMContentLoaded` (or bottom script) with synchronous force ticks if using `forceSimulation`, so within 1 second all SVG marks have valid finite numeric coordinates (NO `NaN`, NO `undefined`, NO 0x0 SVG).
5. Output ONLY the complete HTML document inside ```html ... ```.
"""

        if image_parts:
            user_content: str | list[dict[str, Any]] = [{"type": "text", "text": text_prompt}, *image_parts]
        else:
            user_content = text_prompt

        raw_html = await self.llm.complete_text(
            system_prompt=(
                "You are an expert data visualization engineer specializing in D3.js v7 and interactive scientific graphics. "
                "Output ONLY clean, valid, complete HTML inside a single ```html ... ``` code block."
            ),
            user_content=user_content,
            max_tokens=24576,
        )
        html_code = extract_code_block(raw_html, ("html", "htm"))
        final_html = _inject_data_and_bridge(html_code, analysis_result)

        if inject_fault:
            # Deliberate runtime JS error injected ONLY when S11 fault-injection test flag is active
            fault_script = '<script>/* S11_INJECTED_FAULT */ throw new Error("Simulated Visualization JS Runtime Fault for S11 Retry Verification");</script>'
            if "</body>" in final_html:
                final_html = final_html.replace("</body>", f"{fault_script}\n</body>", 1)
            else:
                final_html += fault_script

        return final_html
