# OpenAlex Verified Research Dataset Snapshot

- **Source**: OpenAlex Works API (`https://api.openalex.org/works`)
- **Retrieval Timestamp (UTC)**: `2026-11-24T06:57:53.334668+00:00`
- **Filter**: `publication_year:2018-2025,concepts.id:C154945302` (Artificial Intelligence concept `C154945302`, 2018–2025)
- **Order**: `most cited (age-adjusted) first`
- **Total Matching Works in OpenAlex**: `7079797`
- **Sampled Record Count**: `400` works (`page=1..2`, `per_page=200`)
- **Distinct Years**: `[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]`
- **Distinct Authors**: `2641`
- **Distinct Primary Topics**: `106`
- **License & Usage**: CC0 1.0 Universal (OpenAlex open metadata). Trimmed `abstract_inverted_index` to keep repository size lightweight; all statistical, co-authorship, institution, citation, and 4-level taxonomic topic hierarchy fields are authentic and intact.

## Re-fetching Fresh Snapshot

```bash
uv run --project backend python data/seed/fetch_openalex_seed.py
```

If an `OPENALEX_API_KEY` is provided in `.env`, the backend also supports live OpenAlex `filter` queries per analytical task, falling back explicitly to this verified snapshot if network/rate-limit quotas are exhausted.
