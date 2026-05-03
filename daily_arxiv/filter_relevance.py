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

    kept, dropped = [], 0
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue
            blob = " ".join(str(item.get(k, "")) for k in ("title", "summary"))
            if matches_any(blob, keywords):
                kept.append(item)
            else:
                dropped += 1

    total = len(kept) + dropped
    print(f"[filter] {total} crawled -> {len(kept)} kept, {dropped} dropped",
          file=sys.stderr)

    if not kept:
        # Don't overwrite with empty file; just signal stop.
        print("[filter] zero matches — workflow will stop", file=sys.stderr)
        return 1

    with target.open("w", encoding="utf-8") as f:
        for item in kept:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"[filter] wrote filtered file: {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[filter] error: {e}", file=sys.stderr)
        sys.exit(2)
