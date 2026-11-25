from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from typing import Any

from ..agents.llm_client import LLMClient

_SAFE_KEY_RE = re.compile(r"[^a-z0-9]+")

_HUMAN_VALUE_ALIASES: dict[str, str] = {
    "article": "Journal Articles",
    "conference-paper": "Conference Papers",
    "proceedings-article": "Conference Papers",
    "preprint": "Preprints",
    "review": "Review Articles",
    "book": "Books & Monographs",
    "book-chapter": "Book Chapters",
    "dataset": "Datasets",
    "letter": "Letters & Notes",
    "editorial": "Editorials",
}

_EXCLUDED_STATUS_WORDS = (
    "excluded",
    "filtered",
    "dropped",
    "removed",
    "missing",
    "invalid",
    "duplicate",
    "retained",
    "kept",
    "待排除",
    "已排除",
    "已保留",
)


def _slugify(text: str, prefix: str = "cat") -> str:
    slug = _SAFE_KEY_RE.sub("_", text.lower()).strip("_")
    return slug[:36] if slug else prefix


def _humanize_value(val: Any) -> str:
    raw = str(val or "").strip()
    if not raw:
        return "Unspecified"
    lower = raw.lower()
    if lower in _HUMAN_VALUE_ALIASES:
        return _HUMAN_VALUE_ALIASES[lower]
    cleaned = re.sub(r"[-_]+", " ", raw).strip()
    return cleaned[:42].title() if cleaned.islower() or "_" in raw or "-" in raw else cleaned[:42]


def _extract_raw_records(
    raw_payload: Any, source_meta: dict[str, Any], snapshot_id: str
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """
    Normalizes either a list of raw records (e.g. OpenAlex works or JSON list)
    or an uploaded multi-file bundle dict into flat records with per-source origins.
    Returns: (raw_records, inferred_record_unit, source_buckets).
    """
    records: list[dict[str, Any]] = []
    source_buckets: list[dict[str, Any]] = []
    source_name = str(source_meta.get("source_name") or "Primary Data Source")
    source_url = str(source_meta.get("source_url") or "")
    retrieval_mode = str(source_meta.get("retrieval_mode") or "")

    if isinstance(raw_payload, list):
        miner_shards = source_meta.get("miner_shards")
        rec_sources = source_meta.get("miner_record_sources")
        has_multi_shard = (
            isinstance(miner_shards, list)
            and len(miner_shards) > 1
            and isinstance(rec_sources, list)
            and len(rec_sources) == len(raw_payload)
        )
        shard_counts: Counter[str] = Counter()
        for idx, item in enumerate(raw_payload):
            rec = dict(item) if isinstance(item, dict) else {"value": item}
            if has_multi_shard:
                sid = f"src_{snapshot_id}_{rec_sources[idx]}"
                rec["__source_id"] = sid
                shard_counts[sid] += 1
            else:
                rec.setdefault("__source_id", f"src_{snapshot_id}_0")
            records.append(rec)
        unit = "works" if (records and "publication_year" in records[0] and "authorships" in records[0]) else "records"
        if has_multi_shard:
            for s in miner_shards:
                sid = f"src_{snapshot_id}_{s.get('miner_id')}"
                cnt = shard_counts.get(sid, 0)
                if cnt <= 0:
                    continue
                lbl = str(s.get("label") or s.get("miner_id") or source_name)
                source_buckets.append(
                    {
                        "id": sid,
                        "label": lbl,
                        "provider": f"OpenAlex · {lbl}",
                        "source_url": source_url,
                        "retrieval_mode": retrieval_mode,
                        "raw_count": cnt,
                        "record_unit": unit,
                        "snapshot_id": snapshot_id,
                    }
                )
        if not source_buckets:
            source_buckets.append(
                {
                    "id": f"src_{snapshot_id}_0",
                    "label": source_name,
                    "provider": source_name,
                    "source_url": source_url,
                    "retrieval_mode": retrieval_mode,
                    "raw_count": len(records),
                    "record_unit": unit,
                    "snapshot_id": snapshot_id,
                }
            )
        return records, unit, source_buckets

    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("files"), list):
        files = raw_payload["files"]
        for fidx, fitem in enumerate(files):
            if not isinstance(fitem, dict):
                continue
            rel = str(fitem.get("relative_path") or f"file_{fidx + 1}")
            src_id = f"src_{snapshot_id}_{fidx}"
            fmt = str(fitem.get("format") or "")
            f_recs: list[dict[str, Any]] = []
            if isinstance(fitem.get("rows"), list):
                for row in fitem["rows"]:
                    r = dict(row) if isinstance(row, dict) else {"value": row}
                    r["__source_id"] = src_id
                    f_recs.append(r)
            elif isinstance(fitem.get("data"), list):
                for row in fitem["data"]:
                    r = dict(row) if isinstance(row, dict) else {"value": row}
                    r["__source_id"] = src_id
                    f_recs.append(r)
            elif isinstance(fitem.get("data"), dict):
                r = dict(fitem["data"])
                r["__source_id"] = src_id
                f_recs.append(r)
            elif isinstance(fitem.get("text"), str) and fitem["text"].strip():
                f_recs.append(
                    {
                        "file_path": rel,
                        "text_excerpt": fitem["text"][:400],
                        "size_bytes": fitem.get("size_bytes", len(fitem["text"])),
                        "__source_id": src_id,
                    }
                )
            if f_recs:
                records.extend(f_recs)
                source_buckets.append(
                    {
                        "id": src_id,
                        "label": rel if len(files) > 1 else source_name,
                        "provider": source_name if len(files) == 1 else f"{source_name} · {rel}",
                        "source_url": source_url,
                        "retrieval_mode": retrieval_mode,
                        "raw_count": len(f_recs),
                        "record_unit": "rows" if fmt in ("csv_rows", "tsv_rows") else "records",
                        "snapshot_id": snapshot_id,
                    }
                )
        unit = "rows" if all(b["record_unit"] == "rows" for b in source_buckets) and source_buckets else "records"
        return records, unit, source_buckets

    if isinstance(raw_payload, dict):
        rec = dict(raw_payload)
        rec["__source_id"] = f"src_{snapshot_id}_0"
        records.append(rec)
        source_buckets.append(
            {
                "id": f"src_{snapshot_id}_0",
                "label": source_name,
                "provider": source_name,
                "source_url": source_url,
                "retrieval_mode": retrieval_mode,
                "raw_count": 1,
                "record_unit": "records",
                "snapshot_id": snapshot_id,
            }
        )
        return records, "records", source_buckets

    return [], "records", []


def _resolve_field_path(record: dict[str, Any], field_path: str) -> Any:
    if not field_path or not isinstance(record, dict):
        return None
    cur: Any = record
    for part in field_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            # Gather non-null values from list of dicts if e.g. topics.display_name
            vals = [
                item.get(part)
                for item in cur
                if isinstance(item, dict) and item.get(part) is not None
            ]
            return vals if vals else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _extract_candidate_fields(
    raw_records: list[dict[str, Any]], schema: dict[str, Any]
) -> dict[str, Any]:
    total = len(raw_records)
    if total == 0:
        return {"record_count": 0, "fields": [], "samples": [], "schema": schema}

    candidate_paths: set[str] = set()
    for rec in raw_records[:50]:
        for k, v in rec.items():
            if k.startswith("__"):
                continue
            candidate_paths.add(k)
            if isinstance(v, dict):
                for sk, sv in v.items():
                    if not isinstance(sv, dict):
                        candidate_paths.add(f"{k}.{sk}")
                    elif isinstance(sv.get("display_name"), (str, int, float)):
                        candidate_paths.add(f"{k}.{sk}.display_name")

    field_stats: list[dict[str, Any]] = []
    for path in sorted(candidate_paths):
        non_empty = 0
        scalar_counts: Counter[str] = Counter()
        numeric_vals: list[float] = []
        list_non_empty = 0
        for rec in raw_records:
            val = _resolve_field_path(rec, path)
            if val is None or val == "" or val == []:
                continue
            non_empty += 1
            if isinstance(val, list):
                list_non_empty += 1
            elif isinstance(val, bool):
                scalar_counts["true" if val else "false"] += 1
            elif isinstance(val, (int, float)):
                numeric_vals.append(float(val))
                if len(scalar_counts) < 30:
                    scalar_counts[str(val)] += 1
            elif isinstance(val, str):
                s = val.strip()
                if s and len(s) <= 80 and len(scalar_counts) < 40:
                    scalar_counts[s] += 1

        coverage = round(non_empty / total, 3) if total else 0.0
        entry: dict[str, Any] = {
            "field_path": path,
            "non_empty_count": non_empty,
            "coverage": coverage,
        }
        if scalar_counts and list_non_empty == 0:
            entry["distinct_observed"] = len(scalar_counts)
            entry["top_values"] = [
                {"value": k, "count": c} for k, c in scalar_counts.most_common(10)
            ]
        elif numeric_vals:
            entry["numeric_min"] = min(numeric_vals)
            entry["numeric_max"] = max(numeric_vals)
        elif list_non_empty:
            entry["is_list"] = True
        field_stats.append(entry)

    # Sort informative fields first (good coverage & reasonable cardinality)
    def _field_rank(fs: dict[str, Any]) -> tuple[int, float]:
        dist = fs.get("distinct_observed", 999)
        is_cat = 1 if (2 <= dist <= 12 and fs["coverage"] >= 0.4) else 0
        return (-is_cat, -fs["coverage"])

    field_stats.sort(key=_field_rank)

    compact_samples = []
    for rec in raw_records[:3]:
        compact = {}
        for k, v in rec.items():
            if k.startswith("__"):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                compact[k] = v
            elif isinstance(v, dict):
                compact[k] = {
                    sk: sv
                    for sk, sv in list(v.items())[:4]
                    if isinstance(sv, (str, int, float, bool, dict))
                }
            elif isinstance(v, list):
                compact[k] = f"list(len={len(v)})"
        compact_samples.append(compact)

    return {
        "record_count": total,
        "schema": schema,
        "fields": field_stats[:30],
        "samples": compact_samples,
    }


def _match_rule(record: dict[str, Any], rule: dict[str, Any]) -> bool:
    field_path = str(rule.get("field_path") or "").strip()
    op = str(rule.get("operator") or "eq").strip().lower()
    target = rule.get("value")
    val = _resolve_field_path(record, field_path)

    if op == "exists":
        return val is not None and val != "" and val != [] and val != {}
    if op == "not_exists":
        return val is None or val == "" or val == [] or val == {}

    if op in ("len_gte", "len_eq"):
        try:
            n = int(target)
        except Exception:  # noqa: BLE001
            return False
        length = len(val) if isinstance(val, (list, dict, str)) else 0
        return length >= n if op == "len_gte" else length == n

    if val is None:
        return False

    if op == "in":
        if not isinstance(target, list):
            target = [target]
        norm_targets = {str(x).strip().lower() for x in target}
        if isinstance(val, list):
            return any(str(x).strip().lower() in norm_targets for x in val)
        return str(val).strip().lower() in norm_targets

    if op == "eq":
        if isinstance(val, bool) and isinstance(target, bool):
            return val == target
        return str(val).strip().lower() == str(target).strip().lower()

    if op == "neq":
        return str(val).strip().lower() != str(target).strip().lower()

    if op == "contains":
        needle = str(target or "").strip().lower()
        if not needle:
            return False
        if isinstance(val, list):
            return any(needle in str(x).lower() for x in val)
        return needle in str(val).lower()

    if op in ("gte", "lte", "gt", "lt"):
        try:
            num_val = float(val)
            num_tgt = float(target)
        except Exception:  # noqa: BLE001
            return False
        if op == "gte":
            return num_val >= num_tgt
        if op == "lte":
            return num_val <= num_tgt
        if op == "gt":
            return num_val > num_tgt
        return num_val < num_tgt

    if op == "regex":
        try:
            return bool(re.search(str(target), str(val), flags=re.IGNORECASE))
        except re.error:
            return False

    return False


def _format_rule_summary(rule: dict[str, Any]) -> str:
    fp = str(rule.get("field_path") or "")
    op = str(rule.get("operator") or "eq")
    val = rule.get("value")
    if op == "exists":
        return f"{fp} is present"
    if op == "not_exists":
        return f"{fp} is missing"
    if op == "in" and isinstance(val, list):
        shown = ", ".join(str(x) for x in val[:4])
        suffix = f" (+{len(val) - 4})" if len(val) > 4 else ""
        return f"{fp} ∈ [{shown}{suffix}]"
    return f"{fp} {op} {val}"


async def _propose_semantic_rules_with_llm(
    llm: LLMClient | None,
    research_goal: str,
    dataset_summary: dict[str, Any],
    source_name: str,
    transform_steps: list[str],
) -> dict[str, Any] | None:
    if llm is None or dataset_summary.get("record_count", 0) == 0:
        return None

    system_prompt = (
        "You are a Semantic Data Profiler for a scientific data lineage system.\n"
        "Understand the semantic categories present in the RAW dataset based on the user's research goal, "
        "actual field names, observed value distributions, and sample records.\n\n"
        "CRITICAL RULES:\n"
        "1. Semantic Categories and Processing Status are TWO INDEPENDENT DIMENSIONS.\n"
        "   - Semantic categories describe WHAT each record represents (e.g., Journal Articles vs Conference Papers vs Preprints, "
        "or Hardware Category, or Clinical Trial Phase, or Study Subfield) — NEVER output labels like 'Excluded', 'Missing Year', 'Filtered Out', or 'Retained'.\n"
        "2. Propose 2 to 6 MUTUALLY EXCLUSIVE semantic category rules referencing ONLY field paths present in `fields`.\n"
        "3. Every rule uses one operator from: eq, neq, in, contains, exists, not_exists, gte, lte, gt, lt, len_gte, regex.\n"
        "4. You MUST NOT fabricate or guess record counts — Python will evaluate your exact rules on all real records.\n"
        "5. Optionally include `subcategory_field_path` on a category if there is a clean secondary grouping field (e.g., `primary_topic.field.display_name` or `department`), "
        "and optionally `secondary_tag_rules` (non-exclusive tags).\n"
        "6. Return strict JSON:\n"
        "{\n"
        '  "record_unit": "works | rows | trials | records | ...",\n'
        '  "primary_dimension_label": "Human-readable dimension name",\n'
        '  "categories": [\n'
        "    {\n"
        '      "id": "snake_case_id",\n'
        '      "label": "Human Readable Category Name",\n'
        '      "description": "Short 1-sentence semantic explanation.",\n'
        '      "field_path": "exact.field.path",\n'
        '      "operator": "in | eq | contains | gte | lte | exists",\n'
        '      "value": ["..."],\n'
        '      "confidence": "high | medium | low",\n'
        '      "basis_explanation": "Why this field reliably identifies this semantic type.",\n'
        '      "subcategory_field_path": "optional.sub.field.path"\n'
        "    }\n"
        "  ],\n"
        '  "secondary_tag_rules": [\n'
        '    {"id": "open_access", "label": "Open Access", "field_path": "is_oa", "operator": "eq", "value": true}\n'
        "  ]\n"
        "}"
    )

    user_payload = json.dumps(
        {
            "research_goal": research_goal,
            "source_name": source_name,
            "transform_steps": transform_steps[:6],
            "dataset_summary": dataset_summary,
        },
        ensure_ascii=False,
    )

    try:
        resp = await asyncio.wait_for(
            llm.complete_json(
                system_prompt,
                user_payload,
                max_tokens=2048,
                effort_override="low",
            ),
            timeout=12.0,
        )
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(resp, dict) or not isinstance(resp.get("categories"), list):
        return None

    valid_paths = {f["field_path"] for f in dataset_summary.get("fields", [])}
    valid_ops = {"in", "eq", "neq", "contains", "exists", "not_exists", "gte", "lte", "gt", "lt", "len_gte", "len_eq", "regex"}

    cleaned_cats: list[dict[str, Any]] = []
    for c in resp["categories"]:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or "").strip()
        fp = str(c.get("field_path") or "").strip()
        op = str(c.get("operator") or "").strip().lower()
        if not label or not fp or op not in valid_ops:
            continue
        if any(bad in label.lower() for bad in _EXCLUDED_STATUS_WORDS):
            continue
        if valid_paths and fp not in valid_paths:
            continue
        sub_fp = str(c.get("subcategory_field_path") or "").strip()
        if sub_fp and valid_paths and sub_fp not in valid_paths:
            sub_fp = ""
        conf = str(c.get("confidence") or "medium").lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        cleaned_cats.append(
            {
                "id": _slugify(str(c.get("id") or label), f"cat_{len(cleaned_cats) + 1}"),
                "label": label[:48],
                "description": str(c.get("description") or f"Grouped by {fp}")[:180],
                "field_path": fp,
                "operator": op,
                "value": c.get("value"),
                "confidence": conf,
                "basis_explanation": str(c.get("basis_explanation") or f"Classified using `{fp}`")[:180],
                "subcategory_field_path": sub_fp,
            }
        )

    if len(cleaned_cats) < 1:
        return None

    sec_tags: list[dict[str, Any]] = []
    for st in (resp.get("secondary_tag_rules") or [])[:4]:
        if not isinstance(st, dict):
            continue
        fp = str(st.get("field_path") or "").strip()
        op = str(st.get("operator") or "").strip().lower()
        lbl = str(st.get("label") or "").strip()
        if lbl and fp in valid_paths and op in valid_ops:
            sec_tags.append(
                {
                    "id": _slugify(str(st.get("id") or lbl), "tag"),
                    "label": lbl[:36],
                    "field_path": fp,
                    "operator": op,
                    "value": st.get("value"),
                }
            )

    return {
        "record_unit": str(resp.get("record_unit") or "records")[:32],
        "primary_dimension_label": str(resp.get("primary_dimension_label") or "Semantic Type")[:48],
        "categories": cleaned_cats[:6],
        "secondary_tag_rules": sec_tags,
    }


def _propose_deterministic_fallback_rules(
    raw_records: list[dict[str, Any]], dataset_summary: dict[str, Any]
) -> dict[str, Any]:
    fields = dataset_summary.get("fields") or []
    preferred_tokens = (
        "type",
        "category",
        "segment",
        "class",
        "group",
        "field",
        "subfield",
        "domain",
        "status",
        "phase",
        "region",
        "venue",
        "source",
        "kind",
        "topic",
    )

    best_field: dict[str, Any] | None = None
    best_score = -1.0
    for f in fields:
        fp = str(f.get("field_path") or "")
        dist = f.get("distinct_observed", 0)
        cov = float(f.get("coverage", 0.0))
        top_vals = f.get("top_values") or []
        if not top_vals or dist < 2 or dist > 12 or cov < 0.5:
            continue
        if any(fp.lower().endswith(bad) for bad in ("id", "_id", "url", "date", "time", "title", "name")):
            if not fp.lower().endswith(("display_name", "type", "category")):
                continue
        score = cov * 10.0 - abs(dist - 4) * 0.6
        if any(tok in fp.lower() for tok in preferred_tokens):
            score += 6.0
        if score > best_score:
            best_score = score
            best_field = f

    categories: list[dict[str, Any]] = []
    if best_field:
        fp = str(best_field["field_path"])
        top_vals = best_field.get("top_values") or []
        # Up to 5 main values; merge long tail into Other matched values if needed
        chosen = [tv["value"] for tv in top_vals[:5]]
        for val in chosen:
            label = _humanize_value(val)
            categories.append(
                {
                    "id": _slugify(f"{fp}_{val}", f"cat_{len(categories) + 1}"),
                    "label": label,
                    "description": f"Records whose `{fp}` attribute equals `{val}`.",
                    "field_path": fp,
                    "operator": "eq",
                    "value": val,
                    "confidence": "high" if best_field["coverage"] >= 0.85 else "medium",
                    "basis_explanation": f"Observed categorical distribution in field `{fp}` ({best_field['non_empty_count']}/{len(raw_records)} populated).",
                    "subcategory_field_path": "",
                }
            )
        if len(top_vals) > 5:
            remaining_vals = [tv["value"] for tv in top_vals[5:]]
            categories.append(
                {
                    "id": _slugify(f"{fp}_other", "other_typed"),
                    "label": f"Other {_humanize_value(fp.split('.')[-1])}",
                    "description": f"Remaining classified values under `{fp}`.",
                    "field_path": fp,
                    "operator": "in",
                    "value": remaining_vals,
                    "confidence": "medium",
                    "basis_explanation": f"Grouped minor categories from `{fp}`.",
                    "subcategory_field_path": "",
                }
            )

    unit = (
        "works"
        if (raw_records and "publication_year" in raw_records[0] and "authorships" in raw_records[0])
        else "records"
    )
    return {
        "record_unit": unit,
        "primary_dimension_label": _humanize_value(best_field["field_path"].split(".")[-1])
        if best_field
        else "Unclassified",
        "categories": categories,
        "secondary_tag_rules": [],
    }


def _record_identity_key(rec: dict[str, Any]) -> str:
    for id_key in ("id", "work_id", "record_id", "doi", "uuid"):
        v = rec.get(id_key)
        if isinstance(v, (str, int)) and str(v).strip():
            return f"{id_key}:{str(v).strip()}"
    clean = {k: v for k, v in rec.items() if not k.startswith("__")}
    return json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)


def _classify_exclusions(
    raw_records: list[dict[str, Any]],
    retained_records: list[dict[str, Any]],
    processing_trace: dict[str, Any] | None,
    missingness_quality: dict[str, Any],
    transform_steps: list[str],
    data_scope: str,
) -> tuple[set[int], list[dict[str, Any]], list[str]]:
    """
    Determines which raw_records were retained vs excluded and attributes every
    excluded record to a concrete exclusion reason, stage, and filter rule.
    """
    n_raw = len(raw_records)
    notes: list[str] = []

    if (
        isinstance(processing_trace, dict)
        and isinstance(processing_trace.get("retained_indices"), list)
        and all(isinstance(i, int) and 0 <= i < n_raw for i in processing_trace["retained_indices"])
    ):
        retained_set = set(processing_trace["retained_indices"])
        excluded_map: dict[int, dict[str, Any]] = {}
        for ex in processing_trace.get("exclusions") or []:
            if not isinstance(ex, dict):
                continue
            code = _slugify(str(ex.get("reason_code") or ex.get("reason_label") or "filtered"), "filtered")
            label = str(ex.get("reason_label") or _humanize_value(code))
            rule = str(ex.get("rule") or data_scope or "Sandbox filtering rule")
            stage = str(ex.get("stage") or "Filtering & Cleaning")
            for idx in ex.get("record_indices") or []:
                if isinstance(idx, int) and 0 <= idx < n_raw and idx not in retained_set and idx not in excluded_map:
                    excluded_map[idx] = {
                        "index": idx,
                        "reason_code": code,
                        "reason_label": label,
                        "stage": stage,
                        "filter_rule": rule,
                    }
        for idx in range(n_raw):
            if idx not in retained_set and idx not in excluded_map:
                excluded_map[idx] = {
                    "index": idx,
                    "reason_code": "scope_filter",
                    "reason_label": "Filtered Outside Target Scope",
                    "stage": "Filtering & Cleaning",
                    "filter_rule": "; ".join(transform_steps[:2]) or data_scope,
                }
        return retained_set, list(excluded_map.values()), notes

    # Deterministic alignment between retained_records and raw_records
    retained_key_counts = Counter(_record_identity_key(r) for r in retained_records)
    seen_raw_keys: set[str] = set()
    retained_indices: set[int] = set()
    excluded_list: list[dict[str, Any]] = []

    missing_field_hints = [
        k.replace("missing_", "")
        for k, v in missingness_quality.items()
        if k.startswith("missing_") and isinstance(v, (int, float)) and v > 0
    ]

    for idx, rec in enumerate(raw_records):
        key = _record_identity_key(rec)
        if retained_key_counts[key] > 0:
            retained_indices.add(idx)
            retained_key_counts[key] -= 1
            seen_raw_keys.add(key)
            continue

        # Not retained: attribute exact observable cause
        if key in seen_raw_keys:
            excluded_list.append(
                {
                    "index": idx,
                    "reason_code": "duplicate_record",
                    "reason_label": "Duplicate Record Removed",
                    "stage": "Deduplication",
                    "filter_rule": "Deduplicated by unique record identifier (`id` / row hash)",
                }
            )
            continue

        missing_hit = None
        for hint in missing_field_hints:
            v = rec.get(hint)
            if v is None or v == "" or v == []:
                missing_hit = hint
                break
        if missing_hit is None:
            for critical in ("publication_year", "title", "authorships", "primary_topic"):
                if critical in rec and (rec.get(critical) is None or rec.get(critical) == "" or rec.get(critical) == []):
                    missing_hit = critical
                    break

        if missing_hit:
            excluded_list.append(
                {
                    "index": idx,
                    "reason_code": f"missing_{_slugify(missing_hit, 'field')}",
                    "reason_label": f"Missing Required Field ({missing_hit})",
                    "stage": "Quality Validation",
                    "filter_rule": f"Excluded records missing non-empty `{missing_hit}`",
                }
            )
        else:
            rule_desc = "; ".join(transform_steps[:3]) if transform_steps else (data_scope or "Task scope filter")
            excluded_list.append(
                {
                    "index": idx,
                    "reason_code": "scope_filter",
                    "reason_label": "Outside Research Scope / Threshold",
                    "stage": "Scope & Cleaning Filter",
                    "filter_rule": rule_desc[:220],
                }
            )

    if len(retained_indices) != len(retained_records):
        notes.append(
            f"Snapshot transformed/created {len(retained_records)} output row(s) from {len(retained_indices)} matched raw record(s)."
        )

    return retained_indices, excluded_list, notes


async def build_semantic_profile(
    llm: LLMClient | None,
    raw_payload: Any,
    retained_records: list[dict[str, Any]],
    snapshot_id: str,
    source_meta: dict[str, Any],
    schema: dict[str, Any],
    missingness_quality: dict[str, Any],
    transform_steps: list[str],
    data_scope: str,
    processing_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Dataset-agnostic Semantic Data Profiler:
    1. Proposes mutually exclusive semantic classification rules using schema & sample records
       (via LLM with deterministic schema-based fallback).
    2. Executes all rules & exclusion reconciliation deterministically in Python against real
       raw & retained records so every count is 100% grounded in executed data.
    """
    raw_records, inferred_unit, source_buckets = _extract_raw_records(
        raw_payload, source_meta, snapshot_id
    )
    if not raw_records:
        return {
            "status": "unavailable",
            "unavailable_reason": "追踪信息不可用：未记录原始输入样本集合。",
        }

    dataset_summary = _extract_candidate_fields(raw_records, schema)
    plan = await _propose_semantic_rules_with_llm(
        llm=llm,
        research_goal=data_scope,
        dataset_summary=dataset_summary,
        source_name=str(source_meta.get("source_name") or ""),
        transform_steps=transform_steps,
    )

    using_llm_plan = plan is not None
    if plan is not None:
        # Verify at least 1 record matches proposed rules; otherwise fall back cleanly
        cats_test = plan.get("categories") or []
        any_match = any(_match_rule(r, c) for r in raw_records[:100] for c in cats_test)
        if not any_match:
            plan = None
            using_llm_plan = False

    if plan is None:
        plan = _propose_deterministic_fallback_rules(raw_records, dataset_summary)

    record_unit = (
        str(processing_trace.get("record_unit") or "").strip()
        if isinstance(processing_trace, dict)
        else ""
    ) or str(plan.get("record_unit") or inferred_unit or "records")

    retained_indices, excluded_records, trace_notes = _classify_exclusions(
        raw_records=raw_records,
        retained_records=retained_records,
        processing_trace=processing_trace,
        missingness_quality=missingness_quality,
        transform_steps=transform_steps,
        data_scope=data_scope,
    )

    excl_by_index = {ex["index"]: ex for ex in excluded_records}
    categories_spec: list[dict[str, Any]] = plan.get("categories") or []

    # Mutually exclusive primary classification pass over every single raw record
    cat_indices: dict[str, list[int]] = defaultdict(list)
    source_cat_counts: dict[tuple[str, str], int] = Counter()
    record_cat_map: dict[int, str] = {}

    for idx, rec in enumerate(raw_records):
        matched_id: str | None = None
        for cat in categories_spec:
            if _match_rule(rec, cat):
                matched_id = cat["id"]
                break
        if matched_id is None:
            matched_id = "unclassified"
        record_cat_map[idx] = matched_id
        cat_indices[matched_id].append(idx)
        src_id = str(rec.get("__source_id") or source_buckets[0]["id"])
        source_cat_counts[(src_id, matched_id)] += 1

    # Secondary multi-label tag counts (non-additive, strictly for inspector detail)
    sec_tag_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for tag_rule in plan.get("secondary_tag_rules") or []:
        for idx, rec in enumerate(raw_records):
            if _match_rule(rec, tag_rule):
                cid = record_cat_map[idx]
                sec_tag_counts[cid][tag_rule["label"]] += 1

    # Materialize FlowCategoryNode payloads with exact Python-counted numbers
    category_nodes: list[dict[str, Any]] = []
    all_specs = list(categories_spec)
    if cat_indices.get("unclassified"):
        all_specs.append(
            {
                "id": "unclassified",
                "label": "Unclassified / 待确认",
                "description": "Records lacking explicit field values or matching none of the inferred semantic rules.",
                "field_path": "Multiple / Missing",
                "operator": "fallback",
                "value": None,
                "confidence": "low",
                "basis_explanation": "Residual bucket ensuring strict 100% raw record accounting without hallucinated labels.",
                "subcategory_field_path": "",
                "_is_unclassified": True,
            }
        )

    cat_label_lookup: dict[str, str] = {}
    for spec in all_specs:
        cid = spec["id"]
        indices = cat_indices.get(cid, [])
        if not indices:
            continue
        is_unclassified = bool(spec.get("_is_unclassified"))
        ret_cnt = sum(1 for i in indices if i in retained_indices)
        exc_cnt = sum(1 for i in indices if i in excl_by_index)
        raw_cnt = len(indices)

        subcategories: list[dict[str, Any]] = []
        sub_fp = str(spec.get("subcategory_field_path") or "").strip()
        if sub_fp and not is_unclassified:
            sub_groups: dict[str, list[int]] = defaultdict(list)
            for i in indices:
                sv = _resolve_field_path(raw_records[i], sub_fp)
                sub_key = str(sv[0] if isinstance(sv, list) and sv else (sv if sv is not None else "Unspecified"))[:42]
                sub_groups[sub_key].append(i)
            if 1 < len(sub_groups) <= 15:
                for skey, s_indices in sorted(sub_groups.items(), key=lambda kv: -len(kv[1]))[:6]:
                    s_ret = sum(1 for i in s_indices if i in retained_indices)
                    s_exc = sum(1 for i in s_indices if i in excl_by_index)
                    subcategories.append(
                        {
                            "id": _slugify(f"{cid}_{skey}", "sub"),
                            "label": _humanize_value(skey),
                            "raw_count": len(s_indices),
                            "retained_count": s_ret,
                            "excluded_count": s_exc,
                            "rule_summary": f"{sub_fp} = {skey}",
                        }
                    )

        sec_list = [
            {"label": lbl, "count": cnt}
            for lbl, cnt in sec_tag_counts.get(cid, Counter()).most_common(4)
        ]

        cat_label_lookup[cid] = spec["label"]
        verifiable = (
            "unverified"
            if is_unclassified
            else ("verified_field" if spec.get("operator") in ("eq", "in") and using_llm_plan or not using_llm_plan else "derived_rule")
        )
        category_nodes.append(
            {
                "id": cid,
                "label": spec["label"],
                "description": spec["description"],
                "field_basis": f"{spec['field_path']} — {spec['basis_explanation']}",
                "rule_summary": "Fallback residual bucket" if is_unclassified else _format_rule_summary(spec),
                "confidence": spec["confidence"],
                "verifiable_status": verifiable,
                "dataset_version": snapshot_id,
                "raw_count": raw_cnt,
                "retained_count": ret_cnt,
                "excluded_count": exc_cnt,
                "transformed_count": 0,
                "is_unclassified": is_unclassified,
                "subcategories": subcategories,
                "secondary_tags": sec_list,
            }
        )

    # Group exclusions by (reason_code, stage, filter_rule) with per-category breakdown
    excl_groups: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(Counter)
    for idx, ex in excl_by_index.items():
        cid = record_cat_map[idx]
        gkey = (ex["reason_code"], ex["reason_label"], ex["stage"], ex["filter_rule"])
        excl_groups[gkey][cid] += 1

    exclusion_branches: list[dict[str, Any]] = []
    for (rcode, rlabel, stage, frule), cat_counts in sorted(
        excl_groups.items(), key=lambda kv: -sum(kv[1].values())
    ):
        total_for_reason = sum(cat_counts.values())
        affected = [
            {
                "category_id": cid,
                "category_label": cat_label_lookup.get(cid, cid),
                "count": cnt,
            }
            for cid, cnt in sorted(cat_counts.items(), key=lambda kv: -kv[1])
        ]
        exclusion_branches.append(
            {
                "id": f"out_excl_{rcode}",
                "outcome_kind": "excluded",
                "label": rlabel,
                "count": total_for_reason,
                "stage": stage,
                "reason_code": rcode,
                "reason_label": rlabel,
                "filter_rule": frule,
                "affected_categories": affected,
            }
        )

    source_category_flows = [
        {"source_id": sid, "category_id": cid, "value": val}
        for (sid, cid), val in sorted(source_cat_counts.items())
        if val > 0
    ]

    return {
        "status": "complete",
        "primary_dimension_label": plan.get("primary_dimension_label", "Semantic Category"),
        "record_unit": record_unit,
        "total_input_count": len(raw_records),
        "total_retained_count": len(retained_indices),
        "total_snapshot_output_count": len(retained_records),
        "total_excluded_count": len(excluded_records),
        "sources": source_buckets,
        "categories": category_nodes,
        "source_category_flows": source_category_flows,
        "exclusion_branches": exclusion_branches,
        "transform_steps": transform_steps,
        "notes": trace_notes,
        "profiling_method": "llm_rules_verified_in_python" if using_llm_plan else "schema_distribution_in_python",
    }
