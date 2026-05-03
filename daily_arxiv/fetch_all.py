#!/usr/bin/env python3
"""
Fetch today's papers from multiple preprint / paper-aggregator sources.

Replaces the scrapy spider with three small, independent source modules
that all hit official, programmatic-friendly APIs (no HTML scraping).
Each source emits items in a common JSONL schema:

    {
      "id":         <stable identifier, source-prefixed for non-arxiv>,
      "categories": [<list of category labels>],
      "pdf":        <pdf URL or null>,
      "abs":        <abstract / landing page URL>,
      "authors":    [<author names>],
      "title":      <title>,
      "comment":    <optional comment>,
      "summary":    <abstract text>,
      "source":     "arxiv" | "chemrxiv" | "openalex"
    }

Output: data/<today>.jsonl in the repo root (relative path "../data" since
this script is invoked with daily_arxiv/ as the cwd, matching the rest of
the pipeline).

Environment variables (all optional, with sensible defaults):
    CATEGORIES   comma-separated arxiv category codes
    LOOKBACK_DAYS  how many days back to consider "today" — default 1
    OPENALEX_EMAIL email for OpenAlex polite pool — default zefengc@andrew.cmu.edu
    KEYWORDS_YAML  path to keywords.yaml — default ./keywords.yaml
    SOURCES        comma-separated subset of arxiv,chemrxiv,openalex,biorxiv
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

import requests

# ----- config --------------------------------------------------------------
DEFAULT_CATEGORIES = (
    "cond-mat.mtrl-sci,physics.chem-ph,physics.comp-ph,cond-mat.soft,"
    "cs.LG,cs.AI,q-bio.BM"
)
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))
OPENALEX_EMAIL = os.environ.get("OPENALEX_EMAIL", "zefengc@andrew.cmu.edu")
SOURCES = [
    s.strip().lower()
    for s in os.environ.get("SOURCES", "arxiv,openalex").split(",")
    if s.strip()
]
USER_AGENT = (
    "daily-arXiv-ai-enhanced/1.0 "
    "(+https://github.com/Jiayihua2001/daily-arXiv-ai-enhanced; "
    f"mailto:{OPENALEX_EMAIL})"
)

# Keywords (used by ChemRxiv + OpenAlex search; arxiv uses categories instead).
def load_keywords() -> list[str]:
    path = Path(os.environ.get("KEYWORDS_YAML",
                               Path(__file__).parent / "keywords.yaml"))
    if not path.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]


# ============================================================================
# arXiv — official API
# ============================================================================

def fetch_arxiv() -> list[dict]:
    """Use the arxiv pip package (export.arxiv.org/api wrapper)."""
    import arxiv

    cats = [c.strip() for c in os.environ.get("CATEGORIES", DEFAULT_CATEGORIES).split(",") if c.strip()]
    if not cats:
        return []

    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=5)
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    # Single query for all categories — much faster than one-per-cat.
    cat_clause = " OR ".join(f"cat:{c}" for c in cats)
    search = arxiv.Search(
        query=cat_clause,
        max_results=600,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    items: list[dict] = []
    seen_ids: set[str] = set()
    for paper in client.results(search):
        if paper.published.replace(tzinfo=timezone.utc) < cutoff:
            break
        aid = paper.entry_id.rsplit("/", 1)[-1]
        if aid in seen_ids:
            continue
        seen_ids.add(aid)
        items.append({
            "id":         aid,
            "categories": list(paper.categories),
            "pdf":        f"https://arxiv.org/pdf/{aid}",
            "abs":        f"https://arxiv.org/abs/{aid}",
            "authors":    [a.name for a in paper.authors],
            "title":      _collapse_ws(paper.title),
            "comment":    paper.comment,
            "summary":    _collapse_ws(paper.summary),
            "source":     "arxiv",
        })
    return items


# ============================================================================
# ChemRxiv — direct API blocked by Cloudflare bot protection.
# OpenAlex indexes ChemRxiv content (DOI prefix 10.26434), so we get
# ChemRxiv coverage transparently via the openalex source.
# Keeping the function for parity / future use behind an opt-in SOURCES var.
# ============================================================================

def fetch_chemrxiv(keywords: list[str]) -> list[dict]:
    """ChemRxiv public API. Note: as of 2026-05, Cloudflare blocks
    automated access from CI — this function will likely return 0 items
    unless run from a residential IP with cookies. Prefer the OpenAlex
    source for ChemRxiv coverage."""
    base = "https://chemrxiv.org/engage/chemrxiv/public-api/v1/items"
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    items: list[dict] = []
    # ChemRxiv has no built-in date filter, so we page through "latest" and
    # stop once we cross the cutoff.
    skip = 0
    page_size = 50
    max_pages = 10
    for _ in range(max_pages):
        try:
            r = requests.get(
                base,
                params={"limit": page_size, "skip": skip, "sort": "PUBLISHED_DATE_DESC"},
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"[chemrxiv] page failed: {e}", file=sys.stderr)
            break

        page = r.json()
        results = page.get("itemHits") or page.get("data") or []
        if not results:
            break

        any_in_window = False
        for hit in results:
            it = hit.get("item") or hit
            published = it.get("publishedDate") or it.get("createdDate")
            if not published:
                continue
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                continue
            if pub_dt < cutoff:
                continue
            any_in_window = True

            doi = it.get("doi") or ""
            cid = it.get("id") or doi
            authors = [a.get("firstName", "") + " " + a.get("lastName", "")
                       for a in (it.get("authors") or [])]
            authors = [a.strip() for a in authors if a.strip()]
            categories = [c.get("name") for c in (it.get("categories") or []) if c.get("name")]

            items.append({
                "id":         f"chemrxiv:{cid}",
                "categories": categories or ["chemistry"],
                "pdf":        it.get("asset", {}).get("original", {}).get("url"),
                "abs":        f"https://doi.org/{doi}" if doi else f"https://chemrxiv.org/engage/chemrxiv/article-details/{cid}",
                "authors":    authors,
                "title":      _collapse_ws(it.get("title") or ""),
                "comment":    None,
                "summary":    _collapse_ws(it.get("abstract") or ""),
                "source":     "chemrxiv",
            })

        # If a whole page was outside the window, we've gone past it.
        if not any_in_window:
            break
        skip += page_size

    # Apply keyword filter to keep this source narrow (ChemRxiv has lots of
    # non-MCSP chemistry like organic synthesis we don't want).
    if keywords:
        items = [p for p in items if _matches_keywords(p, keywords)]
    return items


# ============================================================================
# OpenAlex — covers paywalled journals via title+abstract metadata
# ============================================================================

def fetch_openalex(keywords: list[str]) -> list[dict]:
    """
    OpenAlex /works endpoint. Free, no auth; mailto in UA puts us in the
    'polite pool' (faster, more generous rate limits).

    Strategy: search for each keyword in titles/abstracts, restricted to
    papers published in the last LOOKBACK_DAYS days. Keep type=article and
    type=preprint. Dedupe by DOI/id.
    """
    if not keywords:
        return []

    base = "https://api.openalex.org/works"
    today = datetime.now(timezone.utc).date()
    from_date = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # OpenAlex accepts a search filter that does fielded title+abstract search.
    # We OR together a small set of strong-signal keywords (substring search
    # would explode the query length); pick the most specific ones.
    strong = [k for k in keywords if any(t in k.lower() for t in (
        "crystal", "polymorph", "lattice", "cocrystal", "co-crystal",
        "molecular packing", "perovskite", "interatomic", "force field",
        "metadynamics", "ab initio", "density functional",
        "machine learning potential", "neural network potential",
    ))]
    if not strong:
        strong = keywords[:8]
    # OpenAlex search filter accepts ' OR ' between phrases.
    search = " OR ".join(f'"{k}"' for k in strong[:12])

    items: list[dict] = []
    seen: set[str] = set()
    cursor = "*"
    pages = 0
    max_pages = 4  # ~ 4 * 50 = 200 papers max; usually plenty
    while cursor and pages < max_pages:
        params = {
            "search": search,
            "filter": f"from_publication_date:{from_date},type:article|preprint",
            "per-page": "50",
            "cursor": cursor,
            "mailto": OPENALEX_EMAIL,
        }
        try:
            r = requests.get(
                base, params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"[openalex] page {pages} failed: {e}", file=sys.stderr)
            break

        body = r.json()
        for w in body.get("results", []):
            wid = w.get("id") or ""
            if wid in seen:
                continue
            seen.add(wid)

            # Reconstruct abstract from inverted index (OpenAlex stores it that way).
            inv = w.get("abstract_inverted_index") or {}
            abstract = _abstract_from_inverted_index(inv)
            if not (w.get("title") or abstract):
                continue

            authors = [au["author"]["display_name"]
                       for au in (w.get("authorships") or [])
                       if au.get("author")]
            host = (w.get("primary_location") or {}).get("source") or {}
            venue = host.get("display_name") or ""
            doi   = w.get("doi") or ""
            pdf   = (w.get("primary_location") or {}).get("pdf_url")
            land  = (w.get("primary_location") or {}).get("landing_page_url") or doi

            items.append({
                "id":         f"openalex:{wid.rsplit('/', 1)[-1]}",
                "categories": ([venue] if venue else []) + (
                    [c["display_name"] for c in (w.get("concepts") or []) if c.get("score", 0) > 0.3][:5]
                ),
                "pdf":        pdf,
                "abs":        land or doi or wid,
                "authors":    authors,
                "title":      _collapse_ws(w.get("title") or ""),
                "comment":    venue or None,
                "summary":    _collapse_ws(abstract),
                "source":     "openalex",
            })

        cursor = (body.get("meta") or {}).get("next_cursor")
        pages += 1
    return items


def _abstract_from_inverted_index(inv: dict) -> str:
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


# ============================================================================
# bioRxiv — optional, off by default
# ============================================================================

def fetch_biorxiv(keywords: list[str]) -> list[dict]:
    """
    bioRxiv 'details' API returns recent posts. We pull the last N days,
    then optionally filter by keywords.
    """
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    end   = today.isoformat()

    items: list[dict] = []
    cursor = 0
    while True:
        url = f"https://api.biorxiv.org/details/biorxiv/{start}/{end}/{cursor}"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"[biorxiv] page {cursor} failed: {e}", file=sys.stderr)
            break
        body = r.json()
        coll = body.get("collection") or []
        if not coll:
            break
        for it in coll:
            doi = it.get("doi") or ""
            authors = [a.strip() for a in (it.get("authors") or "").split(";") if a.strip()]
            items.append({
                "id":         f"biorxiv:{doi}",
                "categories": [it.get("category") or "biology"],
                "pdf":        f"https://www.biorxiv.org/content/{doi}.full.pdf" if doi else None,
                "abs":        f"https://doi.org/{doi}" if doi else None,
                "authors":    authors,
                "title":      _collapse_ws(it.get("title") or ""),
                "comment":    None,
                "summary":    _collapse_ws(it.get("abstract") or ""),
                "source":     "biorxiv",
            })
        msg = (body.get("messages") or [{}])[0]
        total = int(msg.get("total", 0))
        cursor += len(coll)
        if cursor >= total:
            break

    if keywords:
        items = [p for p in items if _matches_keywords(p, keywords)]
    return items


# ============================================================================
# Helpers + main
# ============================================================================

_WS = re.compile(r"\s+")
def _collapse_ws(s: str) -> str:
    return _WS.sub(" ", (s or "")).strip()

def _matches_keywords(paper: dict, keywords: list[str]) -> bool:
    blob = (paper.get("title", "") + " " + paper.get("summary", "")).lower()
    return any(k.lower() in blob for k in keywords)


def dedupe(items: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = (it.get("title") or "").strip().lower()[:200]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = Path("..") / "data" / f"{today}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    keywords = load_keywords()
    print(f"[fetch] sources={SOURCES} lookback_days={LOOKBACK_DAYS} "
          f"keywords_loaded={len(keywords)}", file=sys.stderr)

    runners = {
        "arxiv":    lambda: fetch_arxiv(),
        "chemrxiv": lambda: fetch_chemrxiv(keywords),
        "openalex": lambda: fetch_openalex(keywords),
        "biorxiv":  lambda: fetch_biorxiv(keywords),
    }

    all_items: list[dict] = []
    per_source: dict[str, int] = {}
    for s in SOURCES:
        runner = runners.get(s)
        if not runner:
            print(f"[fetch] unknown source '{s}', skipping", file=sys.stderr)
            continue
        try:
            got = runner()
        except Exception as e:
            print(f"[fetch] {s} crashed: {e}", file=sys.stderr)
            got = []
        per_source[s] = len(got)
        print(f"[fetch] {s}: {len(got)} items", file=sys.stderr)
        all_items.extend(got)

    deduped = dedupe(all_items)
    print(f"[fetch] total={len(all_items)} deduped={len(deduped)} "
          f"per_source={per_source}", file=sys.stderr)

    with out_path.open("w", encoding="utf-8") as f:
        for it in deduped:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    print(f"[fetch] wrote {out_path}", file=sys.stderr)
    return 0 if deduped else 1


if __name__ == "__main__":
    sys.exit(main())
