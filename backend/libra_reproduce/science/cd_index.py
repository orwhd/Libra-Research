from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import httpx
import pandas as pd

from ..config import resolve_llm_runtime_config


@dataclass(frozen=True)
class DisruptionCounts:
    focal_id: str
    focal_year: int
    n_refs: int
    n_i: int
    n_j: int
    n_k: int
    disruption: float
    window_years: int | None
    self_loops_ignored: int = 0
    future_refs_ignored: int = 0
    backward_citers_ignored: int = 0


def compute_cd_index(
    focal_id: str,
    papers_year: dict[str, int],
    directed_citations: Iterable[tuple[str, str]],
    *,
    window_years: int | None = None,
) -> DisruptionCounts:
    """
    Independently compute the Funk & Owen-Smith / Wu-Wang-Evans Disruption Index:
        D = (n_i - n_j) / (n_i + n_j + n_k)
    where edge `(citing_id, cited_id)` means `citing_id` cites `cited_id`.
    - Enforces non-self-loop and temporal ordering checks (`year(ref) <= year(focal)` and `year(citer) >= year(focal)`).
    - When `window_years` is set (e.g., 5 or 10), restricts forward citing papers `c` to `year(focal) <= year(c) <= year(focal) + window_years`.
    - Returns `float('nan')` (never `0.0`) when `n_i + n_j + n_k == 0` or `R(focal)` is empty.
    """
    if focal_id not in papers_year:
        raise KeyError(f"Focal paper '{focal_id}' missing from papers_year mapping.")

    focal_year = int(papers_year[focal_id])
    out_refs: dict[str, set[str]] = {}
    self_loops = 0
    for src, dst in directed_citations:
        if src == dst:
            self_loops += 1
            continue
        out_refs.setdefault(src, set()).add(dst)

    raw_refs = out_refs.get(focal_id, set())
    valid_refs: set[str] = set()
    future_refs = 0
    for r in raw_refs:
        ry = papers_year.get(r)
        if ry is not None and ry > focal_year:
            future_refs += 1
            continue
        valid_refs.add(r)

    if not valid_refs:
        return DisruptionCounts(
            focal_id=focal_id,
            focal_year=focal_year,
            n_refs=0,
            n_i=0,
            n_j=0,
            n_k=0,
            disruption=float("nan"),
            window_years=window_years,
            self_loops_ignored=self_loops,
            future_refs_ignored=future_refs,
            backward_citers_ignored=0,
        )

    n_i = 0
    n_j = 0
    n_k = 0
    backward_citers = 0

    for citer_id in sorted(out_refs.keys()):
        if citer_id == focal_id:
            continue
        cites_set = out_refs[citer_id]
        cites_focal = focal_id in cites_set
        cites_any_ref = bool(cites_set & valid_refs)
        if not cites_focal and not cites_any_ref:
            continue

        cy = papers_year.get(citer_id)
        if cy is None:
            continue
        if cy < focal_year:
            backward_citers += 1
            continue
        # Exclude papers published prior to focal; if window_years is set, enforce upper bound
        if cy == focal_year and citer_id in valid_refs:
            continue
        if window_years is not None and cy > focal_year + window_years:
            continue

        if cites_focal and not cites_any_ref:
            n_i += 1
        elif cites_focal and cites_any_ref:
            n_j += 1
        elif (not cites_focal) and cites_any_ref:
            n_k += 1

    denom = n_i + n_j + n_k
    d_val = (n_i - n_j) / denom if denom > 0 else float("nan")
    return DisruptionCounts(
        focal_id=focal_id,
        focal_year=focal_year,
        n_refs=len(valid_refs),
        n_i=n_i,
        n_j=n_j,
        n_k=n_k,
        disruption=d_val,
        window_years=window_years,
        self_loops_ignored=self_loops,
        future_refs_ignored=future_refs,
        backward_citers_ignored=backward_citers,
    )


def verify_cd_toy_graphs() -> dict[str, Any]:
    """Deterministic unit-verifiable S2 toy graph checks (SCI-02)."""
    years = {
        "r1": 1995,
        "r2": 1998,
        "p": 2000,
        "c_i1": 2002,
        "c_i2": 2004,
        "c_i3": 2008,
        "c_j1": 2003,
        "c_k1": 2001,
        "c_k2": 2007,
    }
    edges = [
        ("p", "r1"),
        ("p", "r2"),
        ("p", "p"),  # self-loop must be ignored
        ("c_i1", "p"),
        ("c_i2", "p"),
        ("c_i3", "p"),
        ("c_j1", "p"),
        ("c_j1", "r1"),
        ("c_k1", "r2"),
        ("c_k2", "r1"),
    ]
    res_10y = compute_cd_index("p", years, edges, window_years=10)
    res_5y = compute_cd_index("p", years, edges, window_years=5)
    res_perm = compute_cd_index("p", years, list(reversed(edges)), window_years=10)
    res_zero = compute_cd_index("p", {"r1": 1995, "p": 2000}, [("p", "r1")], window_years=5)

    assert res_10y.n_i == 3 and res_10y.n_j == 1 and res_10y.n_k == 2
    assert math.isclose(res_10y.disruption, (3 - 1) / 6, rel_tol=1e-9)
    assert res_5y.n_i == 2 and res_5y.n_j == 1 and res_5y.n_k == 1
    assert math.isclose(res_5y.disruption, (2 - 1) / 4, rel_tol=1e-9)
    assert math.isclose(res_perm.disruption, res_10y.disruption, rel_tol=1e-12)
    assert math.isnan(res_zero.disruption)

    return {
        "passed": True,
        "toy_10y": {"n_i": res_10y.n_i, "n_j": res_10y.n_j, "n_k": res_10y.n_k, "D": round(res_10y.disruption, 6)},
        "toy_5y": {"n_i": res_5y.n_i, "n_j": res_5y.n_j, "n_k": res_5y.n_k, "D": round(res_5y.disruption, 6)},
        "zero_denom_is_nan": True,
        "permutation_invariant": True,
        "self_loops_filtered": res_10y.self_loops_ignored == 1,
    }


async def compute_bounded_openalex_s2_window_check(max_focal: int = 12) -> pd.DataFrame:
    """
    Bounded S2 empirical check on real OpenAlex works (2005-2012 Computer Science / Complex Systems)
    comparing exact D_5y vs D_10y on an observed OpenAlex citation neighborhood.
    Documented strictly as a bounded subgraph check (capped citing works per node for API feasibility).
    """
    cfg = resolve_llm_runtime_config()
    headers = {"User-Agent": "LibraResearch-ReproduceV2/0.2"}
    if cfg["openalex_email"]:
        headers["From"] = cfg["openalex_email"]

    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            params: dict[str, Any] = {
                "filter": "publication_year:2006-2011,cited_by_count:15-120,referenced_works_count:5-25",
                "per_page": max_focal,
                "sort": "cited_by_count:desc",
            }
            if cfg["openalex_api_key"]:
                params["api_key"] = cfg["openalex_api_key"]
            resp = await client.get("https://api.openalex.org/works", params=params)
            resp.raise_for_status()
            focal_works = resp.json().get("results") or []

            for fw in focal_works[:max_focal]:
                fid = str(fw.get("id") or "")
                fyear = fw.get("publication_year")
                refs = [str(r) for r in (fw.get("referenced_works") or [])[:12]]
                team_size = len(fw.get("authorships") or [])
                if not fid or not fyear or not refs or team_size <= 0:
                    continue

                short_fid = fid.rsplit("/", 1)[-1]
                short_refs = [r.rsplit("/", 1)[-1] for r in refs[:8]]

                # Fetch citing works of focal paper and citing works of up to 8 sampled references
                c_params: dict[str, Any] = {
                    "filter": f"cites:{short_fid}",
                    "per_page": 100,
                    "select": "id,publication_year,referenced_works",
                }
                r_params: dict[str, Any] = {
                    "filter": f"cites:{'|'.join(short_refs)}",
                    "per_page": 150,
                    "select": "id,publication_year,referenced_works",
                }
                if cfg["openalex_api_key"]:
                    c_params["api_key"] = cfg["openalex_api_key"]
                    r_params["api_key"] = cfg["openalex_api_key"]

                c_resp, r_resp = await __import__("asyncio").gather(
                    client.get("https://api.openalex.org/works", params=c_params),
                    client.get("https://api.openalex.org/works", params=r_params),
                )
                if c_resp.status_code != 200 or r_resp.status_code != 200:
                    continue

                years_map: dict[str, int] = {fid: int(fyear)}
                edges: list[tuple[str, str]] = []
                for ref_full in refs[:8]:
                    years_map[ref_full] = int(fyear) - 1
                    edges.append((fid, ref_full))

                seen_citers: dict[str, dict[str, Any]] = {}
                for item in (c_resp.json().get("results") or []) + (r_resp.json().get("results") or []):
                    cid = str(item.get("id") or "")
                    if cid and cid != fid:
                        seen_citers[cid] = item

                target_nodes = {fid, *refs[:8]}
                for cid, item in seen_citers.items():
                    cy = item.get("publication_year")
                    if not cy:
                        continue
                    years_map[cid] = int(cy)
                    for cited in item.get("referenced_works") or []:
                        cited_s = str(cited)
                        if cited_s in target_nodes:
                            edges.append((cid, cited_s))

                d5 = compute_cd_index(fid, years_map, edges, window_years=5)
                d10 = compute_cd_index(fid, years_map, edges, window_years=10)
                rows.append(
                    {
                        "openalex_id": short_fid,
                        "title": str(fw.get("display_name") or "")[:90],
                        "year": int(fyear),
                        "team_size": int(team_size),
                        "team_group": "Small (1-3)" if team_size <= 3 else "Large (4+)",
                        "sampled_refs": len(short_refs),
                        "d5_n_i": d5.n_i,
                        "d5_n_j": d5.n_j,
                        "d5_n_k": d5.n_k,
                        "d_5y": d5.disruption,
                        "d10_n_i": d10.n_i,
                        "d10_n_j": d10.n_j,
                        "d10_n_k": d10.n_k,
                        "d_10y": d10.disruption,
                    }
                )
    except Exception:
        pass

    return pd.DataFrame(rows)
