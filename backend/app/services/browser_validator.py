from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from ..config import VENDOR_DIR

CHROME_EXECUTABLE = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _inline_vendor_for_validation(html_text: str) -> str:
    """
    Replace `/vendor/d3.v7.min.js` with inline local bundle during standalone
    Playwright validation (`set_content` / file context) so the headless check
    runs completely self-contained without needing a live HTTP server loop.
    """
    d3_file = VENDOR_DIR / "d3.v7.min.js"
    if d3_file.exists():
        d3_code = d3_file.read_text(encoding="utf-8")
        # Escape </script> defensively if present
        d3_code_safe = d3_code.replace("</script>", "<\\/script>")
        for src_ref in (
            '<script src="/vendor/d3.v7.min.js"></script>',
            "<script src='/vendor/d3.v7.min.js'></script>",
            '<script src="https://d3js.org/d3.v7.min.js"></script>',
        ):
            if src_ref in html_text:
                html_text = html_text.replace(src_ref, f"<script>\n{d3_code_safe}\n</script>")
    return html_text


async def _launch_browser(p: Any):
    try:
        return await p.chromium.launch(headless=True)
    except Exception:
        if CHROME_EXECUTABLE.exists():
            return await p.chromium.launch(
                headless=True,
                executable_path=str(CHROME_EXECUTABLE),
            )
        raise


async def validate_html_and_capture(
    html_text: str,
    thumbnail_path: Path,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> dict[str, Any]:
    """
    Validation Agent browser verifier:
    1. Renders HTML inside an isolated headless Chromium page.
    2. Captures uncaught `pageerror` & `console.error` diagnostics.
    3. Inspects rendered DOM (<svg> / <canvas> size, rendered mark count, NaN coords).
    4. Exercises basic hover / input interaction to verify interactivity without runtime crashes.
    5. Saves a genuine PNG screenshot to `thumbnail_path`.
    """
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    self_contained_html = _inline_vendor_for_validation(html_text)

    page_errors: list[str] = []
    console_errors: list[str] = []

    async with async_playwright() as p:
        browser = await _launch_browser(p)
        try:
            context = await browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                device_scale_factor=1.5,
            )
            page = await context.new_page()
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text)
                if msg.type == "error"
                else None,
            )

            await page.set_content(self_contained_html, wait_until="load", timeout=15000)
            # Allow force simulations / animations / transitions to settle into readable frame
            await page.wait_for_timeout(1400)

            dom_check = await page.evaluate(
                """() => {
                    const svgs = Array.from(document.querySelectorAll('svg'));
                    const canvases = Array.from(document.querySelectorAll('canvas'));
                    const svgBoxes = svgs.map(s => {
                        const r = s.getBoundingClientRect();
                        return { width: r.width, height: r.height };
                    });
                    const canvasBoxes = canvases.map(c => {
                        const r = c.getBoundingClientRect();
                        return { width: r.width, height: r.height };
                    });
                    const hasLargeSvg = svgBoxes.some(b => b.width >= 200 && b.height >= 150);
                    const hasLargeCanvas = canvasBoxes.some(b => b.width >= 200 && b.height >= 150);

                    const marks = document.querySelectorAll(
                        'svg path, svg circle, svg rect, svg line, svg polygon, svg text'
                    );
                    let nanAttrs = 0;
                    for (const el of marks) {
                        for (const attr of ['d', 'cx', 'cy', 'x', 'y', 'width', 'height', 'x1', 'y1', 'x2', 'y2', 'transform']) {
                            const val = el.getAttribute(attr);
                            if (val && (val.includes('NaN') || val.includes('undefined'))) {
                                nanAttrs++;
                            }
                        }
                    }

                    const interactiveControls = document.querySelectorAll(
                        'input, select, button, [role="slider"]'
                    ).length;

                    const bodyTextLen = (document.body?.innerText || '').trim().length;
                    return {
                        svgCount: svgs.length,
                        canvasCount: canvases.length,
                        hasLargeVisual: hasLargeSvg || hasLargeCanvas,
                        svgBoxes,
                        canvasBoxes,
                        markCount: marks.length,
                        nanAttrs,
                        interactiveControls,
                        bodyTextLen,
                    };
                }"""
            )

            # Hover near center of primary SVG/canvas to smoke-test tooltip / hover handlers
            interaction_tested = False
            try:
                target_box = await page.evaluate(
                    """() => {
                        const el = document.querySelector('svg path, svg circle, svg rect, svg, canvas');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                    }"""
                )
                if target_box and target_box["x"] > 0 and target_box["y"] > 0:
                    await page.mouse.move(target_box["x"], target_box["y"])
                    await page.wait_for_timeout(150)
                    interaction_tested = True
            except Exception:
                pass

            await page.screenshot(path=str(thumbnail_path), full_page=False)
            await context.close()
        finally:
            await browser.close()

    reasons: list[str] = []
    if page_errors:
        reasons.append(f"Uncaught page errors: {page_errors[:3]}")
    if console_errors:
        # Ignore benign favicon console errors if any
        real_console_errs = [e for e in console_errors if "favicon" not in e.lower()]
        if real_console_errs:
            reasons.append(f"Console errors: {real_console_errs[:3]}")
    if not dom_check.get("hasLargeVisual"):
        reasons.append(
            f"No rendered <svg> or <canvas> with size >= 200x150 found (svgBoxes={dom_check.get('svgBoxes')}, canvasBoxes={dom_check.get('canvasBoxes')})"
        )
    if dom_check.get("svgCount", 0) > 0 and dom_check.get("markCount", 0) < 3:
        reasons.append(f"Too few SVG marks rendered ({dom_check.get('markCount')})")
    if dom_check.get("nanAttrs", 0) > 0:
        reasons.append(f"Found {dom_check.get('nanAttrs')} SVG attributes containing NaN/undefined")

    passed = len(reasons) == 0
    return {
        "passed": passed,
        "reasons": reasons,
        "page_errors": page_errors,
        "console_errors": console_errors,
        "dom_check": dom_check,
        "interaction_tested": interaction_tested,
        "thumbnail_saved": thumbnail_path.exists(),
    }


async def capture_comment_screenshot(
    html_text: str,
    screenshot_path: Path,
    x_norm: float,
    y_norm: float,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    filter_state: dict[str, Any] | None = None,
) -> Path:
    """
    Captures an authentic contextual screenshot of the Artifact HTML at the
    user's viewport dimensions and draws a crisp pin reticle at (x_norm, y_norm).
    """
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    self_contained_html = _inline_vendor_for_validation(html_text)
    vw = max(480, min(1920, int(viewport_width or 1280)))
    vh = max(360, min(1200, int(viewport_height or 720)))

    async with async_playwright() as p:
        browser = await _launch_browser(p)
        try:
            context = await browser.new_context(
                viewport={"width": vw, "height": vh},
                device_scale_factor=1.5,
            )
            page = await context.new_page()
            await page.set_content(self_contained_html, wait_until="load", timeout=15000)
            await page.wait_for_timeout(1200)

            if filter_state:
                await page.evaluate(
                    """(fs) => {
                        window.postMessage({ type: 'RC_APPLY_VIEW_STATE', filter_state: fs }, '*');
                    }""",
                    filter_state,
                )
                await page.wait_for_timeout(200)

            # Overlay a high-visibility non-destructive pin marker at normalized coordinate
            await page.evaluate(
                """([xn, yn]) => {
                    const marker = document.createElement('div');
                    marker.style.position = 'fixed';
                    marker.style.left = `${(xn * 100).toFixed(2)}%`;
                    marker.style.top = `${(yn * 100).toFixed(2)}%`;
                    marker.style.width = '28px';
                    marker.style.height = '28px';
                    marker.style.transform = 'translate(-50%, -50%)';
                    marker.style.borderRadius = '9999px';
                    marker.style.border = '3px solid #ef4444';
                    marker.style.background = 'rgba(239, 68, 68, 0.22)';
                    marker.style.boxShadow = '0 0 0 4px rgba(255,255,255,0.9)';
                    marker.style.zIndex = '2147483647';
                    marker.style.pointerEvents = 'none';
                    const dot = document.createElement('div');
                    dot.style.position = 'absolute';
                    dot.style.left = '50%';
                    dot.style.top = '50%';
                    dot.style.width = '8px';
                    dot.style.height = '8px';
                    dot.style.transform = 'translate(-50%, -50%)';
                    dot.style.borderRadius = '9999px';
                    dot.style.background = '#dc2626';
                    marker.appendChild(dot);
                    document.body.appendChild(marker);
                }""",
                [x_norm, y_norm],
            )
            await page.screenshot(path=str(screenshot_path), full_page=False)
            await context.close()
        finally:
            await browser.close()

    return screenshot_path
