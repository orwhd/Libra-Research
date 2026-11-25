from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import OPENALEX_EMAIL
from ..services.openalex_client import (
    DEFAULT_FILTER,
    OPENALEX_WORKS_URL,
    fetch_openalex_shard,
    load_seed_corpus,
)
from .llm_client import LLMClient

ProgressCallback = Callable[[str, dict[str, Any]], None]


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _choose_miner_count(target_volume: int, shard_hints: int = 0) -> int:
    """Scale active parallel Miner Agents (1..6) proportionally to target data volume."""
    if target_volume <= 120:
        base = 2
    elif target_volume <= 240:
        base = 3
    elif target_volume <= 380:
        base = 4
    elif target_volume <= 500:
        base = 5
    else:
        base = 6
    if shard_hints > 0:
        return max(1, min(6, max(base, shard_hints)))
    return max(1, min(6, base))


def _extract_year_bounds(text: str) -> tuple[int, int]:
    current_year = datetime.now(timezone.utc).year
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text or "") if 1980 <= int(y) <= current_year + 1]
    if len(years) >= 2:
        return min(years), max(years)
    if len(years) == 1:
        y = years[0]
        return (y, current_year) if y <= current_year else (current_year - 5, current_year)
    if any(tok in (text or "").lower() for tok in ("latest", "recent", "newest", "emerging", "最新", "近期", "前沿")):
        return max(2021, current_year - 4), current_year
    return 2018, current_year


def _work_searchable_blob(work: dict[str, Any]) -> str:
    parts: list[str] = [
        str(work.get("title") or ""),
        str(work.get("display_name") or ""),
    ]
    pt = work.get("primary_topic")
    if isinstance(pt, dict):
        parts.append(str(pt.get("display_name") or ""))
        for sub_key in ("subfield", "field", "domain"):
            sub = pt.get(sub_key)
            if isinstance(sub, dict):
                parts.append(str(sub.get("display_name") or ""))
    for t in (work.get("topics") or [])[:6]:
        if isinstance(t, dict):
            parts.append(str(t.get("display_name") or ""))
            sub = t.get("subfield")
            if isinstance(sub, dict):
                parts.append(str(sub.get("display_name") or ""))
    for c in (work.get("concepts") or [])[:8]:
        if isinstance(c, dict):
            parts.append(str(c.get("display_name") or ""))
    venue = work.get("venue")
    if isinstance(venue, dict):
        parts.append(str(venue.get("display_name") or ""))
    return " ".join(p for p in parts if p).lower()


def _score_and_validate_work(
    work: dict[str, Any],
    quality_keywords: list[str],
    year_min: int | None,
    year_max: int | None,
) -> tuple[bool, float]:
    """
    Dataset-agnostic scholarly quality & topical relevance validator.
    Returns (is_valid_quality, relevance_score).
    """
    wid = str(work.get("id") or "").strip()
    title = str(work.get("title") or work.get("display_name") or "").strip()
    yr = work.get("publication_year")
    if not wid or len(title) < 5 or not isinstance(yr, int):
        return False, 0.0
    if yr < 1900 or yr > 2035:
        return False, 0.0
    if year_min is not None and yr < year_min - 1:
        return False, 0.0
    if year_max is not None and yr > year_max + 1:
        return False, 0.0

    blob = _work_searchable_blob(work)
    ascii_kws = [k.strip().lower() for k in quality_keywords if k and k.strip() and k.strip().isascii()]

    kw_hits = 0
    for kw in ascii_kws:
        if kw in blob:
            kw_hits += 1
        else:
            # Multi-word phrase partial token match (e.g. "human computer interaction")
            tokens = [tok for tok in re.split(r"[\s\-_/]+", kw) if len(tok) >= 4]
            if len(tokens) >= 2 and sum(1 for tok in tokens if tok in blob) >= 2:
                kw_hits += 1

    # Reject completely off-topic noise when explicit domain keywords were planned and zero matched
    if ascii_kws and kw_hits == 0:
        return False, 0.0

    citations = int(work.get("cited_by_count") or 0)
    has_authors = 1.0 if work.get("authorships") else 0.0
    has_topic = 1.0 if work.get("primary_topic") else 0.0
    recency_bonus = max(0.0, (yr - 2018) * 0.25)
    score = (kw_hits * 3.0) + has_authors + has_topic + recency_bonus + min(4.0, (citations ** 0.35) * 0.4)
    return True, round(score, 3)


class MinerWorker:
    """Independent parallel Miner Agent responsible for retrieving and screening one data shard."""

    def __init__(self, miner_id: str, shard_spec: dict[str, Any]) -> None:
        self.miner_id = miner_id
        self.shard_spec = shard_spec

    async def run_openalex(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        quality_keywords: list[str],
        year_min: int | None,
        year_max: int | None,
    ) -> dict[str, Any]:
        label = str(self.shard_spec.get("label") or self.miner_id)
        search_query = str(self.shard_spec.get("search_query") or "").strip()
        filter_expr = str(self.shard_spec.get("filter_expr") or "").strip()
        sort_expr = str(self.shard_spec.get("sort_expr") or "relevance_score:desc").strip()
        limit = max(60, min(250, int(self.shard_spec.get("limit") or 150)))

        async with sem:
            try:
                shard_res = await fetch_openalex_shard(
                    client,
                    search_query=search_query,
                    filter_expr=filter_expr,
                    sort_expr=sort_expr,
                    max_records=limit,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "miner_id": self.miner_id,
                    "label": label,
                    "query_desc": search_query or filter_expr,
                    "total_available": 0,
                    "fetched_count": 0,
                    "accepted_count": 0,
                    "rejected_noise_count": 0,
                    "works": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

        raw_works = shard_res.get("works") or []
        accepted: list[tuple[float, dict[str, Any]]] = []
        rejected = 0
        for w in raw_works:
            ok, rel_score = _score_and_validate_work(w, quality_keywords, year_min, year_max)
            if ok:
                accepted.append((rel_score, w))
            else:
                rejected += 1

        # If strict keyword check rejected nearly everything on a targeted search shard, keep structural-valid works
        if not accepted and len(raw_works) >= 10 and search_query:
            rejected = 0
            for w in raw_works:
                ok_struct, rel_score = _score_and_validate_work(w, [], year_min, year_max)
                if ok_struct:
                    accepted.append((rel_score, w))
                else:
                    rejected += 1

        accepted.sort(key=lambda item: item[0], reverse=True)
        return {
            "miner_id": self.miner_id,
            "label": label,
            "query_desc": shard_res.get("query_desc", ""),
            "total_available": shard_res.get("total_available") or 0,
            "fetched_count": len(raw_works),
            "accepted_count": len(accepted),
            "rejected_noise_count": rejected,
            "works": [w for _, w in accepted],
            "error": None,
        }


class MinerCoordinator:
    """
    Adaptive Multi-Miner Coordinator:
    1. Interprets the research prompt & data_scope dynamically (zero hardcoded topic workflows).
    2. Estimates required sample volume and chooses how many parallel Miner Agents (1..6) to activate.
    3. Dispatches MinerWorkers concurrently, enforces topical & structural quality gates, and merges/deduplicates shards.
    """

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _fallback_strategy(self, research_prompt: str, data_scope: str) -> dict[str, Any]:
        combined = f"{research_prompt} {data_scope}".strip()
        y_min, y_max = _extract_year_bounds(combined)
        year_filter = f"publication_year:{y_min}-{y_max}"

        # Extract meaningful ASCII phrases/acronyms if present; otherwise use scope text
        ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,28}", combined)
        stop = {
            "the", "and", "for", "with", "from", "that", "this", "show", "build", "create",
            "analyze", "analysis", "chart", "charts", "view", "views", "data", "works", "papers",
            "all", "openalex", "recent", "latest", "research", "study", "trends", "trend",
            "network", "interactive", "visualization", "about", "into", "over", "between",
        }
        core_terms = []
        seen_lower: set[str] = set()
        for tok in ascii_tokens:
            low = tok.lower()
            if low not in stop and low not in seen_lower:
                seen_lower.add(low)
                core_terms.append(tok)

        query_phrase = " ".join(core_terms[:5]).strip() or "artificial intelligence"
        target_volume = 360
        miner_count = _choose_miner_count(target_volume)
        per_limit = min(200, max(120, int(target_volume * 0.55)))
        is_cjk = _has_cjk(research_prompt)

        sort_variants = [
            ("核心相关文献分片" if is_cjk else "Core Relevance Shard", "relevance_score:desc", year_filter),
            ("高影响力引用分片" if is_cjk else "High-Impact Citations Shard", "cited_by_count:desc", year_filter),
            ("最新前沿发表分片" if is_cjk else "Recent Frontier Shard", "publication_date:desc", f"publication_year:{max(y_min, y_max - 2)}-{y_max}"),
            ("开放获取实证分片" if is_cjk else "Open-Access Empirical Shard", "cited_by_count:desc", f"{year_filter},is_oa:true"),
        ]
        shards = []
        for idx in range(miner_count):
            lbl, srt, flt = sort_variants[idx % len(sort_variants)]
            shards.append(
                {
                    "label": lbl,
                    "search_query": query_phrase,
                    "filter_expr": flt,
                    "sort_expr": srt,
                    "limit": per_limit,
                }
            )
        return {
            "target_volume": target_volume,
            "miner_count": miner_count,
            "year_min": y_min,
            "year_max": y_max,
            "quality_keywords": core_terms[:6],
            "shards": shards,
        }

    async def plan_mining_strategy(
        self,
        research_prompt: str,
        data_scope: str,
    ) -> dict[str, Any]:
        fallback = self._fallback_strategy(research_prompt, data_scope)
        is_cjk = _has_cjk(research_prompt)
        lang_note = "Simplified Chinese" if is_cjk else "concise English"

        system_prompt = (
            "You are the Lead Data Mining Coordinator in a scientific research multi-agent system.\n"
            "Interpret the user's research prompt and `data_scope` to design a parallel OpenAlex literature mining plan.\n"
            "CRITICAL RULES:\n"
            "1. DO NOT hardcode templates. Adapt dynamically to ANY academic domain (e.g., HCI, bioinformatics, climate modeling, economics, quantum computing, ML, social science).\n"
            "2. OpenAlex indexes scholarly works in English: ALWAYS write `search_query` and `quality_keywords` in accurate academic English (translating non-English domain terms, e.g., expanding abbreviations like 'HCI' -> 'human computer interaction' AND complementary sub-queries like 'user interface interactive system', 'ubiquitous computing VR interaction', etc.).\n"
            "3. Choose `target_volume` between 180 and 550 based on analytical breadth (e.g. ~220 for focused single-metric trend, ~360-480 for multi-chart trend + collaboration network + topic hierarchy).\n"
            "4. Choose `miner_count` between 2 and 6 proportional to `target_volume` and topic breadth (2 for <=180, 3-4 for 200-380, 5-6 for >380).\n"
            "5. Design `miner_count` complementary shards covering distinct search facets, time windows, or sort criteria (`relevance_score:desc`, `cited_by_count:desc`, `publication_date:desc`).\n"
            "6. In `filter_expr`, ONLY use guaranteed-valid OpenAlex filters such as `publication_year:2020-2026`, `is_oa:true`, or `cited_by_count:>5`. NEVER invent numeric concept/topic IDs.\n"
            f"7. Write human-readable shard `label`s in {lang_note}.\n"
            "Return strict JSON:\n"
            "{\n"
            '  "target_volume": 360,\n'
            '  "miner_count": 4,\n'
            '  "year_min": 2020,\n'
            '  "year_max": 2026,\n'
            '  "quality_keywords": ["human computer interaction", "user interface", "interactive", "usability"],\n'
            '  "shards": [\n'
            '    {"label": "...", "search_query": "...", "filter_expr": "publication_year:2020-2026", "sort_expr": "relevance_score:desc", "limit": 160}\n'
            "  ]\n"
            "}"
        )
        user_payload = json.dumps(
            {
                "research_prompt": research_prompt,
                "data_scope": data_scope,
                "current_utc_year": datetime.now(timezone.utc).year,
            },
            ensure_ascii=False,
        )

        try:
            plan = await asyncio.wait_for(
                self.llm.complete_json(
                    system_prompt,
                    user_payload,
                    max_tokens=1536,
                    effort_override="low",
                ),
                timeout=15.0,
            )
            if not isinstance(plan, dict) or not isinstance(plan.get("shards"), list) or not plan["shards"]:
                return fallback

            target_vol = max(140, min(600, int(plan.get("target_volume") or 360)))
            raw_shards = [s for s in plan["shards"] if isinstance(s, dict) and str(s.get("search_query") or s.get("filter_expr") or "").strip()]
            if not raw_shards:
                return fallback

            desired_count = _choose_miner_count(target_vol, int(plan.get("miner_count") or len(raw_shards)))
            shards = raw_shards[:desired_count]
            per_shard_default = max(90, min(200, int((target_vol * 1.35) / max(1, len(shards)))))
            for s in shards:
                s["limit"] = max(80, min(200, int(s.get("limit") or per_shard_default)))

            kws = [
                str(k).strip()
                for k in (plan.get("quality_keywords") or [])
                if isinstance(k, str) and len(str(k).strip()) >= 2
            ]
            y_min_fb, y_max_fb = fallback["year_min"], fallback["year_max"]
            y_min = int(plan.get("year_min") or y_min_fb)
            y_max = int(plan.get("year_max") or y_max_fb)
            return {
                "target_volume": target_vol,
                "miner_count": len(shards),
                "year_min": min(y_min, y_max),
                "year_max": max(y_min, y_max),
                "quality_keywords": kws[:10],
                "shards": shards,
            }
        except Exception:  # noqa: BLE001
            return fallback

    async def mine_scholarly_corpus(
        self,
        research_prompt: str,
        data_scope: str,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        is_cjk = _has_cjk(research_prompt)
        strategy = await self.plan_mining_strategy(research_prompt, data_scope)
        shards: list[dict[str, Any]] = strategy["shards"]
        miner_count = len(shards)
        target_volume = int(strategy["target_volume"])
        quality_keywords: list[str] = strategy.get("quality_keywords") or []
        y_min = strategy.get("year_min")
        y_max = strategy.get("year_max")

        if on_progress:
            on_progress(
                f"Activated {miner_count} parallel Miner Agents (target={target_volume})",
                {"miner_count": miner_count, "target_volume": target_volume},
            )

        headers = {"User-Agent": "WHALES-ResearchCanvas/2.0 (MultiMiner)"}
        if OPENALEX_EMAIL:
            headers["From"] = OPENALEX_EMAIL

        sem = asyncio.Semaphore(6)
        workers = [MinerWorker(f"miner_{i + 1}", spec) for i, spec in enumerate(shards)]

        async with httpx.AsyncClient(timeout=14.0, headers=headers) as client:
            worker_reports = await asyncio.gather(
                *(
                    w.run_openalex(
                        client=client,
                        sem=sem,
                        quality_keywords=quality_keywords,
                        year_min=y_min,
                        year_max=y_max,
                    )
                    for w in workers
                )
            )

        # Deduplicate across miners while tracking exact shard origin per record for Semantic Data Map v2
        merged_works: list[dict[str, Any]] = []
        record_sources: list[str] = []
        seen_ids: set[str] = set()
        seen_titles: set[str] = set()
        shard_summaries: list[dict[str, Any]] = []
        shard_final_counts: dict[str, int] = {r["miner_id"]: 0 for r in worker_reports}

        total_fetched = sum(int(r.get("fetched_count") or 0) for r in worker_reports)
        total_rejected_noise = sum(int(r.get("rejected_noise_count") or 0) for r in worker_reports)
        cross_shard_dups = 0

        # Interleave round-robin across miners so every facet shard is well-represented up to target_volume
        max_len = max((len(r["works"]) for r in worker_reports), default=0)
        cap = max(target_volume, min(600, int(target_volume * 1.25)))
        for pos in range(max_len):
            if len(merged_works) >= cap:
                break
            for rep in worker_reports:
                w_list = rep["works"]
                if pos >= len(w_list):
                    continue
                w = w_list[pos]
                wid = str(w.get("id") or w.get("doi") or "").strip()
                norm_title = re.sub(r"\W+", "", str(w.get("title") or "").lower())[:80]
                if (wid and wid in seen_ids) or (len(norm_title) >= 12 and norm_title in seen_titles):
                    cross_shard_dups += 1
                    continue
                if wid:
                    seen_ids.add(wid)
                if norm_title:
                    seen_titles.add(norm_title)
                merged_works.append(w)
                record_sources.append(rep["miner_id"])
                shard_final_counts[rep["miner_id"]] = shard_final_counts.get(rep["miner_id"], 0) + 1
                if len(merged_works) >= cap:
                    break

        for rep in worker_reports:
            mid = rep["miner_id"]
            shard_summaries.append(
                {
                    "miner_id": mid,
                    "label": rep["label"],
                    "query_desc": rep["query_desc"],
                    "fetched_count": rep["fetched_count"],
                    "accepted_count": rep["accepted_count"],
                    "rejected_noise_count": rep["rejected_noise_count"],
                    "merged_unique_count": shard_final_counts.get(mid, 0),
                    "total_available": rep.get("total_available") or 0,
                    "error": rep.get("error"),
                }
            )

        if len(merged_works) >= 15:
            total_avail_sum = sum(int(r.get("total_available") or 0) for r in worker_reports)
            if on_progress:
                dropped_total = total_rejected_noise + cross_shard_dups
                note = (
                    (
                        f"已检索 {total_fetched} 篇相关文献，筛除 {dropped_total} 条离题、残缺及重复记录，保留 **{len(merged_works)} 篇有效样本**。"
                        if dropped_total > 0
                        else f"已检索并筛选 **{len(merged_works)} 篇相关有效文献**。"
                    )
                    if is_cjk
                    else (
                        f"Retrieved {total_fetched} records; filtered {dropped_total} off-topic, incomplete, or duplicate entries -> **{len(merged_works)} clean samples**."
                        if dropped_total > 0
                        else f"Retrieved **{len(merged_works)} relevant records**."
                    )
                )
                on_progress(
                    f"Merged {len(merged_works)} deduplicated works across {miner_count} Miner Agents",
                    {
                        "progress_note": note,
                        "miner_count": miner_count,
                        "raw_fetched": total_fetched,
                        "merged_count": len(merged_works),
                        "shards": shard_summaries,
                    },
                )

            query_summary = " | ".join(
                f"{s['label']}({s['merged_unique_count']})" for s in shard_summaries if s["merged_unique_count"] > 0
            )
            return {
                "works": merged_works,
                "provenance": {
                    "source_name": f"OpenAlex Works API ({miner_count} Parallel Miners)",
                    "source_url": OPENALEX_WORKS_URL,
                    "retrieval_time": datetime.now(timezone.utc).isoformat(),
                    "retrieval_mode": "parallel_live_openalex_miners",
                    "query_filter": query_summary or DEFAULT_FILTER,
                    "api_total_matching_works": total_avail_sum or len(merged_works),
                    "raw_record_count": len(merged_works),
                    "miner_count": miner_count,
                    "miner_shards": [
                        s for s in shard_summaries if s["merged_unique_count"] > 0
                    ],
                    "miner_record_sources": record_sources,
                    "fallback_reason": None,
                },
            }

        # Graceful offline fallback to local verified snapshot if live network is unreachable
        works, seed_prov = load_seed_corpus()
        errs = [r["error"] for r in worker_reports if r.get("error")]
        return {
            "works": works,
            "provenance": {
                "source_name": seed_prov.get("source_name", "OpenAlex Works API (Verified Local Snapshot)"),
                "source_url": seed_prov.get("source_url", OPENALEX_WORKS_URL),
                "retrieval_time": seed_prov.get("retrieval_time", datetime.now(timezone.utc).isoformat()),
                "retrieval_mode": "verified_local_openalex_snapshot",
                "query_filter": seed_prov.get("filter_expression", DEFAULT_FILTER),
                "api_total_matching_works": seed_prov.get("api_total_matching_works"),
                "raw_record_count": len(works),
                "miner_count": 1,
                "fallback_reason": "; ".join(errs[:2]) if errs else "Offline fallback to verified local OpenAlex corpus.",
            },
        }


def _parse_single_uploaded_file(files_dir: Path, rel: str) -> dict[str, Any] | None:
    fp = files_dir / rel
    if not fp.exists():
        return None
    ext = fp.suffix.lower()
    entry: dict[str, Any] = {
        "relative_path": rel,
        "extension": ext,
        "size_bytes": fp.stat().st_size,
    }
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
        if ext == ".csv":
            rows = list(csv.DictReader(io.StringIO(text)))[:5000]
            entry["format"] = "csv_rows"
            entry["rows"] = rows
        elif ext == ".tsv":
            rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))[:5000]
            entry["format"] = "tsv_rows"
            entry["rows"] = rows
        elif ext == ".json":
            entry["format"] = "json"
            entry["data"] = json.loads(text)
        elif ext == ".jsonl":
            entry["format"] = "jsonl"
            entry["rows"] = [json.loads(line) for line in text.splitlines() if line.strip()][:5000]
        else:
            entry["format"] = "text"
            entry["text"] = text[:100000]
    except Exception as exc:  # noqa: BLE001
        entry["format"] = "unparsed"
        entry["parse_error"] = str(exc)
    return entry


async def load_uploaded_bundle_parallel(
    upload_dir: Path,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """Parse uploaded multi-file bundles concurrently across parallel file-miner workers."""
    rel_files: list[str] = list(manifest.get("files") or [])
    files_dir = upload_dir / "files"
    if not rel_files:
        return [], 1

    miner_count = max(1, min(6, len(rel_files)))
    sem = asyncio.Semaphore(miner_count)

    async def _worker(rel: str) -> dict[str, Any] | None:
        async with sem:
            return await asyncio.to_thread(_parse_single_uploaded_file, files_dir, rel)

    parsed = await asyncio.gather(*(_worker(rel) for rel in rel_files))
    return [item for item in parsed if item is not None], miner_count
