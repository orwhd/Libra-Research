from __future__ import annotations

import base64
import json
import re
from typing import Any

from ..config import RUNTIME_DIR
from .capability_catalog import (
    IFRAME_VIEW_STATE_BRIDGE,
    build_visualization_capability_guide,
    rewrite_cdn_scripts_to_local_vendor,
)
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
:is(div, main, section):has(> :is(header, nav, form, .controls, .toolbar, .filters)):has(> :is(svg, canvas, [data-rc-chart-stage], [id*="chart" i], [class*="chart" i], [id*="viz" i], [class*="viz" i], [id*="graph" i], [class*="graph" i], [id*="plot" i], [class*="plot" i], :has(svg), :has(canvas), :has([data-rc-chart-stage]))) {
  display: flex !important;
  flex-direction: column !important;
  gap: 14px !important;
}
:is(div, main, section):has(> :is(header, nav, form, .controls, .toolbar, .filters)):has(> :is(svg, canvas, [data-rc-chart-stage], [id*="chart" i], [class*="chart" i], [id*="viz" i], [class*="viz" i], [id*="graph" i], [class*="graph" i], [id*="plot" i], [class*="plot" i], :has(svg), :has(canvas), :has([data-rc-chart-stage]))) > * {
  margin-top: 0 !important;
  order: 2;
}
:is(div, main, section):has(> :is(header, nav, form, .controls, .toolbar, .filters)):has(> :is(svg, canvas, [data-rc-chart-stage], [id*="chart" i], [class*="chart" i], [id*="viz" i], [class*="viz" i], [id*="graph" i], [class*="graph" i], [id*="plot" i], [class*="plot" i], :has(svg), :has(canvas), :has([data-rc-chart-stage]))) > :is(svg, canvas, [data-rc-chart-stage], [id*="chart" i], [class*="chart" i], [id*="viz" i], [class*="viz" i], [id*="graph" i], [class*="graph" i], [id*="plot" i], [class*="plot" i], :has(svg), :has(canvas), :has([data-rc-chart-stage])) {
  order: 1 !important;
}
body {
  padding-bottom: 112px !important;
}
</style>"""


def _inject_data_and_bridge(html_code: str, analysis_result: dict[str, Any]) -> str:
    safe_json = re.sub(
        r"</script",
        r"<\\/script",
        json.dumps(analysis_result, ensure_ascii=False),
        flags=re.IGNORECASE,
    )
    placeholder = "__ANALYSIS_DATA_JSON__"

    if placeholder in html_code:
        html_code = html_code.replace(placeholder, safe_json)
    else:
        tag = f'<script id="analysis-data" type="application/json">{safe_json}</script>'
        if "</head>" in html_code:
            html_code = html_code.replace("</head>", f"{tag}\n</head>", 1)
        else:
            html_code = f"{tag}\n{html_code}"

    html_code = rewrite_cdn_scripts_to_local_vendor(html_code)

    if 'data-rc-chart-first="1"' not in html_code:
        if "</head>" in html_code:
            html_code = html_code.replace("</head>", f"{_CHART_FIRST_LAYOUT_STYLE}\n</head>", 1)
        else:
            html_code = f"{_CHART_FIRST_LAYOUT_STYLE}\n{html_code}"

    if "data-rc-bridge=" not in html_code:
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
        capability_guide = build_visualization_capability_guide()

        comment_block = ""
        image_parts: list[dict[str, Any]] = []
        if comments:
            formatted = []
            for c in comments:
                pvs = c.get("preview_view_state") or {}
                fstate = pvs.get("filter_state") if isinstance(pvs, dict) else {}
                target_insp = fstate.get("__target_inspector") if isinstance(fstate, dict) else None
                clean_fstate = (
                    {fk: fv for fk, fv in fstate.items() if fk != "__target_inspector"}
                    if isinstance(fstate, dict)
                    else {}
                )
                formatted.append(
                    {
                        "comment_id": c["comment_id"],
                        "text": c["text"],
                        "anchor_norm": c.get("anchor"),
                        "scroll_offset": {
                            "scroll_x": pvs.get("scroll_x", 0) if isinstance(pvs, dict) else 0,
                            "scroll_y": pvs.get("scroll_y", 0) if isinstance(pvs, dict) else 0,
                        },
                        "element_hint": c.get("optional_element_hint"),
                        "target_inspector": target_insp,
                        "active_ui_filters": clean_fstate,
                        "viewport": {
                            "width": pvs.get("viewport_width", 1280) if isinstance(pvs, dict) else 1280,
                            "height": pvs.get("viewport_height", 720) if isinstance(pvs, dict) else 720,
                        },
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
Carefully inspect the anchor locations, view states, and attached screenshots (red reticle marks where the user clicked), and directly resolve every visual/layout/interaction critique while keeping existing working features intact!
"""

        parent_block = ""
        if parent_html and not previous_attempt_html:
            compact_parent = re.sub(
                r'(<script id="analysis-data"[^>]*>)(.*?)(</script>)',
                r"\1__ANALYSIS_DATA_JSON__\3",
                parent_html,
                flags=re.DOTALL,
            )
            parent_block = f"""
EXISTING PARENT HTML (revise surgically to address the request/comments while preserving data fidelity & working interactions):
```html
{compact_parent[:14000]}
```
"""

        retry_block = ""
        if validation_feedback:
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
You are the Visualization Agent in a generalizable scientific visual analytics harness.
Interpret the visualization requirements, select the most suitable local visualization library/algorithm from the discovered runtime catalog below, and implement a complete, interactive, publication-grade HTML5 visualization page.

Artifact Title: {title}
Visualization Paradigm / Context: {chart_kind}
Visualization & Interaction Goal:
{visualization_goal}

Exact Schema & Sample of `ANALYSIS_DATA` (embedded at runtime in `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>`):
{json.dumps(structure_preview, ensure_ascii=False, indent=2)[:7500]}

{capability_guide}

{comment_block}
{parent_block}
{retry_block}

Engineering Requirements:
1. Select and include `<script src="/vendor/..."></script>` in `<head>` for whichever discovered local runtime(s) best fit the visualization goal (e.g. Apache ECharts 5 `/vendor/echarts.min.js`, D3.js v7 `/vendor/d3.v7.min.js`, Chart.js 4 `/vendor/chart.umd.min.js`, or native SVG/Canvas/WebGL). Never rely on external CDN links.
2. Include `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>` verbatim so the harness embeds the real computed JSON without mutation.
3. Read `const RAW = JSON.parse(document.getElementById('analysis-data').textContent); const DATA = RAW.data; const SUMMARY = RAW.summary || {{}};` at startup, referencing ONLY keys actually present in the schema above.
4. Keep code concise, clean, and fast-rendering:
   - Place the primary interactive chart stage FIRST at the top of `<body>` with `data-rc-chart-stage="1"` and explicit non-zero height (e.g. `height: 540px; width: 100%;`), and place all controls, filters, KPI summaries, and legends BELOW the chart stage.
   - Keep `body {{ padding-bottom: 112px; }}` and NEVER anchor fixed/sticky elements near the bottom 96px dock zone.
   - Render synchronously on load (no slow entrance delays; pre-tick iterative layouts if used) so `<svg>` or `<canvas>` is immediately >= 200x150 with zero `NaN`/`undefined` attributes.
5. Output ONLY the complete HTML document inside ```html ... ```.
"""

        if image_parts:
            user_content: str | list[dict[str, Any]] = [{"type": "text", "text": text_prompt}, *image_parts]
        else:
            user_content = text_prompt

        raw_html = await self.llm.complete_text(
            system_prompt=(
                "You are an expert interactive data visualization engineer. "
                "Autonomously select the optimal visualization library and visual encoding for the data and goal, "
                "and output ONLY clean, valid, complete HTML inside a single ```html ... ``` code block."
            ),
            user_content=user_content,
            max_tokens=16384,
            effort_override="low",
        )
        html_code = extract_code_block(raw_html, ("html", "htm"))
        final_html = _inject_data_and_bridge(html_code, analysis_result)

        if inject_fault:
            fault_script = '<script>/* S11_INJECTED_FAULT */ throw new Error("Simulated Visualization JS Runtime Fault for S11 Retry Verification");</script>'
            if "</body>" in final_html:
                final_html = final_html.replace("</body>", f"{fault_script}\n</body>", 1)
            else:
                final_html += fault_script

        return final_html
