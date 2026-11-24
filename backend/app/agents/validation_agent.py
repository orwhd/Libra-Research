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
