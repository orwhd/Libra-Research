from __future__ import annotations

import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..agents.llm_client import LLMClient
from ..config import OPENALEX_API_KEY, OPENALEX_EMAIL, RUNTIME_DIR

TRENDING_CACHE_FILE = RUNTIME_DIR / "weekly_trending_charts.json"
CACHE_TTL = timedelta(days=7)
_REFRESH_LOCK = asyncio.Lock()

VALID_ICON_KINDS: tuple[str, ...] = (
    "trend",
    "network",
    "bundling",
    "heatmap",
    "flow",
    "hierarchy",
    "scatter",
    "radial",
)

_ALLOWED_SVG_TAGS: set[str] = {
    "svg",
    "g",
    "path",
    "circle",
    "rect",
    "line",
    "polyline",
    "polygon",
    "ellipse",
    "text",
    "tspan",
    "defs",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
}

_ALLOWED_SVG_ATTRS: set[str] = {
    "viewBox",
    "fill",
    "fill-opacity",
    "stroke",
    "stroke-width",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-opacity",
    "opacity",
    "d",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "width",
    "height",
    "points",
    "transform",
    "class",
    "id",
    "offset",
    "stop-color",
    "stop-opacity",
    "text-anchor",
    "font-size",
    "font-weight",
    "letter-spacing",
    "clip-path",
}

_ALLOWED_SVG_CLASSES: set[str] = {
    "chart-preview-svg",
    "preview-anim-pulse",
    "preview-anim-flow",
    "preview-anim-phase-a",
    "preview-anim-phase-b",
}

_PRIMITIVE_TAGS: set[str] = {
    "path",
    "circle",
    "rect",
    "line",
    "polyline",
    "polygon",
    "ellipse",
}

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_REF_RE = re.compile(r"^url\(#([A-Za-z0-9_-]+)\)$")
_UNSAFE_VAL_RE = re.compile(r"(javascript:|data:|https?:|expression\()", re.IGNORECASE)


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _prefix_svg_id(raw_id: str, id_prefix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw_id)
    if not cleaned:
        return ""
    if cleaned.startswith(f"{id_prefix}_"):
        return cleaned
    return f"{id_prefix}_{cleaned}"


def sanitize_schematic_svg(raw_svg: str, id_prefix: str) -> str:
    """Strictly sanitize an SVG string with element/attribute allowlists and ID prefixing."""
    if not isinstance(raw_svg, str):
        return ""
    text = raw_svg.strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```(?:svg|xml)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    start_idx = text.find("<svg")
    end_idx = text.rfind("</svg>")
    if start_idx == -1 or end_idx == -1:
        return ""
    text = text[start_idx : end_idx + len("</svg>")]

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""

    if _local_tag(root.tag) != "svg":
        return ""

    primitive_count = 0

    def _clone_clean(src: ET.Element, is_root: bool = False) -> ET.Element | None:
        nonlocal primitive_count
        tag = _local_tag(src.tag)
        if tag not in _ALLOWED_SVG_TAGS:
            return None

        if tag in _PRIMITIVE_TAGS:
            primitive_count += 1

        clean_attrs: dict[str, str] = {}
        for attr_name, attr_val in src.attrib.items():
            local_attr = _local_tag(attr_name)
            if local_attr not in _ALLOWED_SVG_ATTRS:
                continue
            val = attr_val.strip()
            if not val or _UNSAFE_VAL_RE.search(val):
                continue

            if local_attr == "class":
                kept = [tok for tok in val.split() if tok in _ALLOWED_SVG_CLASSES]
                if kept:
                    clean_attrs["class"] = " ".join(kept)
                continue

            if local_attr == "id":
                prefixed = _prefix_svg_id(val, id_prefix)
                if prefixed:
                    clean_attrs["id"] = prefixed
                continue

            if local_attr in ("fill", "stroke", "clip-path"):
                m_url = _URL_REF_RE.match(val)
                if "url(" in val.lower():
                    if not m_url:
                        continue
                    ref_id = m_url.group(1)
                    if not _SAFE_ID_RE.match(ref_id):
                        continue
                    prefixed_ref = _prefix_svg_id(ref_id, id_prefix)
                    clean_attrs[local_attr] = f"url(#{prefixed_ref})"
                    continue

            clean_attrs[local_attr] = val

        if is_root:
            clean_attrs["viewBox"] = "0 0 280 124"
            clean_attrs["fill"] = "none"
            clean_attrs["class"] = "chart-preview-svg"

        dst = ET.Element(tag, clean_attrs)
        if src.text and tag in ("text", "tspan"):
            dst.text = src.text[:80]

        for child in list(src):
            clean_child = _clone_clean(child, is_root=False)
            if clean_child is not None:
                dst.append(clean_child)
                if child.tail and tag in ("text", "tspan"):
                    clean_child.tail = child.tail[:40]
        return dst

    clean_root = _clone_clean(root, is_root=True)
    if clean_root is None or primitive_count < 4:
        return ""

    return ET.tostring(clean_root, encoding="unicode")


def _iso_week_label(dt: datetime) -> str:
    iso = dt.astimezone(timezone.utc).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _parse_iso(ts: str) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        parsed = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_valid_item_dict(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    required = ("id", "title", "icon_kind", "summary", "prompt", "source_label")
    for k in required:
        if not isinstance(item.get(k), str) or not item[k].strip():
            return False
    if item.get("icon_kind") not in VALID_ICON_KINDS:
        return False
    if not isinstance(item.get("interactions"), list) or len(item["interactions"]) == 0:
        return False
    return True


def is_cache_fresh(data: dict[str, Any], now: datetime | None = None) -> bool:
    if not isinstance(data, dict):
        return False
    items = data.get("items")
    if not isinstance(items, list) or len(items) != 4:
        return False
    if not all(_is_valid_item_dict(it) for it in items):
        return False
    fetched_dt = _parse_iso(str(data.get("fetched_at", "")))
    if fetched_dt is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current - fetched_dt
    return timedelta(seconds=-60) <= age < CACHE_TTL


def _read_cache_file() -> dict[str, Any] | None:
    if not TRENDING_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(TRENDING_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        return None
    return None


def has_fresh_weekly_cache() -> bool:
    data = _read_cache_file()
    return data is not None and is_cache_fresh(data)


async def fetch_live_trending_signals() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff_60d = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    cutoff_365d = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    gh_url = "https://api.github.com/search/repositories"
    gh_params = {
        "q": f"interactive data visualization chart pushed:>={cutoff_60d}",
        "sort": "stars",
        "order": "desc",
        "per_page": "8",
    }

    oa_url = "https://api.openalex.org/works"
    oa_params: dict[str, str] = {
        "search": "interactive data visualization visual analytics",
        "filter": f"from_publication_date:{cutoff_365d}",
        "sort": "cited_by_count:desc",
        "per-page": "8",
    }
    if OPENALEX_API_KEY:
        oa_params["api_key"] = OPENALEX_API_KEY
    if OPENALEX_EMAIL:
        oa_params["mailto"] = OPENALEX_EMAIL

    github_items: list[dict[str, Any]] = []
    openalex_items: list[dict[str, Any]] = []
    sources: list[str] = []

    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "LibraResearch/1.0"},
    ) as client:
        gh_res, oa_res = await asyncio.gather(
            client.get(
                gh_url,
                params=gh_params,
                headers={"Accept": "application/vnd.github+json"},
            ),
            client.get(oa_url, params=oa_params),
            return_exceptions=True,
        )

    if isinstance(gh_res, httpx.Response) and gh_res.status_code == 200:
        try:
            payload = gh_res.json()
            for repo in payload.get("items", [])[:8]:
                if not isinstance(repo, dict):
                    continue
                full_name = str(repo.get("full_name") or repo.get("name") or "").strip()
                if not full_name:
                    continue
                github_items.append(
                    {
                        "name": full_name,
                        "description": str(repo.get("description") or "")[:220],
                        "stars": int(repo.get("stargazers_count") or 0),
                        "topics": list(repo.get("topics") or [])[:5],
                        "url": str(repo.get("html_url") or ""),
                        "pushed_at": str(repo.get("pushed_at") or "")[:10],
                    }
                )
            if github_items:
                sources.append("GitHub")
        except Exception:  # noqa: BLE001
            pass

    if isinstance(oa_res, httpx.Response) and oa_res.status_code == 200:
        try:
            payload = oa_res.json()
            for work in payload.get("results", [])[:8]:
                if not isinstance(work, dict):
                    continue
                title = str(work.get("display_name") or work.get("title") or "").strip()
                if not title:
                    continue
                concepts = [
                    str(c.get("display_name") or "")
                    for c in (work.get("concepts") or [])[:4]
                    if isinstance(c, dict) and c.get("display_name")
                ]
                openalex_items.append(
                    {
                        "title": title[:180],
                        "year": work.get("publication_year"),
                        "citations": int(work.get("cited_by_count") or 0),
                        "concepts": concepts,
                        "url": str(work.get("id") or ""),
                    }
                )
            if openalex_items:
                sources.append("OpenAlex")
        except Exception:  # noqa: BLE001
            pass

    return {
        "github": github_items,
        "openalex": openalex_items,
        "sources": sources,
    }


def _normalize_chart_items(
    raw_items: list[dict[str, Any]], iso_week: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used_icons: set[str] = set()
    used_ids: set[str] = set()
    slug_week = re.sub(r"[^a-z0-9]+", "_", iso_week.lower()).strip("_") or "wk"

    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:48]
        summary = str(raw.get("summary") or "").strip()[:180]
        prompt = str(raw.get("prompt") or "").strip()[:420]
        source_label = str(raw.get("source_label") or "").strip()[:60]
        source_url = str(raw.get("source_url") or "").strip()[:300]
        if source_url and not source_url.startswith(("https://", "http://")):
            source_url = ""

        icon_kind = str(raw.get("icon_kind") or "").strip().lower()
        if icon_kind not in VALID_ICON_KINDS or icon_kind in used_icons:
            icon_kind = next(
                (k for k in VALID_ICON_KINDS if k not in used_icons),
                VALID_ICON_KINDS[idx % len(VALID_ICON_KINDS)],
            )
        used_icons.add(icon_kind)

        raw_interactions = raw.get("interactions")
        interactions: list[str] = []
        if isinstance(raw_interactions, list):
            for tag in raw_interactions:
                t = str(tag).strip()[:28]
                if t and t not in interactions:
                    interactions.append(t)
                if len(interactions) >= 3:
                    break
        if not interactions:
            interactions = ["Hover Inspect", "Cross-Filter", "Zoom & Pan"]

        if not title or not summary or not prompt or not source_label:
            continue

        base_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] or icon_kind
        item_id = f"{base_id}-{idx + 1}"
        if item_id in used_ids:
            item_id = f"chart-{idx + 1}"
        used_ids.add(item_id)

        prefix = f"wk_{slug_week}_{idx + 1}"
        clean_svg = sanitize_schematic_svg(str(raw.get("schematic_svg") or ""), prefix)

        normalized.append(
            {
                "id": item_id,
                "title": title,
                "icon_kind": icon_kind,
                "summary": summary,
                "interactions": interactions,
                "prompt": prompt,
                "source_label": source_label,
                "source_url": source_url,
                "schematic_svg": clean_svg,
            }
        )
        if len(normalized) == 4:
            break

    return normalized


async def _synthesize_charts_with_llm(
    signals: dict[str, Any], iso_week: str
) -> list[dict[str, Any]] | None:
    try:
        llm = LLMClient()
    except Exception:  # noqa: BLE001
        return None

    system_prompt = (
        "You are a senior data visualization curator for Libra Research.\n"
        "Given live weekly trending repositories from GitHub and trending interactive visualization papers from OpenAlex, "
        "curate EXACTLY 4 distinct currently popular interactive visualization chart views.\n"
        "Return strict JSON matching:\n"
        '{"items": [\n'
        "  {\n"
        '    "title": "Short 2-4 word English chart title",\n'
        '    "icon_kind": "one of: trend | network | bundling | heatmap | flow | hierarchy | scatter | radial",\n'
        '    "summary": "One crisp sentence explaining what this chart reveals and its key interaction.",\n'
        '    "interactions": ["Tag 1", "Tag 2", "Tag 3"],\n'
        '    "prompt": "Actionable research prompt telling Libra Research to build this interactive visualization from the user\'s attached dataset or URL.",\n'
        '    "source_label": "Real signal attribution, e.g. GitHub · repo-name ★42k or OpenAlex · Paper Topic",\n'
        '    "source_url": "Exact https URL from the provided signals",\n'
        '    "schematic_svg": "<svg viewBox=\\"0 0 280 124\\" fill=\\"none\\">...</svg>"\n'
        "  }\n"
        "]}\n\n"
        "Requirements:\n"
        "- All 4 items MUST use 4 DIFFERENT icon_kind values.\n"
        "- Anchor each item in the actual GitHub repos or OpenAlex works provided (do not invent URLs).\n"
        "- Keep title <= 30 characters so it fits cleanly on a pill card.\n"
        "- For schematic_svg: draw a crisp, light-mode vector schematic (viewBox 0 0 280 124, transparent background) "
        "using palette #2563eb, #0ea5e9, #6366f1, #0f172a, #cbd5e1, #e2e8f0. Include subtle grid/axes plus >= 5 vector primitives "
        "and a dark mini tooltip pill (#0f172a fill with #ffffff 8px text). You may apply classes preview-anim-pulse, "
        "preview-anim-flow, preview-anim-phase-a, or preview-anim-phase-b to highlight interactive elements. "
        "Never include <script>, <style>, event attributes, or external URLs."
    )

    user_payload = json.dumps(
        {
            "iso_week": iso_week,
            "github_trending_repos": signals.get("github", []),
            "openalex_top_works": signals.get("openalex", []),
        },
        ensure_ascii=False,
    )

    try:
        result = await llm.complete_json(system_prompt, user_payload, max_tokens=4096)
    except Exception:  # noqa: BLE001
        return None

    raw_items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(raw_items, list):
        return None
    normalized = _normalize_chart_items(raw_items, iso_week)
    return normalized if len(normalized) == 4 else None


def _build_signal_fallback_items(
    signals: dict[str, Any], iso_week: str
) -> list[dict[str, Any]]:
    gh_list = signals.get("github") or []
    oa_list = signals.get("openalex") or []

    def _pick_source(idx: int, default_label: str) -> tuple[str, str]:
        combined: list[tuple[str, str]] = []
        for r in gh_list:
            stars = r.get("stars", 0)
            star_str = f" ★{stars // 1000}k" if stars >= 1000 else f" ★{stars}"
            combined.append((f"GitHub · {r['name']}{star_str}", str(r.get("url") or "")))
        for w in oa_list:
            cites = w.get("citations", 0)
            short_title = str(w["title"])[:32].rstrip()
            combined.append((f"OpenAlex · {short_title} ({cites} cites)", str(w.get("url") or "")))
        if idx < len(combined):
            return combined[idx]
        if combined:
            return combined[idx % len(combined)]
        return default_label, ""

    templates = [
        {
            "title": "Streaming Trend Explorer",
            "icon_kind": "trend",
            "summary": "Interactive multi-series trajectory with hover crosshair inspection and brush range zoom.",
            "interactions": ["Metric Switch", "Hover Crosshair", "Brush Zoom"],
            "prompt": "Build a multi-series interactive trend and growth explorer from the attached data with metric switching, hover crosshair readouts, and range brushing.",
        },
        {
            "title": "Flow & Ribbon Sankey",
            "icon_kind": "flow",
            "summary": "Weighted stage-to-stage flow diagram highlighting dominant pathways on ribbon hover.",
            "interactions": ["Path Highlight", "Stage Filter", "Flow Tooltip"],
            "prompt": "Create an interactive Sankey flow diagram revealing stage-by-stage transitions and proportional shares from the data with path highlighting.",
        },
        {
            "title": "Force Cluster Network",
            "icon_kind": "network",
            "summary": "Interactive force-directed community graph with ego-neighborhood focus and tie filtering.",
            "interactions": ["Ego-Node Hover", "Drag & Zoom", "Tie Threshold"],
            "prompt": "Construct a force-directed relationship network highlighting community clusters, hub nodes, and interactive tie-weight filtering.",
        },
        {
            "title": "Sorted Matrix Heatmap",
            "icon_kind": "heatmap",
            "summary": "Sortable category-by-cohort matrix paired with animated cell inspection and row totals.",
            "interactions": ["Sort Matrix", "Cell Inspector", "Marginal Bars"],
            "prompt": "Design an interactive category-by-dimension heatmap matrix with row/column sorting, cell hover inspection, and marginal distribution bars.",
        },
    ]

    raw_items: list[dict[str, Any]] = []
    for idx, tpl in enumerate(templates):
        src_label, src_url = _pick_source(idx, f"Weekly Research Digest ({iso_week})")
        raw_items.append(
            {
                **tpl,
                "source_label": src_label,
                "source_url": src_url,
                "schematic_svg": "",
            }
        )
    return _normalize_chart_items(raw_items, iso_week)


def _persist_cache_atomic(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TRENDING_CACHE_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, TRENDING_CACHE_FILE)


async def get_weekly_trending_charts(force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        existing = _read_cache_file()
        if existing is not None and is_cache_fresh(existing):
            return {**existing, "cached": True}

    async with _REFRESH_LOCK:
        if not force_refresh:
            existing = _read_cache_file()
            if existing is not None and is_cache_fresh(existing):
                return {**existing, "cached": True}

        now = datetime.now(timezone.utc)
        iso_week = _iso_week_label(now)
        signals = await fetch_live_trending_signals()
        has_live_signals = bool(signals.get("github") or signals.get("openalex"))

        if not has_live_signals:
            stale = _read_cache_file()
            if (
                stale is not None
                and isinstance(stale.get("items"), list)
                and len(stale["items"]) == 4
                and all(_is_valid_item_dict(it) for it in stale["items"])
            ):
                return {**stale, "cached": True}

            fallback_items = _build_signal_fallback_items(signals, iso_week)
            return {
                "iso_week": iso_week,
                "fetched_at": now.isoformat(),
                "expires_at": (now + CACHE_TTL).isoformat(),
                "cached": False,
                "sources": ["Offline Fallback"],
                "items": fallback_items,
            }

        items = await _synthesize_charts_with_llm(signals, iso_week)
        if not items or len(items) != 4:
            items = _build_signal_fallback_items(signals, iso_week)

        payload = {
            "iso_week": iso_week,
            "fetched_at": now.isoformat(),
            "expires_at": (now + CACHE_TTL).isoformat(),
            "sources": signals.get("sources") or ["GitHub", "OpenAlex"],
            "items": items,
        }
        _persist_cache_atomic(payload)
        return {**payload, "cached": False}
