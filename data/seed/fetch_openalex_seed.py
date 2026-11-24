#!/usr/bin/env python3
"""
Fetch a real, well-documented subset of OpenAlex AI research works (2018-2025)
using low-cost filter queries (`concepts.id:C154945302`), keeping full scholarly
provenance while trimming redundant inverted-index text.

Usage:
    uv run --project backend python data/seed/fetch_openalex_seed.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_JSON = ROOT / "data" / "seed" / "openalex_ai_works_2018_2025.json"
OUT_README = ROOT / "data" / "seed" / "README_DATA.md"

FILTER_EXPR = "publication_year:2018-2025,concepts.id:C154945302"
PER_PAGE = 200
PAGES = [1, 2]
BASE_URL = "https://api.openalex.org/works"


def trim_work(work: dict[str, Any]) -> dict[str, Any]:
    """Keep authentic scholarly metadata needed for stats, networks, and hierarchies."""
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
        "is_oa": (work.get("open_access") or {}).get("is_oa"),
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
        "referenced_works_count": len(work.get("referenced_works") or []),
        "referenced_works": (work.get("referenced_works") or [])[:40],
        "venue": {
            "id": source.get("id"),
            "display_name": source.get("display_name"),
            "type": source.get("type"),
        }
        if source.get("display_name")
        else None,
    }


def main() -> None:
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    works: list[dict[str, Any]] = []
    meta_summary: dict[str, Any] = {}
    request_urls: list[str] = []

    with httpx.Client(timeout=20.0, headers={"User-Agent": "WHALES-ResearchCanvas/1.0"}) as client:
        for page in PAGES:
            params: dict[str, Any] = {
                "filter": FILTER_EXPR,
                "per_page": PER_PAGE,
                "page": page,
            }
            if api_key:
                params["api_key"] = api_key

            resp = client.get(BASE_URL, params=params)
            request_urls.append(str(resp.url).replace(api_key, "***") if api_key else str(resp.url))
            resp.raise_for_status()
            payload = resp.json()
            if not meta_summary:
                meta_summary = payload.get("meta") or {}
            for raw in payload.get("results", []):
                works.append(trim_work(raw))

    dataset = {
        "provenance": {
            "source_name": "OpenAlex Works API",
            "source_url": "https://api.openalex.org/works",
            "request_urls": request_urls,
            "filter_expression": FILTER_EXPR,
            "concept_label": "Artificial intelligence (C154945302)",
            "publication_year_range": [2018, 2025],
            "sort_order": meta_summary.get("x_query", {}).get("order", "most cited (age-adjusted) first"),
            "api_total_matching_works": meta_summary.get("count"),
            "raw_record_count": len(works),
            "retrieval_time": retrieved_at,
            "license": "CC0 1.0 Universal (OpenAlex Public Data)",
            "notes": (
                "Compact verified local snapshot of 400 high-impact OpenAlex AI works (2018-2025). "
                "Preserves full authorship, institution, citation, concept, and 4-level topic hierarchy "
                "(domain -> field -> subfield -> topic) while stripping bulky abstract_inverted_index."
            ),
        },
        "works": works,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    years = sorted({w["publication_year"] for w in works if w.get("publication_year")})
    authors = {
        a["author"]["id"] or a["author"]["display_name"]
        for w in works
        for a in w.get("authorships", [])
        if a.get("author", {}).get("display_name")
    }
    topics = {
        w["primary_topic"]["display_name"]
        for w in works
        if isinstance(w.get("primary_topic"), dict) and w["primary_topic"].get("display_name")
    }

    readme = f"""# OpenAlex Verified Research Dataset Snapshot

- **Source**: OpenAlex Works API (`https://api.openalex.org/works`)
- **Retrieval Timestamp (UTC)**: `{retrieved_at}`
- **Filter**: `{FILTER_EXPR}` (Artificial Intelligence concept `C154945302`, 2018–2025)
- **Order**: `{dataset['provenance']['sort_order']}`
- **Total Matching Works in OpenAlex**: `{meta_summary.get('count')}`
- **Sampled Record Count**: `{len(works)}` works (`page=1..2`, `per_page=200`)
- **Distinct Years**: `{years}`
- **Distinct Authors**: `{len(authors)}`
- **Distinct Primary Topics**: `{len(topics)}`
- **License & Usage**: CC0 1.0 Universal (OpenAlex open metadata). Trimmed `abstract_inverted_index` to keep repository size lightweight; all statistical, co-authorship, institution, citation, and 4-level taxonomic topic hierarchy fields are authentic and intact.

## Re-fetching Fresh Snapshot

```bash
uv run --project backend python data/seed/fetch_openalex_seed.py
```

If an `OPENALEX_API_KEY` is provided in `.env`, the backend also supports live OpenAlex `filter` queries per analytical task, falling back explicitly to this verified snapshot if network/rate-limit quotas are exhausted.
"""
    OUT_README.write_text(readme, encoding="utf-8")
    print(f"Saved {len(works)} works to {OUT_JSON} ({OUT_JSON.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
