from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.agents.llm_client import LLMClient
from app.models import ArtifactVersion

logger = logging.getLogger(__name__)

_REQUIRED_STAGES = (
    "planning",
    "collecting",
    "analyzing",
    "generating_html",
    "validating",
    "retrying",
    "summarizing",
    "completed",
)


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


class InteractionAgent:
    """Dedicated agent that synthesizes non-hardcoded streaming stage labels and interactive follow-up radio choices."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _fallback_stage_labels(
        self,
        user_prompt: str,
        planned_tasks: list[dict[str, Any]],
    ) -> dict[str, str]:
        titles = [str(t.get("title") or "").strip() for t in planned_tasks if t.get("title")]
        topic_hint = titles[0] if titles else user_prompt.strip()[:28]
        if _contains_cjk(user_prompt):
            return {
                "planning": f"规划「{topic_hint[:12]}」研究方案",
                "collecting": "多智能体并发检索与清洗数据",
                "analyzing": f"计算「{topic_hint[:12]}」统计与结构特征",
                "generating_html": "构建交互式可视化视图",
                "validating": "无头浏览器校验图表与交互",
                "retrying": "快速修复图表细节并复检",
                "summarizing": "整理分析结论与研究报告",
                "completed": "分析视图已就绪",
            }
        short_hint = re.sub(r"\s+", " ", topic_hint)[:26].strip(" -:,.")
        return {
            "planning": f"Scoping {short_hint or 'research goal'}",
            "collecting": f"Parallel mining {short_hint or 'dataset'} records",
            "analyzing": f"Computing {short_hint or 'analytical'} metrics",
            "generating_html": "Building interactive chart views",
            "validating": "Verifying DOM & visual quality",
            "retrying": "Refining chart layout & retries",
            "summarizing": "Synthesizing research findings",
            "completed": "Canvas artifacts ready",
        }

    async def synthesize_stage_labels(
        self,
        user_prompt: str,
        planned_tasks: list[dict[str, Any]],
    ) -> dict[str, str]:
        fallback = self._fallback_stage_labels(user_prompt, planned_tasks)
        task_briefs = [
            {
                "title": t.get("title"),
                "chart_kind": t.get("chart_kind"),
                "intent": t.get("intent"),
            }
            for t in planned_tasks
        ]
        lang_rule = (
            "Write all labels in Simplified Chinese."
            if _contains_cjk(user_prompt)
            else "Write all labels in concise English."
        )
        system_prompt = (
            "You are InteractionAgent in a Multi-Agent Research Canvas.\n"
            "Craft domain-specific, non-hardcoded progress stage labels shown in the rolling status bar.\n"
            f"{lang_rule}\n"
            "Return strictly a JSON object with a single key `labels` mapping EVERY one of these exact keys: "
            "`planning`, `collecting`, `analyzing`, `generating_html`, `validating`, `retrying`, `summarizing`, `completed`.\n"
            "Constraints:\n"
            "- Each label must be 2 to 5 words (or 4 to 12 Chinese characters), present progressive / action-oriented, "
            "referencing the actual topic or analytical domain from the prompt.\n"
            "- Do NOT include trailing `...` or punctuation (the UI adds animated dots).\n"
            "- Never return generic single words like 'Planning', 'Data', 'Analysis', 'Vis', or 'Ready'."
        )
        user_content = (
            f"User research request:\n{user_prompt}\n\n"
            f"Planned artifact workflows:\n{task_briefs}"
        )
        try:
            res = await asyncio.wait_for(
                self.llm.complete_json(
                    system_prompt,
                    user_content,
                    max_tokens=1024,
                    effort_override="low",
                ),
                timeout=12.0,
            )
            raw_labels = res.get("labels") if isinstance(res.get("labels"), dict) else res
            if not isinstance(raw_labels, dict):
                return fallback
            out: dict[str, str] = {}
            for stage in _REQUIRED_STAGES:
                val = raw_labels.get(stage)
                if isinstance(val, str):
                    cleaned = re.sub(r"[.。…]+$", "", val.strip())
                    if cleaned:
                        out[stage] = cleaned
                        continue
                out[stage] = fallback[stage]
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("InteractionAgent stage label synthesis fallback: %s", exc)
            return fallback

    def _fallback_followup(
        self,
        user_prompt: str,
        produced_versions: list[ArtifactVersion],
    ) -> dict[str, Any]:
        titles = [v.title for v in produced_versions[:3]]
        anchor = titles[0] if titles else "current findings"
        if _contains_cjk(user_prompt):
            return {
                "question": f"基于当前的「{anchor}」分析，你想继续深入哪个方向？",
                "options": [
                    {
                        "label": "下钻核心细分领域与异常峰值",
                        "prompt": f"针对当前画布中的「{anchor}」，进一步拆解核心子领域/关键维度的时间变化与高影响力驱动因素，并生成交互式对比图表。",
                    },
                    {
                        "label": "探索实体共现与协作/结构网络",
                        "prompt": f"围绕「{anchor}」补充实体关联或结构网络视角，突出关键枢纽节点与高权重聚类簇，支持悬停高亮与筛选。",
                    },
                    {
                        "label": "横向对比增长率与引用/影响力分布",
                        "prompt": f"对「{anchor}」构建增长率与影响力分布的交互式多视图对比分析，支持缩放、排序与指标切换。",
                    },
                ],
            }
        return {
            "question": f"Which analytical direction would you like to explore next from {anchor}?",
            "options": [
                {
                    "label": "Drill into top sub-segments & breakout drivers",
                    "prompt": f"Building on {anchor}, break down top sub-segments and key drivers with interactive filtering and linked detail tooltips.",
                },
                {
                    "label": "Inspect structural relationships & top hubs",
                    "prompt": f"Extend {anchor} with a structural relationship or network clustering view highlighting key hubs and bridge connections.",
                },
                {
                    "label": "Compare normalized growth rates & impact outliers",
                    "prompt": f"Create a comparative impact and growth-rate breakdown complementing {anchor}, with metric toggles and range selection.",
                },
            ],
        }

    def _fallback_narrative(
        self,
        user_prompt: str,
        produced_versions: list[ArtifactVersion],
        failures: list[str] | None = None,
    ) -> str:
        is_cjk = _contains_cjk(user_prompt)
        lines: list[str] = []
        for v in produced_versions:
            detail = (v.summary or "").strip() or (
                "已通过校验并渲染至右侧画布，可悬停或交互探索。"
                if is_cjk
                else "Interactive visualization rendered and verified on the canvas."
            )
            lines.append(f"- **{v.title}** (`{v.chart_kind}`): {detail}")
        if failures:
            err_lines = "\n".join(f"- {f}" for f in failures)
            lines.append("")
            lines.append(
                ("**部分任务未能完成：**\n" if is_cjk else "**Partial execution notes:**\n")
                + err_lines
            )
        return "\n".join(lines)

    def _parse_followup_payload(
        self,
        res: Any,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(res, dict):
            return fallback
        question = res.get("question")
        raw_opts = res.get("options")
        if not isinstance(question, str) or not question.strip() or not isinstance(raw_opts, list):
            return fallback
        cleaned_opts: list[dict[str, str]] = []
        for item in raw_opts:
            if not isinstance(item, dict):
                continue
            lbl = str(item.get("label") or "").strip()
            prm = str(item.get("prompt") or "").strip()
            if lbl and prm:
                cleaned_opts.append({"label": lbl, "prompt": prm})
        if len(cleaned_opts) < 2:
            return fallback
        return {
            "question": question.strip(),
            "options": cleaned_opts[:3],
        }

    async def synthesize_completion(
        self,
        user_prompt: str,
        produced_versions: list[ArtifactVersion],
        failures: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        fallback_followup = self._fallback_followup(user_prompt, produced_versions)
        fallback_text = self._fallback_narrative(user_prompt, produced_versions, failures)
        if not produced_versions:
            return fallback_text, fallback_followup

        artifact_summaries = [
            {
                "title": v.title,
                "chart_kind": v.chart_kind,
                "summary": v.summary or "",
            }
            for v in produced_versions
        ]
        lang_rule = (
            "Write `narrative`, `question`, and all option `label` and `prompt` fields in Simplified Chinese."
            if _contains_cjk(user_prompt)
            else "Write `narrative`, `question`, and all option `label` and `prompt` fields in concise English."
        )
        system_prompt = (
            "You are InteractionAgent wrapping up an analytical visualization turn on Libra Research Canvas.\n"
            f"{lang_rule}\n"
            "Given the user's request, the generated artifacts, and any partial errors, write:\n"
            "1. `narrative`: Natural, insightful Markdown explaining what each visualization shows, key patterns or metrics computed, and interactive capabilities. The UI already shows a green checkmark + `Success` header directly above your text, so NEVER start `narrative` with `Success`, checkmarks, or boilerplate like `Completed N validated interactive visualization artifact(s)`.\n"
            "2. `question` and 3 concrete `options` for a pre-selected follow-up radio menu.\n"
            "Return strictly valid JSON:\n"
            "{\n"
            '  "narrative": "1 short framing sentence followed by concise bullet highlights for each chart",\n'
            '  "question": "Concise 1-sentence question asking what angle to explore next",\n'
            '  "options": [\n'
            '    {"label": "Short scannable option title (5-12 words)", "prompt": "Self-contained actionable prompt referencing current workspace findings to create 1-2 new interactive charts"},\n'
            '    {"label": "...", "prompt": "..."},\n'
            '    {"label": "...", "prompt": "..."}\n'
            "  ]\n"
            "}\n"
            "Make option 0 the most natural high-signal next step."
        )
        user_content = (
            f"User request:\n{user_prompt}\n\n"
            f"Validated artifacts on canvas:\n{artifact_summaries}\n\n"
            f"Partial failures (if any):\n{failures or []}"
        )
        try:
            res = await asyncio.wait_for(
                self.llm.complete_json(
                    system_prompt,
                    user_content,
                    max_tokens=2048,
                    effort_override="low",
                ),
                timeout=16.0,
            )
            raw_narrative = str(res.get("narrative") or "").strip() if isinstance(res, dict) else ""
            cleaned_narrative = re.sub(
                r"^(?:[✓✅]\s*)?(?:Success[!:.：\s]*\n+|Completed\s+\*{0,2}\d+\*{0,2}\s+[^\n]*artifact\(s\)[^\n]*:\s*\n+)",
                "",
                raw_narrative,
                flags=re.IGNORECASE,
            ).strip()
            narrative = cleaned_narrative or fallback_text
            follow_up = self._parse_followup_payload(res, fallback_followup)
            return narrative, follow_up
        except Exception as exc:  # noqa: BLE001
            logger.warning("InteractionAgent completion synthesis fallback: %s", exc)
            return fallback_text, fallback_followup

    async def synthesize_followup(
        self,
        user_prompt: str,
        produced_versions: list[ArtifactVersion],
        summary_text: str = "",
    ) -> dict[str, Any]:
        fallback = self._fallback_followup(user_prompt, produced_versions)
        if not produced_versions:
            return fallback

        artifact_summaries = [
            {
                "title": v.title,
                "chart_kind": v.chart_kind,
                "summary": v.summary or "",
            }
            for v in produced_versions
        ]
        lang_rule = (
            "Write `question` and all option `label` and `prompt` fields in Simplified Chinese."
            if _contains_cjk(user_prompt)
            else "Write `question` and all option `label` and `prompt` fields in concise English."
        )
        system_prompt = (
            "You are InteractionAgent guiding multi-turn exploratory visual analytics on an infinite Research Canvas.\n"
            f"{lang_rule}\n"
            "Given the user's request and the validated artifacts just added to the Canvas, generate a brief interactive "
            "next-step question and 3 distinct, concrete follow-up exploration options for a pre-selected radio menu.\n"
            "Return strictly valid JSON:\n"
            "{\n"
            '  "question": "Concise 1-sentence question asking what angle to explore next",\n'
            '  "options": [\n'
            '    {"label": "Short scannable option title (5-12 words)", "prompt": "Self-contained actionable prompt referencing current workspace findings to create 1-2 new interactive charts"},\n'
            '    {"label": "...", "prompt": "..."},\n'
            '    {"label": "...", "prompt": "..."}\n'
            "  ]\n"
            "}\n"
            "Make option 0 the most natural high-signal next step (it will be pre-selected by default)."
        )
        user_content = (
            f"User request:\n{user_prompt}\n\n"
            f"Validated artifacts on canvas:\n{artifact_summaries}\n\n"
            f"Assistant summary:\n{summary_text}"
        )
        try:
            res = await asyncio.wait_for(
                self.llm.complete_json(
                    system_prompt,
                    user_content,
                    max_tokens=1536,
                    effort_override="low",
                ),
                timeout=14.0,
            )
            return self._parse_followup_payload(res, fallback)
        except Exception as exc:  # noqa: BLE001
            logger.warning("InteractionAgent follow-up synthesis fallback: %s", exc)
            return fallback
