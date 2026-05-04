#!/usr/bin/env python3
"""
Topical pre-filter (MCSP × AI4Sci): keep only papers whose title or
abstract substring-matches at least one keyword from keywords.yaml.

Runs in the workflow between the dedup check and the AI enhancement
step, so we never spend LLM tokens on irrelevant papers.

Exit codes (mirror check_stats.py so the workflow can chain):
  0 = papers remain after filtering -> continue
  1 = no papers remain (or no input) -> stop workflow gracefully
  2 = error
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    print("[filter] PyYAML not installed; skipping keyword filter", file=sys.stderr)
    sys.exit(0)


def load_keywords(path: Path) -> list[str]:
    if not path.exists():
        print(f"[filter] keywords.yaml not found at {path}; no filter applied",
              file=sys.stderr)
        return []
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    kws = cfg.get("keywords") or []
    return [str(k).strip().lower() for k in kws if str(k).strip()]


def matches_any(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    t = text.lower()
    return any(k in t for k in keywords)


# Softfloor: when keyword matches alone produce too few papers, fall back
# to scoring by source/category and keeping the top N. Better to LLM-summarize
# 20 mostly-relevant papers than publish 0–3 strict matches.
SOFTFLOOR_MIN = 20

# Source/category preference ordering (higher = more likely to be in-scope).
def _heuristic_score(item: dict) -> float:
    score = 0.0
    src = (item.get("source") or "").lower()
    if src == "arxiv":
        score += 1.0  # arxiv is already category-gated upstream
    elif src == "openalex":
        score += 0.7  # openalex is already keyword-gated upstream
    cats = " ".join(str(c) for c in (item.get("categories") or [])).lower()
    for hint, w in (
        ("cond-mat.mtrl-sci", 1.5),
        ("physics.chem-ph",   1.2),
        ("cond-mat.soft",     1.0),
        ("physics.comp-ph",   1.0),
        ("q-bio.bm",          0.8),
        ("crystal",           1.0),
        ("polymorph",         1.0),
        ("materials",         0.5),
        ("chemistry",         0.4),
        ("cs.lg",            -0.3),  # noisy without other signals
        ("cs.ai",            -0.3),
    ):
        if hint in cats:
            score += w
    return score


def main() -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    # When invoked from the workflow we cd into daily_arxiv/, so resolve
    # both relative paths.
    candidates = [
        Path(f"../data/{today}.jsonl"),
        Path(f"data/{today}.jsonl"),
    ]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        print(f"[filter] no input file for {today}", file=sys.stderr)
        return 1

    kw_path = Path(__file__).parent / "keywords.yaml"
    keywords = load_keywords(kw_path)
    print(f"[filter] loaded {len(keywords)} keywords from {kw_path}",
          file=sys.stderr)

    all_items, kept, dropped = [], [], []
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            all_items.append(item)
            blob = " ".join(str(item.get(k, "")) for k in ("title", "summary"))
            if matches_any(blob, keywords):
                kept.append(item)
            else:
                dropped.append(item)

    total = len(all_items)
    print(f"[filter] {total} crawled -> {len(kept)} keyword-matched, "
          f"{len(dropped)} non-matching", file=sys.stderr)

    # Softfloor: top up with heuristically-scored non-matches if we're below
    # SOFTFLOOR_MIN. Means a sparse-keyword day still publishes ~20 items,
    # which is what makes the daily reading feed feel alive.
    if len(kept) < SOFTFLOOR_MIN and dropped:
        need = SOFTFLOOR_MIN - len(kept)
        scored = sorted(dropped, key=_heuristic_score, reverse=True)
        topup = scored[:need]
        kept.extend(topup)
        print(f"[filter] softfloor: added {len(topup)} heuristically-ranked "
              f"papers to reach {len(kept)} total", file=sys.stderr)

    if not kept:
        print("[filter] zero papers from any source — workflow will stop",
              file=sys.stderr)
        return 1

    with target.open("w", encoding="utf-8") as f:
        for item in kept:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"[filter] wrote filtered file: {target} ({len(kept)} papers)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[filter] error: {e}", file=sys.stderr)
        sys.exit(2)
