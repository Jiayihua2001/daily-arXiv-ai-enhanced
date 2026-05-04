#!/usr/bin/env python3
"""
Drop papers we've already screened+summarized on a previous day.

Without this step, with LOOKBACK_DAYS=3 (and arXiv not posting on weekends),
the same paper appears in fetch results for several consecutive days, so we
were paying LLM tokens to re-screen and re-summarize it every time. This
script fetches `assets/seen_ids.txt` from the data branch (a flat list of
every paper ID we've ever summarized) and removes already-seen items from
today's file before any expensive step runs.

Position in the workflow:  fetch_all → check_stats → filter_relevance →
                           >>> dedup_history (this script) <<< → screen →
                           enhance → convert

Exit codes (mirror filter_relevance.py and screen.py):
  0 = new papers remain, continue
  1 = no new papers (every fetch result was already processed) — workflow
      should stop gracefully without re-publishing yesterday's data
  2 = hard error
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


# Where to fetch the seen-IDs index from. Auto-detected from the workflow
# context when run in CI; can be overridden with DATA_BRANCH_RAW_BASE.
def _data_branch_raw_base() -> str:
    explicit = os.environ.get("DATA_BRANCH_RAW_BASE")
    if explicit:
        return explicit.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY")  # e.g. "Jiayihua2001/daily-arXiv-ai-enhanced"
    branch = os.environ.get("DATA_BRANCH", "data")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{branch}"
    # Local-dev fallback to the actual user repo we know about.
    return "https://raw.githubusercontent.com/Jiayihua2001/daily-arXiv-ai-enhanced/data"


def load_seen_from_remote() -> set[str]:
    base = _data_branch_raw_base()
    url = f"{base}/assets/seen_ids.txt"
    try:
        r = requests.get(url, timeout=20)
    except Exception as e:
        print(f"[dedup-history] WARN: fetch {url} failed ({e}); "
              f"treating history as empty (will reprocess)", file=sys.stderr)
        return set()
    if r.status_code == 404:
        print(f"[dedup-history] no seen_ids.txt yet (first run on this branch?)",
              file=sys.stderr)
        return set()
    if r.status_code != 200:
        print(f"[dedup-history] WARN: GET {url} -> {r.status_code}; "
              f"treating history as empty", file=sys.stderr)
        return set()
    seen = {ln.strip() for ln in r.text.splitlines() if ln.strip()}
    print(f"[dedup-history] loaded {len(seen)} historical IDs from {url}",
          file=sys.stderr)
    return seen


def _norm_id(item: dict) -> set[str]:
    """Return the set of IDs that should match this paper against history.
    A paper is "the same" if any of:
      - exact id field
      - arxiv base id (strip 'arxiv:' prefix and version suffix)
      - DOI (extracted from abs URL)
    """
    keys: set[str] = set()
    pid = (item.get("id") or "").strip()
    if pid:
        keys.add(pid)
        # If id is 'arxiv:2604.00001v2' or '2604.00001', also add bare base.
        bare = re.sub(r"^arxiv:", "", pid, flags=re.I)
        bare = re.sub(r"v\d+$", "", bare)
        if bare and bare != pid:
            keys.add(bare)
    abs_url = (item.get("abs") or "").lower()
    if "doi.org/" in abs_url:
        doi = abs_url.split("doi.org/", 1)[1].split("?")[0].rstrip("/")
        if doi:
            keys.add(f"doi:{doi}")
    return keys


def main() -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    candidates = [Path(f"../data/{today}.jsonl"), Path(f"data/{today}.jsonl")]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        print(f"[dedup-history] no input file for {today}", file=sys.stderr)
        return 1

    seen = load_seen_from_remote()

    keep, dropped_dupes = [], 0
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys = _norm_id(item)
            if keys & seen:
                dropped_dupes += 1
                continue
            keep.append(item)

    total = len(keep) + dropped_dupes
    print(f"[dedup-history] {total} input -> {len(keep)} new "
          f"({dropped_dupes} already-processed)", file=sys.stderr)

    if not keep:
        print("[dedup-history] zero new papers — workflow will stop "
              "(today's run is a no-op)", file=sys.stderr)
        return 1

    with target.open("w", encoding="utf-8") as f:
        for item in keep:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"[dedup-history] wrote {target} ({len(keep)} new papers)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[dedup-history] error: {e}", file=sys.stderr)
        sys.exit(2)
