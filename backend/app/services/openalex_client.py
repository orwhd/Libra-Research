from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

from ..config import DATA_SEED_PATH, OPENALEX_API_KEY, OPENALEX_EMAIL


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_FILTER = "publication_year:2018-2025,concepts.id:C154945302"


def trim_work(work: dict[str, Any]) -> dict[str, Any]:
    authorships_trimmed = []
    for a in work.get("authorships", []):
        author = a.get("author") or {}
        insts = [
            {
                "id": inst.get("id"),
                "display_name": inst.get("display_name"),
                "country_code": inst.get("country_code"),
                "type": inst.get("type"),
            }
            for inst in (a.get("institutions") or [])
            if inst.get("display_name")
        ]
        authorships_trimmed.append(
            {
                "author_position": a.get("author_position"),
                "author": {
                    "id": author.get("id"),
                    "display_name": author.get("display_name"),
                    "orcid": author.get("orcid"),
                },
                "institutions": insts,
                "countries": a.get("countries") or [],
            }
        )

    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}

    return {
        "id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("title") or work.get("display_name"),
        "display_name": work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "type": work.get("type"),
        "cited_by_count": work.get("cited_by_count", 0),
        "is_oa": (work.get("open_access") or {}).get("is_oa", work.get("is_oa")),
        "primary_topic": work.get("primary_topic"),
        "topics": work.get("topics") or [],
        "concepts": [
            {
                "id": c.get("id"),
                "display_name": c.get("display_name"),
                "level": c.get("level"),
                "score": c.get("score"),
            }
            for c in (work.get("concepts") or [])[:10]
        ],
        "authorships": authorships_trimmed,
        "referenced_works_count": work.get(
            "referenced_works_count", len(work.get("referenced_works") or [])
        ),
        "referenced_works": (work.get("referenced_works") or [])[:40],
        "venue": work.get("venue")
        or (
            {
                "id": source.get("id"),
                "display_name": source.get("display_name"),
                "type": source.get("type"),
            }
            if source.get("display_name")
            else None
        ),
    }


def load_seed_corpus(seed_path: Path = DATA_SEED_PATH) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not seed_path.exists():
        raise FileNotFoundError(
            f"Verified OpenAlex seed corpus missing at {seed_path}. "
            "Run data/seed/fetch_openalex_seed.py first."
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    works = payload.get("works", [])
    provenance = payload.get("provenance", {})
    return works, provenance


async def fetch_openalex_corpus(
    filter_expr: str = DEFAULT_FILTER,
    max_records: int = 400,
    prefer_live: bool = False,
) -> dict[str, Any]:
    """
    Fetch works from OpenAlex API using filter queries when `OPENALEX_API_KEY`
    or `prefer_live=True` is set; gracefully falls back to the verified local
    OpenAlex snapshot when HTTP 429 / budget limits or network issues occur.
    """
    api_key = OPENALEX_API_KEY
    should_try_live = prefer_live or bool(api_key)
    live_error: str | None = None

    if should_try_live:
        try:
            works: list[dict[str, Any]] = []
            meta_first: dict[str, Any] = {}
            pages = max(1, min(3, (max_records + 199) // 200))
            headers = {"User-Agent": "WHALES-ResearchCanvas/1.0"}
            if OPENALEX_EMAIL:
                headers["From"] = OPENALEX_EMAIL
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                for page in range(1, pages + 1):
                    params: dict[str, Any] = {
                        "filter": filter_expr,
                        "per_page": min(200, max_records),
                        "page": page,
                    }
                    if api_key:
                        params["api_key"] = api_key
                    resp = await client.get(OPENALEX_WORKS_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    if not meta_first:
                        meta_first = data.get("meta") or {}
                    for raw in data.get("results", []):
                        works.append(trim_work(raw))
                        if len(works) >= max_records:
                            break
            if works:
                return {
                    "works": works[:max_records],
                    "provenance": {
                        "source_name": "OpenAlex Works API",
                        "source_url": OPENALEX_WORKS_URL,
                        "retrieval_time": datetime.now(timezone.utc).isoformat(),
                        "retrieval_mode": "live_openalex_api",
                        "query_filter": filter_expr,
                        "api_total_matching_works": meta_first.get("count"),
                        "raw_record_count": len(works[:max_records]),
                        "fallback_reason": None,
                    },
                }
        except Exception as exc:  # noqa: BLE001
            live_error = f"{type(exc).__name__}: {exc}"

    works, seed_prov = load_seed_corpus()
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
            "seed_file": str(DATA_SEED_PATH),
            "fallback_reason": live_error
            or "Using verified local OpenAlex 2018-2025 AI snapshot to ensure reproducible low-latency execution without exhausting shared public OpenAlex daily IP quota.",
        },
    }
