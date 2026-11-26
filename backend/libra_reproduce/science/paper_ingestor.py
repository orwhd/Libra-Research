from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import re
import socket
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", flags=re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)(\d{4}\.\d{4,5})(?:v\d+)?", flags=re.IGNORECASE)
_SAFE_ZIP_EXTS = frozenset({".py", ".csv", ".tsv", ".json", ".md", ".txt", ".yaml", ".yml", ".parquet"})


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _profile_tabular_bytes(raw: bytes, ext: str) -> dict[str, Any]:
    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(raw), nrows=500)
        elif ext == ".tsv":
            df = pd.read_csv(io.BytesIO(raw), sep="\t", nrows=500)
        elif ext == ".parquet":
            df = pd.read_parquet(io.BytesIO(raw)).head(500)
        else:
            return {}
        return {
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
            "preview_rows": int(len(df)),
            "missingness": {str(c): round(float(df[c].isna().mean()), 4) for c in df.columns},
            "sample_rows": json.loads(df.head(5).to_json(orient="records")),
        }
    except Exception as exc:
        return {"profile_error": str(exc)}


def extract_attachment_dossier(file_path: Path, workspace_data_dir: Path) -> dict[str, Any]:
    """Safely extract structured paper text, links, or dataset schemas from ANY uploaded attachment."""
    att_dir = workspace_data_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    raw = file_path.read_bytes()
    sha = _sha256_bytes(raw)
    suffix = file_path.suffix.lower()
    dest = att_dir / file_path.name
    if not dest.exists() or _sha256_bytes(dest.read_bytes()) != sha:
        dest.write_bytes(raw)

    rel_saved = f"data/attachments/{dest.name}"
    info: dict[str, Any] = {
        "filename": file_path.name,
        "rel_path": rel_saved,
        "sha256": sha,
        "size_bytes": len(raw),
        "kind": suffix.lstrip(".") or "binary",
        "detected_links": [],
        "detected_dois": [],
        "detected_arxiv_ids": [],
    }

    extracted_text = ""
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            meta = {}
            if reader.metadata:
                for k in ("/Title", "/Author", "/Subject", "/CreationDate"):
                    if k in reader.metadata and reader.metadata[k]:
                        meta[k.lstrip("/").lower()] = str(reader.metadata[k])
            info["pdf_metadata"] = meta
            info["page_count"] = len(reader.pages)
            chunks: list[str] = []
            for idx, page in enumerate(reader.pages[:28]):
                txt = (page.extract_text() or "").strip()
                if txt:
                    chunks.append(f"--- Page {idx + 1} ---\n{txt}")
            extracted_text = "\n\n".join(chunks)[:180_000]
            txt_out = att_dir / f"{file_path.stem}_extracted.txt"
            txt_out.write_text(extracted_text, encoding="utf-8")
            info["extracted_text_rel_path"] = f"data/attachments/{txt_out.name}"
            info["excerpt"] = extracted_text[:10_000]
        except Exception as exc:
            info["pdf_parse_error"] = str(exc)
    elif suffix in (".csv", ".tsv", ".parquet"):
        info["tabular_profile"] = _profile_tabular_bytes(raw, suffix)
    elif suffix == ".json":
        txt = raw.decode("utf-8", errors="replace")
        extracted_text = txt[:80_000]
        info["excerpt"] = txt[:10_000]
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, dict):
                info["json_keys"] = list(parsed.keys())[:30]
            elif isinstance(parsed, list):
                info["json_list_len"] = len(parsed)
        except Exception:
            pass
    elif suffix in (".txt", ".md", ".py"):
        txt = raw.decode("utf-8", errors="replace")
        extracted_text = txt[:80_000]
        info["excerpt"] = txt[:10_000]
    elif suffix == ".zip":
        extract_root = att_dir / f"{file_path.stem}_unzipped"
        extract_root.mkdir(parents=True, exist_ok=True)
        extracted_files: list[str] = []
        total_out = 0
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for member in zf.infolist()[:120]:
                    if member.is_dir():
                        continue
                    norm = member.filename.replace("\\", "/").lstrip("/")
                    parts = Path(norm).parts
                    if not norm or ".." in parts or any(p.startswith(".") for p in parts):
                        continue
                    if Path(norm).suffix.lower() not in _SAFE_ZIP_EXTS:
                        continue
                    if member.file_size > 10 * 1024 * 1024:
                        continue
                    total_out += member.file_size
                    if total_out > 25 * 1024 * 1024:
                        break
                    out_file = (extract_root / norm).resolve()
                    try:
                        out_file.relative_to(extract_root.resolve())
                    except ValueError:
                        continue
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file.write_bytes(zf.read(member))
                    extracted_files.append(f"data/attachments/{extract_root.name}/{norm}")
        except Exception as exc:
            info["zip_error"] = str(exc)
        info["extracted_files"] = extracted_files

    if extracted_text:
        info["detected_links"] = list(dict.fromkeys(_URL_RE.findall(extracted_text)))[:25]
        info["detected_dois"] = list(dict.fromkeys(_DOI_RE.findall(extracted_text)))[:10]
        info["detected_arxiv_ids"] = list(dict.fromkeys(_ARXIV_RE.findall(extracted_text)))[:10]
    return info


async def _validate_public_url(url: str) -> str:
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only valid http/https URLs are permitted.")
    host = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)
    addrs = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    for entry in addrs:
        ip_str = entry[4][0].split("%")[0]
        ip = ipaddress.ip_address(ip_str)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError(f"Blocked non-public host '{host}'.")
    return parsed.geturl()


async def fetch_public_dataset_url(
    url: str,
    dest_dir: Path,
    *,
    filename: str = "",
    max_bytes: int = 20 * 1024 * 1024,
) -> dict[str, Any]:
    """SSRF-safe public HTTP/HTTPS download helper for open paper datasets or code artifacts."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    current = await _validate_public_url(url)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers={"User-Agent": "LibraResearch-Reproduce/0.2"}) as client:
        for _ in range(5):
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    next_url = urllib.parse.urljoin(current, resp.headers["location"])
                    current = await _validate_public_url(next_url)
                    continue
                if resp.status_code >= 400:
                    return {"ok": False, "error": f"HTTP {resp.status_code} fetching {current}"}
                chunks: list[bytes] = []
                total = 0
                async for ch in resp.aiter_bytes():
                    total += len(ch)
                    if total > max_bytes:
                        return {"ok": False, "error": f"Remote payload exceeds {max_bytes // (1024 * 1024)} MB limit."}
                    chunks.append(ch)
                raw = b"".join(chunks)
                break
        else:
            return {"ok": False, "error": "Too many redirects."}

    url_name = Path(urllib.parse.urlsplit(current).path).name
    chosen = re.sub(r"[^A-Za-z0-9._-]", "_", (filename or url_name or "downloaded_data.dat").strip()).lstrip(".") or "downloaded_data.dat"
    out_path = dest_dir / chosen
    out_path.write_bytes(raw)
    sha = _sha256_bytes(raw)
    res: dict[str, Any] = {
        "ok": True,
        "final_url": current,
        "filename": out_path.name,
        "size_bytes": len(raw),
        "sha256": sha,
    }
    ext = out_path.suffix.lower()
    if ext in (".csv", ".tsv", ".parquet"):
        res["tabular_profile"] = _profile_tabular_bytes(raw, ext)
    else:
        res["excerpt"] = raw.decode("utf-8", errors="replace")[:4000]
    return res


async def fetch_paper_metadata_from_prompt(
    user_prompt: str,
    attachment_dossiers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generic arXiv / DOI / title metadata resolver using public scholarly APIs (never hardcoded to any single paper)."""
    combined = user_prompt + "\n" + "\n".join(str(d.get("excerpt") or "")[:3000] for d in attachment_dossiers)
    arxiv_matches = _ARXIV_RE.findall(combined)
    doi_matches = _DOI_RE.findall(combined)
    quoted_titles = re.findall(r'"([^"]{12,180})"', user_prompt)

    arxiv_id = arxiv_matches[0] if arxiv_matches else None
    doi = doi_matches[0] if doi_matches else None
    title_guess = quoted_titles[0] if quoted_titles else ""

    result: dict[str, Any] = {
        "detected_arxiv_id": arxiv_id,
        "detected_doi": doi,
        "title_guess": title_guess,
        "resolved": False,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "LibraResearch/0.2"}) as client:
            if arxiv_id:
                r = await client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
                if r.status_code == 200 and "<entry>" in r.text:
                    root = ET.fromstring(r.text)
                    ns = {"a": "http://www.w3.org/2005/Atom"}
                    entry = root.find("a:entry", ns)
                    if entry is not None:
                        t_el = entry.find("a:title", ns)
                        s_el = entry.find("a:summary", ns)
                        authors = [
                            (a.findtext("a:name", default="", namespaces=ns) or "").strip()
                            for a in entry.findall("a:author", ns)
                        ]
                        pub = (entry.findtext("a:published", default="", namespaces=ns) or "")[:4]
                        result.update(
                            {
                                "resolved": True,
                                "source": "arxiv_api",
                                "title": " ".join((t_el.text or "").split()) if t_el is not None else "",
                                "abstract": " ".join((s_el.text or "").split())[:2500] if s_el is not None else "",
                                "authors": [a for a in authors if a][:8],
                                "year": pub,
                                "url": f"https://arxiv.org/abs/{arxiv_id}",
                            }
                        )
                        return result

            if doi:
                r = await client.get(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}")
                if r.status_code == 200:
                    w = r.json()
                    auths = [
                        str((a.get("author") or {}).get("display_name") or "")
                        for a in (w.get("authorships") or [])[:8]
                    ]
                    result.update(
                        {
                            "resolved": True,
                            "source": "openalex_doi",
                            "title": str(w.get("title") or ""),
                            "year": str(w.get("publication_year") or ""),
                            "authors": [a for a in auths if a],
                            "venue": str(((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""),
                            "url": f"https://doi.org/{doi}",
                        }
                    )
                    return result

            if title_guess:
                r = await client.get(
                    "https://api.openalex.org/works",
                    params={"search": title_guess, "per-page": 1},
                )
                if r.status_code == 200:
                    items = (r.json() or {}).get("results") or []
                    if items:
                        w = items[0]
                        auths = [
                            str((a.get("author") or {}).get("display_name") or "")
                            for a in (w.get("authorships") or [])[:8]
                        ]
                        result.update(
                            {
                                "resolved": True,
                                "source": "openalex_title_search",
                                "title": str(w.get("title") or ""),
                                "year": str(w.get("publication_year") or ""),
                                "authors": [a for a in auths if a],
                                "doi": str(w.get("doi") or ""),
                                "url": str(w.get("doi") or w.get("id") or ""),
                            }
                        )
                        return result
    except Exception as exc:
        result["lookup_note"] = f"Metadata lookup skipped cleanly: {type(exc).__name__}"

    return result


async def stage_paper_workspace(
    run_dir: Path,
    *,
    user_prompt: str,
    attachment_paths: list[Path],
    mode: str,
) -> dict[str, Any]:
    """Ingest ANY uploaded paper PDFs, datasets, code archives, and prompt/URL references into `data/paper_dossier.json`."""
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    dossiers = [extract_attachment_dossier(p, data_dir) for p in attachment_paths if p.exists()]

    meta: dict[str, Any] = {"resolved": False}
    if mode == "live":
        meta = await fetch_paper_metadata_from_prompt(user_prompt, dossiers)

    tabular_files = [
        d["rel_path"]
        for d in dossiers
        if d.get("kind") in ("csv", "tsv", "parquet")
    ]
    extracted_texts = [
        d["extracted_text_rel_path"]
        for d in dossiers
        if d.get("extracted_text_rel_path")
    ]

    dossier = {
        "user_prompt": user_prompt,
        "attachments": dossiers,
        "scholarly_metadata": meta,
        "tabular_rel_paths": tabular_files,
        "extracted_text_rel_paths": extracted_texts,
    }
    dossier_path = data_dir / "paper_dossier.json"
    dossier_path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dossier": dossier,
        "dossier_path": dossier_path,
        "data_dir": data_dir,
    }


_METHOD_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:#*\s*)?(?:\d+(?:\.\d+)?\s*)?(?:Methods?|Materials\s+and\s+Methods|Methodology|Experimental\s+Setup|Experiments?|Algorithm|Model|Approach|Data(?:sets?)?|Equations?|Results?)\b",
    flags=re.IGNORECASE,
)


def build_paper_context_brief(
    dossier: dict[str, Any],
    workspace_dir: Path,
    *,
    max_chars: int = 6800,
) -> str:
    """
    Build a concise, 100% paper-agnostic structured digest from `paper_dossier.json` and
    extracted PDF/tabular/code attachments so specialist agents can understand the paper and
    launch experiments immediately without 10+ pagination calls.
    """
    sections: list[str] = []
    sch = dossier.get("scholarly_metadata") or {}
    if sch:
        meta_lines = []
        if sch.get("title"):
            meta_lines.append(f"Title: {sch['title']}")
        if sch.get("authors"):
            meta_lines.append(f"Authors: {', '.join(str(a) for a in sch['authors'][:6])}")
        if sch.get("year") or sch.get("venue"):
            meta_lines.append(f"Year/Venue: {sch.get('year', '')} {sch.get('venue', '')}".strip())
        if sch.get("url") or sch.get("detected_doi") or sch.get("detected_arxiv_id"):
            meta_lines.append(
                f"Identifier: {sch.get('url') or sch.get('detected_doi') or sch.get('detected_arxiv_id')}"
            )
        if sch.get("abstract"):
            meta_lines.append(f"Abstract: {str(sch['abstract'])[:1200]}")
        if meta_lines:
            sections.append("[Scholarly Metadata]\n" + "\n".join(meta_lines))

    att_lines: list[str] = []
    all_links: list[str] = []
    all_dois: list[str] = []
    all_arxiv: list[str] = []
    for att in dossier.get("attachments") or []:
        rel = str(att.get("rel_path") or att.get("filename") or "")
        kind = str(att.get("kind") or "file")
        size_kb = round(int(att.get("size_bytes") or 0) / 1024, 1)
        att_lines.append(f"- {rel} ({kind}, {size_kb} KB)")
        tab = att.get("tabular_profile")
        if isinstance(tab, dict) and tab.get("columns"):
            cols = ", ".join(str(c) for c in tab["columns"][:20])
            att_lines.append(f"  Columns: {cols}")
            if tab.get("sample_rows"):
                att_lines.append(f"  Sample row[0]: {json.dumps(tab['sample_rows'][0], ensure_ascii=False)[:350]}")
        all_links.extend(att.get("detected_links") or [])
        all_dois.extend(att.get("detected_dois") or [])
        all_arxiv.extend(att.get("detected_arxiv_ids") or [])

    if att_lines:
        sections.append("[Attached Files & Schemas]\n" + "\n".join(att_lines[:24]))

    refs = list(dict.fromkeys([*all_links, *all_dois, *all_arxiv]))[:10]
    if refs:
        sections.append("[Detected Public Links / DOIs / arXiv IDs]\n" + "\n".join(f"- {r}" for r in refs))

    ext_paths = dossier.get("extracted_text_rel_paths") or []
    for rel_txt in ext_paths[:2]:
        txt_path = (workspace_dir / rel_txt).resolve()
        try:
            txt_path.relative_to(workspace_dir.resolve())
        except ValueError:
            continue
        if not txt_path.exists() or not txt_path.is_file():
            continue
        full_txt = txt_path.read_text(encoding="utf-8", errors="replace")
        head_excerpt = full_txt[:2600].strip()

        mid_excerpt = ""
        if len(full_txt) > 3000:
            search_start = min(len(full_txt) // 5, 2800)
            m = _METHOD_SECTION_RE.search(full_txt, pos=search_start)
            if m:
                start_idx = max(0, m.start() - 120)
                mid_excerpt = full_txt[start_idx : start_idx + 2600].strip()
            else:
                mid_start = len(full_txt) // 3
                mid_excerpt = full_txt[mid_start : mid_start + 2400].strip()

        sections.append(
            f"[Extracted Paper Digest from `{rel_txt}` (total {len(full_txt)} chars)]\n"
            f"--- Opening / Abstract / Intro ---\n{head_excerpt}"
            + (f"\n\n--- Methodology / Experimental / Quantitative Section ---\n{mid_excerpt}" if mid_excerpt else "")
        )

    brief = "\n\n".join(s for s in sections if s).strip()
    if not brief:
        brief = f"Prompt-based paper reproduction target: {str(dossier.get('user_prompt') or '')[:1200]}"
    return brief[:max_chars]
