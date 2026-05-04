#!/usr/bin/env python3
"""
Cheap LLM pre-screen — runs BEFORE the expensive 5-field enhance.py.

For each paper, makes one short LLM call to score:
  - relevance     (1-10): how MCSP / AI4Sci relevant is this work?
  - significance  (1-10): how novel/important is the contribution?
  - bucket        (str):  best-fit category (overrides the substring bucket)
  - tldr          (str):  one-line 25-word summary

Then drops papers that fail thresholds. Survivors get carried into the
enhance step with their screen scores attached, so the frontend can sort
by significance and show a tiny score badge.

Why this matters
----------------
With 500+ papers/day post-filter, running the full 5-field summary on
all of them costs ~$0.50/day in tokens AND takes 30-45 minutes of CI
wall time. Screening drops ~70% of items at <10% of the per-paper cost,
leaving ~150 high-quality papers for the expensive step. Net cost ~$0.20,
net wall time ~10-15 min.

Usage
-----
    python screen.py --data ../data/2026-05-04.jsonl

Outputs (next to the input file):
  data/2026-05-04_screened.jsonl          ← survivors with screen scores
  data/2026-05-04_screened_dropped.jsonl  ← reject log (for debugging)

Exit codes mirror filter_relevance.py:
  0 = survivors written, continue
  1 = no survivors (fallback to passing the input through unchanged)
  2 = hard error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import dotenv
from openai import OpenAI
from tqdm import tqdm

if os.path.exists(".env"):
    dotenv.load_dotenv()


# ---------- thresholds (env-overridable) ----------
SCREEN_MIN_RELEVANCE    = int(os.environ.get("SCREEN_MIN_RELEVANCE", "5"))
SCREEN_MIN_SIGNIFICANCE = int(os.environ.get("SCREEN_MIN_SIGNIFICANCE", "4"))
# Always keep a paper that scores >= this on relevance even if significance is low
# (a foundational paper in our area is worth showing even if "incremental").
SCREEN_RELEVANCE_AUTOKEEP = int(os.environ.get("SCREEN_RELEVANCE_AUTOKEEP", "8"))
# Floor: never drop more than 90% of input — if the LLM is being too strict
# we'd rather show borderline papers than nothing.
SCREEN_MAX_DROP_RATIO   = float(os.environ.get("SCREEN_MAX_DROP_RATIO", "0.90"))
SCREEN_MAX_WORKERS      = int(os.environ.get("SCREEN_MAX_WORKERS", "12"))

VALID_BUCKETS = {"Materials", "ML methods", "Chemistry", "Bio", "Physics", "Other"}

SYSTEM_PROMPT = """You evaluate AI-for-science papers for a researcher whose focus is Molecular Crystal Structure Prediction (MCSP) and AI for Science (AI4Sci) — broadly: ML potentials, generative models for molecules/materials, GNNs for chemistry, materials informatics, computational chemistry, foundation models for science.

For each paper, return strict JSON (no prose) with these keys:
{
  "relevance": int 1-10,        // 10 = core MCSP/AI4Sci, 1 = unrelated CS/bio/etc
  "significance": int 1-10,     // 10 = breakthrough/new method, 1 = incremental
  "bucket": one of ["Materials", "ML methods", "Chemistry", "Bio", "Physics", "Other"],
  "tldr": string 15-25 words    // crisp one-line description
}

Scoring guidance:
- relevance 9-10: directly about crystal structure prediction, polymorphs, ML potentials (MACE/NequIP/CHGNet), generative models for molecules/crystals, materials discovery via ML.
- relevance 6-8: adjacent ML-for-chemistry, ML-for-biology with structure focus, foundation models that touch science domains.
- relevance 3-5: generic deep learning that *could* apply to science but the paper is about general ML.
- relevance 1-2: unrelated (vision, NLP, robotics, theory).
- significance 9-10: new SOTA on a major benchmark, novel architecture, big dataset release, paradigm shift.
- significance 6-8: solid empirical contribution, useful method, good benchmark.
- significance 3-5: incremental improvement, narrow application, limited novelty.
- significance 1-2: workshop-level, derivative, or just a re-application of existing methods.
"""

USER_TEMPLATE = """Title: {title}

Abstract: {summary}

Return ONLY the JSON object."""


# ---------- helpers ----------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="JSONL input (post filter_relevance)")
    p.add_argument("--max-workers", type=int, default=SCREEN_MAX_WORKERS)
    return p.parse_args()


def _extract_json_obj(text: str) -> dict:
    """Find and parse the first {...} JSON object in `text`. Mirrors enhance.py."""
    if not text:
        return {}
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            v = json.loads(fenced.group(1))
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start != -1:
                blob = text[start:i+1]
                try:
                    v = json.loads(blob)
                    if isinstance(v, dict):
                        return v
                except Exception:
                    pass
                start = -1
    return {}


def _coerce_score(v: Any, default: int = 0) -> int:
    try:
        return max(1, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _coerce_bucket(v: Any) -> str:
    s = str(v or "").strip()
    if s in VALID_BUCKETS:
        return s
    # Common LLM-output normalizations.
    sl = s.lower()
    for b in VALID_BUCKETS:
        if b.lower() == sl or b.lower() in sl:
            return b
    return "Other"


def screen_one(client: OpenAI, model: str, item: dict) -> dict:
    """Return a screen dict; on error returns a permissive default that keeps the paper."""
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    # Cap abstract length to keep prompt small (and cheap).
    if len(summary) > 1500:
        summary = summary[:1500] + "…"

    user_prompt = USER_TEMPLATE.format(title=title, summary=summary)

    last_text = ""
    for attempt, kwargs_extra in enumerate([
        {"response_format": {"type": "json_object"}},
        {},
    ]):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=200,   # tiny output, capped
                **kwargs_extra,
            )
            text = resp.choices[0].message.content or ""
            last_text = text
            data = _extract_json_obj(text)
            if data:
                return {
                    "relevance":    _coerce_score(data.get("relevance"), default=5),
                    "significance": _coerce_score(data.get("significance"), default=5),
                    "bucket":       _coerce_bucket(data.get("bucket")),
                    "tldr":         str(data.get("tldr") or "")[:300],
                    "ok":           True,
                }
        except Exception as e:
            if not getattr(screen_one, "_logged_first_err", False):
                import traceback
                print(f"[screen] FIRST CALL FAILED — full traceback:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                print(f"[screen] OPENAI_BASE_URL={os.environ.get('OPENAI_BASE_URL','(unset)')} "
                      f"MODEL={model}", file=sys.stderr)
                screen_one._logged_first_err = True
            print(f"[screen] {item.get('id','?')} attempt {attempt+1}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    # All attempts failed: keep the paper with neutral scores so we don't lose it.
    print(f"[screen] {item.get('id','?')}: defaulting (last_text={last_text[:120]!r})",
          file=sys.stderr)
    return {
        "relevance":    5,
        "significance": 5,
        "bucket":       "Other",
        "tldr":         (item.get("title") or "")[:200],
        "ok":           False,
    }


def should_keep(screen: dict) -> bool:
    rel = screen.get("relevance", 5)
    sig = screen.get("significance", 5)
    if rel >= SCREEN_RELEVANCE_AUTOKEEP:
        return True
    return rel >= SCREEN_MIN_RELEVANCE and sig >= SCREEN_MIN_SIGNIFICANCE


def main() -> int:
    args = parse_args()

    in_path = args.data
    if not os.path.exists(in_path):
        print(f"[screen] no input file at {in_path}", file=sys.stderr)
        return 1

    # Read input
    items: list[dict] = []
    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not items:
        print(f"[screen] empty input", file=sys.stderr)
        return 1

    print(f"[screen] {len(items)} candidates; "
          f"thresholds rel>={SCREEN_MIN_RELEVANCE} sig>={SCREEN_MIN_SIGNIFICANCE} "
          f"(autokeep at rel>={SCREEN_RELEVANCE_AUTOKEEP})", file=sys.stderr)

    # OpenAI client
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key  = os.environ.get("OPENAI_API_KEY")
    model    = os.environ.get("MODEL_NAME", "deepseek-chat")
    client_kwargs = {}
    if base_url: client_kwargs["base_url"] = base_url
    if api_key:  client_kwargs["api_key"]  = api_key
    client = OpenAI(**client_kwargs)
    print(f"[screen] model={model} via {base_url or '(default openai)'}", file=sys.stderr)

    # Parallel screen
    results: list[tuple[dict, dict]] = [None] * len(items)  # (item, screen)
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        future_to_idx = {
            pool.submit(screen_one, client, model, item): i
            for i, item in enumerate(items)
        }
        for future in tqdm(as_completed(future_to_idx),
                           total=len(items),
                           desc="Screening", file=sys.stderr):
            i = future_to_idx[future]
            try:
                screen = future.result()
            except Exception as e:
                print(f"[screen] worker exc on {i}: {e}", file=sys.stderr)
                screen = {"relevance": 5, "significance": 5, "bucket": "Other",
                          "tldr": items[i].get("title", "")[:200], "ok": False}
            results[i] = (items[i], screen)

    # Apply thresholds + safety floor
    survivors: list[dict] = []
    dropped:  list[dict] = []
    for item, screen in results:
        # Attach screen to item for downstream use
        item = dict(item)
        item["screen"] = {k: v for k, v in screen.items() if k != "ok"}
        # Override category bucket with the LLM's read of it
        item["categories"] = [screen["bucket"]] + [
            c for c in (item.get("raw_categories") or item.get("categories") or [])
            if c != screen["bucket"]
        ]
        if should_keep(screen):
            survivors.append(item)
        else:
            dropped.append(item)

    # Safety floor: don't drop more than SCREEN_MAX_DROP_RATIO
    max_drop = int(len(items) * SCREEN_MAX_DROP_RATIO)
    if len(dropped) > max_drop:
        # Sort dropped by composite score desc, pull back the highest-scoring rejects
        excess = len(dropped) - max_drop
        dropped.sort(
            key=lambda i: (i["screen"]["relevance"] + i["screen"]["significance"]),
            reverse=True,
        )
        survivors.extend(dropped[:excess])
        dropped = dropped[excess:]
        print(f"[screen] safety floor: pulled {excess} borderline papers back "
              f"(would have dropped {max_drop + excess}/{len(items)} otherwise)",
              file=sys.stderr)

    # Sort survivors by composite (significance first, then relevance) so that
    # enhance.py processes the most important first — useful if a CI run times out.
    survivors.sort(
        key=lambda i: (i["screen"]["significance"], i["screen"]["relevance"]),
        reverse=True,
    )

    # Write outputs
    base = in_path.rsplit(".jsonl", 1)[0]
    out_path = f"{base}_screened.jsonl"
    drop_path = f"{base}_screened_dropped.jsonl"

    # IMPORTANT: also overwrite the original file with survivors so that
    # enhance.py and convert.py (which compute the AI-enhanced filename
    # from the input path) keep working unchanged.
    with open(in_path, "w", encoding="utf-8") as f:
        for item in survivors:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(out_path, "w", encoding="utf-8") as f:
        for item in survivors:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(drop_path, "w", encoding="utf-8") as f:
        for item in dropped:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Summary
    avg_rel = sum(i["screen"]["relevance"]    for i in survivors) / max(1, len(survivors))
    avg_sig = sum(i["screen"]["significance"] for i in survivors) / max(1, len(survivors))
    from collections import Counter
    bucket_counts = Counter(i["screen"]["bucket"] for i in survivors)
    print(f"[screen] {len(items)} → {len(survivors)} kept ({len(dropped)} dropped). "
          f"avg relevance={avg_rel:.1f} significance={avg_sig:.1f}", file=sys.stderr)
    print(f"[screen] survivor buckets: {dict(bucket_counts)}", file=sys.stderr)
    print(f"[screen] wrote {in_path} and {out_path}; rejects → {drop_path}",
          file=sys.stderr)

    return 0 if survivors else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[screen] fatal: {e}", file=sys.stderr)
        sys.exit(2)
