from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from ..config import RUNTIME_DIR, VENDOR_DIR

CHROME_EXECUTABLE = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Per-event-loop shared Playwright & Chromium instances to avoid ~2s cold process launch per chart
_LOOP_BROWSERS: dict[int, tuple[Any, Any, Any]] = {}
_LOOP_LOCKS: dict[int, asyncio.Lock] = {}
_INFLIGHT_COMMENT_SCREENSHOTS: dict[str, asyncio.Task[Any]] = {}
_COMMENT_RENDER_JOBS: dict[str, dict[str, Any]] = {}
_COMPLETED_COMMENT_SCREENSHOTS: set[str] = set()


def schedule_comment_screenshot(comment_id: str, job_kwargs: dict[str, Any]) -> None:
    """Register and launch contextual comment screenshot capture asynchronously so Save returns in <15ms."""
    _COMMENT_RENDER_JOBS[comment_id] = job_kwargs

    async def _runner() -> None:
        try:
            await capture_comment_screenshot(**job_kwargs)
            _COMPLETED_COMMENT_SCREENSHOTS.add(comment_id)
            _COMMENT_RENDER_JOBS.pop(comment_id, None)
        except Exception:
            pass

    task = asyncio.create_task(_runner())
    _INFLIGHT_COMMENT_SCREENSHOTS[comment_id] = task

    def _cleanup(_t: asyncio.Task[Any]) -> None:
        _INFLIGHT_COMMENT_SCREENSHOTS.pop(comment_id, None)

    task.add_done_callback(_cleanup)


async def await_comment_screenshots(comment_ids: list[str] | None = None, timeout: float = 8.0) -> None:
    """Ensure background comment screenshots have finished (re-rendering on demand if an ephemeral test loop exited early)."""
    target_ids = (
        list(set(_INFLIGHT_COMMENT_SCREENSHOTS.keys()) | set(_COMMENT_RENDER_JOBS.keys()))
        if comment_ids is None
        else list(comment_ids)
    )
    tasks = [_INFLIGHT_COMMENT_SCREENSHOTS[cid] for cid in target_ids if cid in _INFLIGHT_COMMENT_SCREENSHOTS]
    if tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
        except Exception:
            pass

    for cid in target_ids:
        if cid not in _COMPLETED_COMMENT_SCREENSHOTS and cid in _COMMENT_RENDER_JOBS:
            job = _COMMENT_RENDER_JOBS.pop(cid, None)
            if job:
                try:
                    await capture_comment_screenshot(**job)
                    _COMPLETED_COMMENT_SCREENSHOTS.add(cid)
                except Exception:
                    pass

_VENDOR_SRC_RE = re.compile(
    r"""(<script\b[^>]*?\bsrc\s*=\s*["'])/vendor/([A-Za-z0-9._-]+\.js)(["'][^>]*>\s*</script>)""",
    flags=re.IGNORECASE,
)


def _prepare_local_file_html(html_text: str) -> str:
    """
    Rewrite `/vendor/<lib>.js` script tags to direct `file://` URIs pointing at `VENDOR_DIR`
    so Chromium loads local visualization runtimes natively from disk without `document.write`
    (`page.set_content`) or 1 MB inline script string expansion.
    """
    def _repl(match: re.Match[str]) -> str:
        prefix, fname, suffix = match.group(1), match.group(2), match.group(3)
        candidate = (VENDOR_DIR / fname).resolve()
        if candidate.exists():
            return f"{prefix}{candidate.as_uri()}{suffix}"
        return match.group(0)

    return _VENDOR_SRC_RE.sub(_repl, html_text)


async def _launch_browser(p: Any) -> Any:
    launch_args = [
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        return await p.chromium.launch(headless=True, args=launch_args)
    except Exception:
        if CHROME_EXECUTABLE.exists():
            return await p.chromium.launch(
                headless=True,
                executable_path=str(CHROME_EXECUTABLE),
                args=launch_args,
            )
        raise


async def _get_shared_browser() -> Any:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    lock = _LOOP_LOCKS.get(lid)
    if lock is None:
        lock = asyncio.Lock()
        _LOOP_LOCKS[lid] = lock

    async with lock:
        cached = _LOOP_BROWSERS.get(lid)
        if cached is not None:
            pw_ctx, browser, cached_loop = cached
            if cached_loop is loop and not loop.is_closed():
                try:
                    if browser.is_connected():
                        return browser
                except Exception:
                    pass
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw_ctx.stop()
            except Exception:
                pass
            _LOOP_BROWSERS.pop(lid, None)

        pw = await async_playwright().start()
        browser = await _launch_browser(pw)
        _LOOP_BROWSERS[lid] = (pw, browser, loop)
        return browser


def _write_temp_html(html_text: str) -> Path:
    tmp_dir = RUNTIME_DIR / "tmp_validate"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"val_{uuid.uuid4().hex[:12]}.html"
    tmp_path.write_text(_prepare_local_file_html(html_text), encoding="utf-8")
    return tmp_path


async def validate_html_and_capture(
    html_text: str,
    thumbnail_path: Path,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> dict[str, Any]:
    """
    Validation Agent browser verifier:
    1. Loads HTML via real `file://` navigation (`page.goto`) on a reusable headless Chromium instance
       (eliminating `page.set_content` / `Document.write` parser collisions with `<` inside JS strings).
    2. Captures uncaught `pageerror` & `console.error` diagnostics.
    3. Polls until `<svg>` or `<canvas>` renders at >= 200x150 (fast exit in ~150-300ms when ready).
    4. Inspects rendered DOM (<svg> / <canvas> size, rendered mark count, NaN coords) & smoke-tests hover.
    5. Saves a genuine PNG screenshot to `thumbnail_path`.
    """
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html_path = _write_temp_html(html_text)

    page_errors: list[str] = []
    console_errors: list[str] = []
    interaction_tested = False
    dom_check: dict[str, Any] = {}

    try:
        browser = await _get_shared_browser()
        context = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            device_scale_factor=1.5,
        )
        try:
            page = await context.new_page()
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text)
                if msg.type == "error"
                else None,
            )

            await page.goto(
                tmp_html_path.resolve().as_uri(),
                wait_until="domcontentloaded",
                timeout=12000,
            )

            # Fast readiness poll instead of fixed 1400ms sleep
            try:
                await page.wait_for_function(
                    """() => {
                        const svgs = Array.from(document.querySelectorAll('svg'));
                        const canvases = Array.from(document.querySelectorAll('canvas'));
                        const okCanvas = canvases.some(c => {
                            const r = c.getBoundingClientRect();
                            return r.width >= 200 && r.height >= 150;
                        });
                        if (okCanvas) return true;
                        const okSvg = svgs.some(s => {
                            const r = s.getBoundingClientRect();
                            return r.width >= 200 && r.height >= 150;
                        });
                        const marks = document.querySelectorAll(
                            'svg path, svg circle, svg rect, svg line, svg polygon, svg text'
                        ).length;
                        return okSvg && marks >= 3;
                    }""",
                    timeout=1100,
                )
                await page.wait_for_timeout(120)
            except Exception:
                await page.wait_for_timeout(250)

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
                        hasLargeSvg,
                        hasLargeCanvas,
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

            try:
                target_box = await page.evaluate(
                    """() => {
                        const el = document.querySelector('canvas, svg path, svg circle, svg rect, svg');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                    }"""
                )
                if target_box and target_box["x"] > 0 and target_box["y"] > 0:
                    await page.mouse.move(target_box["x"], target_box["y"])
                    await page.wait_for_timeout(80)
                    interaction_tested = True
            except Exception:
                pass

            await page.screenshot(path=str(thumbnail_path), full_page=False)
        finally:
            await context.close()
    finally:
        tmp_html_path.unlink(missing_ok=True)

    reasons: list[str] = []
    if page_errors:
        reasons.append(f"Uncaught page errors: {page_errors[:3]}")
    if console_errors:
        real_console_errs = [
            e for e in console_errors if "favicon" not in e.lower() and "err_file_not_found" not in e.lower()
        ]
        if real_console_errs:
            reasons.append(f"Console errors: {real_console_errs[:3]}")
    if not dom_check.get("hasLargeVisual"):
        reasons.append(
            f"No rendered <svg> or <canvas> with size >= 200x150 found (svgBoxes={dom_check.get('svgBoxes')}, canvasBoxes={dom_check.get('canvasBoxes')})"
        )
    if (
        not dom_check.get("hasLargeCanvas")
        and dom_check.get("hasLargeSvg")
        and dom_check.get("markCount", 0) < 3
    ):
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
    scroll_x: float = 0.0,
    scroll_y: float = 0.0,
    filter_state: dict[str, Any] | None = None,
) -> Path:
    """
    Captures an authentic contextual screenshot of the Artifact HTML at the
    user's exact viewport dimensions and scroll offset, hovers the commented spot
    so any chart tooltip opens in context, and draws a crisp pin reticle at (x_norm, y_norm).
    """
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html_path = _write_temp_html(html_text)
    vw = max(480, min(1920, int(viewport_width or 1280)))
    vh = max(360, min(1200, int(viewport_height or 720)))

    try:
        browser = await _get_shared_browser()
        context = await browser.new_context(
            viewport={"width": vw, "height": vh},
            device_scale_factor=1.5,
        )
        try:
            page = await context.new_page()
            await page.goto(
                tmp_html_path.resolve().as_uri(),
                wait_until="domcontentloaded",
                timeout=12000,
            )
            await page.wait_for_timeout(350)

            if filter_state or scroll_x or scroll_y:
                await page.evaluate(
                    """([fs, sx, sy]) => {
                        window.postMessage({ type: 'RC_APPLY_VIEW_STATE', filter_state: fs || {}, scroll_x: sx, scroll_y: sy }, '*');
                        window.scrollTo(sx || 0, sy || 0);
                    }""",
                    [filter_state or {}, float(scroll_x or 0.0), float(scroll_y or 0.0)],
                )
                await page.wait_for_timeout(120)

            try:
                px = max(2.0, min(vw - 2.0, float(x_norm) * vw))
                py = max(2.0, min(vh - 2.0, float(y_norm) * vh))
                await page.mouse.move(px, py)
                await page.wait_for_timeout(100)
            except Exception:
                pass

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
        finally:
            await context.close()
    finally:
        tmp_html_path.unlink(missing_ok=True)

    return screenshot_path
