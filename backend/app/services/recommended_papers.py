from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import OPENALEX_API_KEY, OPENALEX_EMAIL, RUNTIME_DIR
from ..db import get_conn

RECOMMENDED_PAPERS_CACHE_FILE = RUNTIME_DIR / "recommended_papers_cache.json"
CACHE_TTL = timedelta(hours=12)
_REFRESH_LOCK = asyncio.Lock()

PAPER_ICON_KINDS: tuple[str, ...] = ("paper", "code", "experiment", "model")

# Bilingual topic signals mapped to (short_tag, openalex_search_query)
_BILINGUAL_TOPIC_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("hci", "人机交互", "交互设计", "用户界面", "ui", "ux", "human-computer", "interactive system"),
        "HCI & Interactive Systems",
        "human computer interaction interactive AI systems",
    ),
    (
        ("可视化", "visual analytics", "data visualization", "infovis", "vis "),
        "Visual Analytics",
        "interactive visual analytics data visualization",
    ),
    (
        ("agent", "agents", "智能体", "多智能体", "multi-agent", "tool use"),
        "LLM Agents",
        "large language model autonomous agents",
    ),
    (
        ("llm", "large language model", "大模型", "语言模型", "reasoning", "prompt", "对齐", "rlhf"),
        "Foundation & Reasoning Models",
        "large language models reasoning alignment",
    ),
    (
        ("rag", "检索增强", "retrieval", "knowledge graph", "知识图谱"),
        "RAG & Knowledge Systems",
        "retrieval augmented generation knowledge graph",
    ),
    (
        ("diffusion", "扩散", "图像生成", "computer vision", "计算机视觉", "3d", "gaussian", "video", "视频"),
        "Vision & Generative Media",
        "diffusion models visual generation computer vision",
    ),
    (
        ("robot", "robotics", "机器人", "embodied", "具身", "manipulation"),
        "Embodied AI & Robotics",
        "embodied AI robot learning manipulation",
    ),
    (
        ("graph", "gnn", "图神经", "network science", "community"),
        "Graph Learning",
        "graph neural networks representation learning",
    ),
    (
        ("security", "安全", "privacy", "隐私", "adversarial", "watermark"),
        "AI Safety & Security",
        "machine learning security alignment robustness",
    ),
    (
        ("medical", "clinical", "医疗", "protein", "bio", "drug", "genomic"),
        "AI for Science & Medicine",
        "foundation models biomedical discovery",
    ),
    (
        ("ai", "人工智能", "machine learning", "机器学习", "深度学习", "deep learning", "neural"),
        "AI & Machine Learning",
        "deep learning multimodal foundation models",
    ),
)

_BOILERPLATE_STOPWORDS: set[str] = {
    "show",
    "build",
    "create",
    "make",
    "design",
    "plot",
    "draw",
    "visualize",
    "visualization",
    "interactive",
    "chart",
    "charts",
    "graph",
    "trend",
    "trends",
    "yearly",
    "annual",
    "monthly",
    "weekly",
    "volume",
    "count",
    "counts",
    "publication",
    "publications",
    "paper",
    "papers",
    "reproduce",
    "reproduction",
    "replicate",
    "dataset",
    "data",
    "using",
    "real",
    "research",
    "workspace",
    "new",
    "from",
    "with",
    "into",
    "over",
    "between",
    "across",
    "about",
    "latest",
    "recent",
    "hot",
    "topic",
    "topics",
    "fall",
    "spring",
    "summer",
    "winter",
    "table",
    "heatmap",
    "sankey",
    "explorer",
    "matrix",
    "sorted",
    "streaming",
    "flow",
    "ribbon",
    "force",
    "cluster",
    "the",
    "and",
    "for",
    "any",
    "ask",
    "put",
    "questions",
    "help",
    "please",
}

_EXCLUDED_WORK_PATTERNS: tuple[str, ...] = (
    "a survey of",
    "survey on",
    "comprehensive survey",
    "systematic review",
    "literature review",
    "bibliometric",
    "scoping review",
    "meta-analysis",
    "corrigendum",
    "erratum",
    "editorial:",
    "retracted",
)

_FALLBACK_PAPERS: tuple[dict[str, Any], ...] = (
    {
        "title": "Generative Agents: Interactive Simulacra",
        "full_title": "Generative Agents: Interactive Simulacra of Human Behavior",
        "icon_kind": "paper",
        "year": 2023,
        "venue": "UIST",
        "citations": 2400,
        "topic_tag": "HCI & Agents",
        "source_url": "https://arxiv.org/abs/2304.03442",
    },
    {
        "title": "Direct Preference Optimization (DPO)",
        "full_title": "Direct Preference Optimization: Your Language Model is Secretly a Reward Model",
        "icon_kind": "model",
        "year": 2023,
        "venue": "NeurIPS",
        "citations": 3800,
        "topic_tag": "Foundation Models",
        "source_url": "https://arxiv.org/abs/2305.18290",
    },
    {
        "title": "SWE-agent: Agent-Computer Interfaces",
        "full_title": "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        "icon_kind": "code",
        "year": 2024,
        "venue": "NeurIPS",
        "citations": 620,
        "topic_tag": "Interactive Agents",
        "source_url": "https://arxiv.org/abs/2405.15793",
    },
    {
        "title": "Data Formulator: AI-Powered Visualization",
        "full_title": "Data Formulator: AI-Powered Concept-Driven Visualization Authoring",
        "icon_kind": "experiment",
        "year": 2024,
        "venue": "IEEE TVCG",
        "citations": 145,
        "topic_tag": "HCI & Visual Analytics",
        "source_url": "https://arxiv.org/abs/2309.10099",
    },
)


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


def collect_all_workspace_signals() -> dict[str, Any]:
    """
    Read ALL existing workspaces across BOTH `data` and `reproduce` modes
    so paper recommendations reflect the user's complete research history.
    """
    with get_conn() as conn:
        ws_rows = conn.execute(
            "SELECT workspace_id, title, workspace_type, updated_at "
            "FROM workspaces ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()
        run_rows = conn.execute(
            "SELECT workspace_id, prompt FROM task_runs ORDER BY created_at DESC LIMIT 30"
        ).fetchall()

    titles: list[str] = []
    for r in ws_rows:
        t = str(r["title"] or "").strip()
        if t and t.lower() not in ("new research workspace", "research workspace"):
            titles.append(t)

    prompts: list[str] = []
    for r in run_rows:
        p = str(r["prompt"] or "").strip()
        if p:
            prompts.append(p[:180])

    return {
        "workspace_count": len(ws_rows),
        "titles": titles,
        "prompts": prompts,
    }


def extract_research_topics(signals: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Return up to 4 `(topic_tag, openalex_search_query)` pairs inferred from
    all existing workspaces (both Data and Reproduce).
    """
    corpus_lines = [*signals.get("titles", []), *signals.get("prompts", [])]
    matched: list[tuple[str, str]] = []
    seen_queries: set[str] = set()

    combined_lower = " \n ".join(corpus_lines).lower()

    for keywords, tag, query in _BILINGUAL_TOPIC_RULES:
        for kw in keywords:
            if kw in (
                "ai",
                "ui",
                "ux",
                "hci",
                "cv",
                "nlp",
                "rag",
                "llm",
                "gnn",
                "3d",
            ):
                hit = bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", combined_lower))
            else:
                hit = kw in combined_lower
            if hit and query not in seen_queries:
                matched.append((tag, query))
                seen_queries.add(query)
                break
        if len(matched) >= 3:
            break

    # Also extract custom English domain phrases from workspace titles (e.g. "Phone Market", "Gaussian Splatting")
    for title in signals.get("titles", [])[:8]:
        words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", title)
        meaningful = [
            w for w in words if w.lower() not in _BOILERPLATE_STOPWORDS and not w.isdigit()
        ]
        if len(meaningful) >= 2:
            phrase = " ".join(meaningful[:4])
            norm_q = phrase.lower()
            if norm_q not in seen_queries and not any(norm_q in q for q in seen_queries):
                matched.append((phrase[:28], phrase))
                seen_queries.add(norm_q)
        if len(matched) >= 4:
            break

    default_topics: list[tuple[str, str]] = [
        ("HCI & Interactive AI", "human computer interaction interactive AI systems"),
        ("LLM Agents", "large language model autonomous agents"),
        ("Multimodal Models", "multimodal foundation models visual reasoning"),
        ("Visual Analytics", "interactive visual analytics human AI collaboration"),
    ]
    for tag, q in default_topics:
        if len(matched) >= 4:
            break
        if q not in seen_queries:
            matched.append((tag, q))
            seen_queries.add(q)

    return matched[:4]


def _compute_fingerprint(signals: dict[str, Any], iso_week: str) -> str:
    raw = json.dumps(
        {
            "iso_week": iso_week,
            "workspace_count": signals.get("workspace_count", 0),
            "titles": signals.get("titles", [])[:15],
            "prompts": signals.get("prompts", [])[:10],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _shorten_paper_title(full_title: str, max_len: int = 44) -> str:
    cleaned = re.sub(r"\s+", " ", full_title).strip()
    if len(cleaned) <= max_len:
        return cleaned
    if ":" in cleaned:
        head = cleaned.split(":", 1)[0].strip()
        if 12 <= len(head) <= max_len:
            return head
    return cleaned[: max_len - 1].rstrip(" ,;:-") + "…"


def _build_reproduce_prompt(full_title: str, year: int | None, source_url: str) -> str:
    yr_part = f" ({year})" if year else ""
    url_part = f" [{source_url}]" if source_url else ""
    return (
        f'Reproduce the core method, experiments, and key evaluation results of "{full_title}"'
        f"{yr_part}{url_part}."
    )


def _is_noisy_work(title: str) -> bool:
    low = title.lower().strip()
    if len(low) < 12:
        return True
    return any(pat in low for pat in _EXCLUDED_WORK_PATTERNS)


async def _fetch_openalex_papers_for_topic(
    client: httpx.AsyncClient,
    topic_tag: str,
    search_query: str,
    cutoff_date: str,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "search": search_query,
        "filter": f"from_publication_date:{cutoff_date}",
        "sort": "cited_by_count:desc",
        "per-page": "10",
    }
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL

    try:
        resp = await client.get("https://api.openalex.org/works", params=params)
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    for work in payload.get("results") or []:
        if not isinstance(work, dict) or work.get("is_retracted"):
            continue
        full_title = str(work.get("display_name") or work.get("title") or "").strip()
        if not full_title or _is_noisy_work(full_title):
            continue
        year_raw = work.get("publication_year")
        year = int(year_raw) if isinstance(year_raw, int) else None
        citations = int(work.get("cited_by_count") or 0)
        primary_loc = work.get("primary_location") or {}
        src = primary_loc.get("source") or {}
        venue = str(src.get("display_name") or "").strip()[:48]
        doi = str(work.get("doi") or "").strip()
        oa_url = str((work.get("open_access") or {}).get("oa_url") or "").strip()
        work_id = str(work.get("id") or "").strip()
        source_url = oa_url or doi or work_id
        if source_url and not source_url.startswith(("http://", "https://")):
            source_url = ""

        out.append(
            {
                "full_title": full_title[:200],
                "year": year,
                "venue": venue or "OpenAlex",
                "citations": citations,
                "topic_tag": topic_tag,
                "source_url": source_url,
            }
        )
    return out


def _read_cache_store() -> dict[str, Any]:
    if not RECOMMENDED_PAPERS_CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(RECOMMENDED_PAPERS_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:  # noqa: BLE001
        pass
    return {}


def _write_cache_store(store: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    # Keep at most 12 recent workspace fingerprints
    entries = list(store.items())[-12:]
    trimmed = dict(entries)
    tmp_path = RECOMMENDED_PAPERS_CACHE_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, RECOMMENDED_PAPERS_CACHE_FILE)


def _is_entry_fresh(entry: dict[str, Any], now: datetime) -> bool:
    if not isinstance(entry, dict):
        return False
    items = entry.get("items")
    if not isinstance(items, list) or len(items) != 4:
        return False
    fetched_dt = _parse_iso(str(entry.get("fetched_at") or ""))
    if fetched_dt is None:
        return False
    age = now - fetched_dt
    return timedelta(seconds=-60) <= age < CACHE_TTL


def _build_fallback_items(topics: list[tuple[str, str]]) -> list[dict[str, Any]]:
    primary_tag = topics[0][0] if topics else "AI & HCI"
    items: list[dict[str, Any]] = []
    for idx, fb in enumerate(_FALLBACK_PAPERS):
        icon_kind = PAPER_ICON_KINDS[idx % len(PAPER_ICON_KINDS)]
        items.append(
            {
                "id": f"rec-paper-{idx + 1}",
                "title": fb["title"],
                "full_title": fb["full_title"],
                "icon_kind": icon_kind,
                "year": fb["year"],
                "venue": fb["venue"],
                "citations": fb["citations"],
                "topic_tag": primary_tag if idx == 0 else fb["topic_tag"],
                "prompt": _build_reproduce_prompt(fb["full_title"], fb["year"], fb["source_url"]),
                "source_url": fb["source_url"],
            }
        )
    return items


async def get_recommended_papers(force_refresh: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    iso_week = _iso_week_label(now)
    signals = collect_all_workspace_signals()
    fingerprint = _compute_fingerprint(signals, iso_week)

    if not force_refresh:
        store = _read_cache_store()
        cached_entry = store.get(fingerprint)
        if isinstance(cached_entry, dict) and _is_entry_fresh(cached_entry, now):
            return {**cached_entry, "cached": True}

    async with _REFRESH_LOCK:
        if not force_refresh:
            store = _read_cache_store()
            cached_entry = store.get(fingerprint)
            if isinstance(cached_entry, dict) and _is_entry_fresh(cached_entry, now):
                return {**cached_entry, "cached": True}

        topics = extract_research_topics(signals)
        cutoff_date = (now - timedelta(days=620)).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(
            timeout=7.5,
            follow_redirects=True,
            headers={"User-Agent": "LibraResearch/1.0"},
        ) as client:
            topic_batches = await asyncio.gather(
                *[
                    _fetch_openalex_papers_for_topic(client, tag, query, cutoff_date)
                    for tag, query in topics[:4]
                ],
                return_exceptions=True,
            )

        buckets: list[list[dict[str, Any]]] = [
            b for b in topic_batches if isinstance(b, list) and len(b) > 0
        ]

        picked: list[dict[str, Any]] = []
        seen_titles: set[str] = set()

        # Round-robin across matched workspace topics for diversity
        round_idx = 0
        while len(picked) < 4 and buckets:
            progressed = False
            for bucket in buckets:
                if round_idx < len(bucket):
                    cand = bucket[round_idx]
                    norm_t = re.sub(r"[^a-z0-9]+", "", cand["full_title"].lower())
                    if norm_t and norm_t not in seen_titles:
                        seen_titles.add(norm_t)
                        picked.append(cand)
                        progressed = True
                        if len(picked) == 4:
                            break
            if not progressed:
                break
            round_idx += 1

        sources: list[str] = ["OpenAlex"] if picked else []
        if signals.get("workspace_count", 0) > 0:
            sources.append("Existing Workspaces")

        items: list[dict[str, Any]] = []
        for idx, cand in enumerate(picked[:4]):
            icon_kind = PAPER_ICON_KINDS[idx % len(PAPER_ICON_KINDS)]
            slug = re.sub(r"[^a-z0-9]+", "-", cand["full_title"].lower()).strip("-")[:24] or "paper"
            items.append(
                {
                    "id": f"{slug}-{idx + 1}",
                    "title": _shorten_paper_title(cand["full_title"]),
                    "full_title": cand["full_title"],
                    "icon_kind": icon_kind,
                    "year": cand["year"],
                    "venue": cand["venue"],
                    "citations": cand["citations"],
                    "topic_tag": cand["topic_tag"],
                    "prompt": _build_reproduce_prompt(
                        cand["full_title"], cand["year"], cand["source_url"]
                    ),
                    "source_url": cand["source_url"],
                }
            )

        if len(items) < 4:
            for fb in _build_fallback_items(topics):
                norm_fb = re.sub(r"[^a-z0-9]+", "", fb["full_title"].lower())
                if norm_fb not in seen_titles:
                    seen_titles.add(norm_fb)
                    fb_copy = {
                        **fb,
                        "id": f"rec-paper-{len(items) + 1}",
                        "icon_kind": PAPER_ICON_KINDS[len(items) % len(PAPER_ICON_KINDS)],
                    }
                    items.append(fb_copy)
                if len(items) == 4:
                    break
            if not sources:
                sources = ["Curated Reproducible Benchmarks"]

        entry = {
            "fingerprint": fingerprint,
            "fetched_at": now.isoformat(),
            "expires_at": (now + CACHE_TTL).isoformat(),
            "workspace_count": int(signals.get("workspace_count", 0)),
            "topics": [t[0] for t in topics],
            "sources": sources,
            "items": items[:4],
        }
        if "OpenAlex" in sources:
            store = _read_cache_store()
            store[fingerprint] = entry
            _write_cache_store(store)

        return {**entry, "cached": False}
