#!/usr/bin/env python3
"""
Regenerate assets/seen_ids.txt — the source of truth for "papers we've
already summarized." Called from the workflow's data-branch publish phase
AFTER all daily files have been merged into ./data/.

A paper is recorded as "seen" only if it appears in an *_AI_enhanced_*.jsonl
file (i.e., it actually got summarized, not just fetched). Each paper
contributes multiple keys so future cross-source matches dedupe correctly:
  - the raw id field as-is ("arxiv:2604.00001", "openalex:W123", "s2:abc")
  - the bare arxiv id with version stripped ("2604.00001")
  - "doi:<doi>" if a DOI is recoverable from the abs URL
"""
import glob
import json
import re
import sys


def main() -> int:
    ids: set[str] = set()
    files = sorted(glob.glob("data/*_AI_enhanced_*.jsonl"))
    print(f"[seen-ids] scanning {len(files)} AI-enhanced files", file=sys.stderr)

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    pid = (item.get("id") or "").strip()
                    if pid:
                        ids.add(pid)
                        bare = re.sub(r"^arxiv:", "", pid, flags=re.I)
                        bare = re.sub(r"v\d+$", "", bare)
                        if bare and bare != pid:
                            ids.add(bare)
                    abs_url = (item.get("abs") or "").lower()
                    if "doi.org/" in abs_url:
                        doi = abs_url.split("doi.org/", 1)[1].split("?")[0].rstrip("/")
                        if doi:
                            ids.add(f"doi:{doi}")
        except Exception as e:
            print(f"[seen-ids] WARN: skipping {path}: {e}", file=sys.stderr)

    with open("assets/seen_ids.txt", "w", encoding="utf-8") as f:
        for x in sorted(ids):
            f.write(x + "\n")
    print(f"[seen-ids] wrote {len(ids)} unique paper IDs to assets/seen_ids.txt",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
