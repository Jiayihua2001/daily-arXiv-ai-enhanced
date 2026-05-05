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

VALID_BUCKETS = {
    "CSP & polymorphs",
    "MLIPs & equivariant NNs",
    "Generative for molecules/crystals",
    "Property prediction & informatics",
    "Foundation models (chemistry/bio)",
    "Other",
}

# Field experts whose work is ALWAYS worth reading. Two groups:
#   MCSP_AUTHORS  - direct CSP / polymorph community → strong direct-relevance signal
#   AI4SCI_AUTHORS - SOTA AI for science whose methods transfer to MCSP
# Last names only (case-insensitive substring match against the authors list).
MCSP_AUTHORS = {
    "marom", "day", "price", "neumann", "hofmann", "tkatchenko", "beran",
    "reilly", "rumson", "hoja", "salimi",
}
AI4SCI_AUTHORS = {
    "csányi", "csanyi", "smidt", "coley", "duvenaud", "welling",
    "barzilay", "leskovec", "jaakkola", "vondrak",
    "batzner", "musaelian", "kovács", "kovacs",  # MACE / NequIP / Allegro folks
    "schütt", "schutt", "klicpera", "gasteiger",  # SchNet / GemNet folks
    "deepmind", "jumper", "abramson",             # AlphaFold-family
}

SYSTEM_PROMPT = """You score arXiv papers for a researcher in Noa Marom's group at Carnegie Mellon. Their core area is FIRST-PRINCIPLES MOLECULAR CRYSTAL STRUCTURE PREDICTION (MCSP) — generating polymorph candidate pools (Genarris), genetic-algorithm search (GAtor), dispersion-DFT ranking (FHI-aims), and increasingly using foundation MLIPs (MACE-OFF, AIMNet2, UMA) to accelerate or replace expensive DFT. They also actively scout SOTA AI-for-science work whose METHODS could TRANSFER to MCSP.

A paper can score high for either reason:
  (A) DIRECT MCSP — about CSP, polymorphs, lattice energy ranking, MLIPs evaluated on organic crystals, blind tests, finite-T corrections, multi-component crystals, flexible molecules, organic semiconductors / pharmaceuticals / energetic materials in CSP context.
  (B) TRANSFER POTENTIAL — methods that are not currently used in MCSP but plausibly should be: foundation MLIPs (MACE family, AIMNet2, UMA, OMat24, GNoME, ANI, ORB, CHGNet, M3GNet, ALIGNN), equivariant / SE(3)-equivariant GNNs, generative diffusion/flow-matching for atoms (CDVAE, DiffCSP, MatterGen, FlowMM, equivariant diffusion), AlphaFold-family structure prediction, foundation models for chemistry (ChemBERTa, MoLFormer, ChemFM), active learning / Bayesian opt for materials, self-supervised pretraining for atomistic systems.

For each paper return ONLY this JSON (no prose):
{
  "relevance": int 1-10,
  "significance": int 1-10,
  "bucket": one of ["CSP & polymorphs", "MLIPs & equivariant NNs", "Generative for molecules/crystals", "Property prediction & informatics", "Foundation models (chemistry/bio)", "Other"],
  "tldr": string 15-30 words,
  "transfer_note": string 10-25 words OR empty   // only if not directly MCSP but transferable; explain WHAT would transfer
}

Relevance ladder (use the HIGHER of direct vs transfer):
  10 = direct CSP work (Marom/Day/Price/Neumann/Hofmann/Tkatchenko/Beran groups, blind tests, polymorph ranking, MLIP-on-organic-crystals)
   8-9 = strong transfer candidate (new foundation MLIP, novel equivariant GNN, generative model for atoms with symmetry, AlphaFold-style structure work) — also award for adjacent crystalline materials work (perovskites, MOFs, COFs)
   6-7 = useful AI4Sci method (property prediction, active learning for chemistry, foundation chem models) without obvious immediate transfer
   3-5 = generic ML touching science but paper itself is about general ML / vision / NLP / RL
   1-2 = unrelated (pure vision, NLP, theory, RL on games, robotics, etc.)

Significance ladder:
  10 = paradigm shift, SOTA on a major benchmark, new dataset/model that the field will use
  7-9 = solid empirical contribution, useful method or benchmark
  4-6 = incremental improvement, narrow application
  1-3 = workshop-level, derivative, or just a re-application of existing methods

Author signal: if any author's last name matches a known MCSP figure (Marom, Day, Price, Neumann, Hofmann, Tkatchenko, Beran, Reilly, Hoja) bump relevance to 10 and significance ≥ 7. If matches an AI4Sci leader (Csányi, Smidt, Coley, Welling, Barzilay, Leskovec, Batzner, Musaelian, Schütt, Klicpera/Gasteiger, Jumper, Abramson) bump relevance to ≥ 8 and significance ≥ 7.

Bucket choice: prefer the most specific that fits. "Other" only when truly unrelated.

`transfer_note` is empty for direct MCSP work and for unrelated papers. Fill it ONLY when relevance >= 7 AND the paper isn't directly MCSP — one sentence on what would transfer to crystal structure prediction.
"""

USER_TEMPLATE = """Title: {title}

Authors: {authors}

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
    # Authors: pass first-authors + last (corresponding) author. Cap at 8
    # names to keep prompt size predictable on giant author lists (CSP
    # blind tests can have 100+ co-authors).
    raw_authors = item.get("authors") or []
    if isinstance(raw_authors, str):
        raw_authors = [a.strip() for a in raw_authors.split(",") if a.strip()]
    if len(raw_authors) > 8:
        authors_str = ", ".join(raw_authors[:5]) + f", … {raw_authors[-1]}"
    else:
        authors_str = ", ".join(raw_authors)

    user_prompt = USER_TEMPLATE.format(
        title=title, authors=authors_str or "(unknown)", summary=summary,
    )

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
                max_tokens=320,   # ~enough for the JSON + transfer_note line
                **kwargs_extra,
            )
            text = resp.choices[0].message.content or ""
            last_text = text
            data = _extract_json_obj(text)
            if data:
                return {
                    "relevance":     _coerce_score(data.get("relevance"), default=5),
                    "significance":  _coerce_score(data.get("significance"), default=5),
                    "bucket":        _coerce_bucket(data.get("bucket")),
                    "tldr":          str(data.get("tldr") or "")[:300],
                    "transfer_note": str(data.get("transfer_note") or "")[:240],
                    "ok":            True,
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

    # All LLM attempts failed. Per user direction: NO heuristic fallback —
    # only real LLM scores are useful. Return a sentinel so this paper is
    # tagged as unscored; the workflow's failure-rate guard catches systemic
    # outages, and per-paper failures keep the paper visible (no badge,
    # sorts to bottom) but we never fabricate scores.
    print(f"[screen] {item.get('id','?')}: LLM failed; no score "
          f"(last_text={last_text[:80]!r})", file=sys.stderr)
    return {
        "relevance":    None,
        "significance": None,
        "bucket":       None,
        "tldr":         None,
        "ok":           False,
    }


def should_keep(screen: dict) -> bool:
    # Unscored papers (LLM call failed): keep them visible — we can't judge,
    # so we don't drop. Frontend won't show a badge and they sort to bottom.
    rel = screen.get("relevance")
    sig = screen.get("significance")
    if rel is None or sig is None:
        return True
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

    # OpenAI client. Bail BEFORE constructing OpenAI() if no key — its
    # constructor raises immediately rather than at first call, and a hard
    # exit at this step would block the entire daily run. Pass-through so
    # enhance.py still gets to summarize what we have.
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key  = os.environ.get("OPENAI_API_KEY")
    model    = os.environ.get("MODEL_NAME", "deepseek-chat")
    if not api_key:
        print("[screen] OPENAI_API_KEY not set — skipping screen step "
              "(passing all papers through unchanged)", file=sys.stderr)
        # Write the input back as the survivors file so downstream filenames work.
        out_path = in_path.rsplit(".jsonl", 1)[0] + "_screened.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        return 0

    client_kwargs = {"api_key": api_key}
    if base_url: client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    print(f"[screen] model={model} via {base_url or '(default openai)'}", file=sys.stderr)

    # Preflight: one tiny LLM call to verify the model + key + base_url
    # combination actually works BEFORE we parallelize 200+ doomed calls.
    # Last-known failure: MODEL_NAME=deepseek-reasoner returned 404 on every
    # one of 278 papers (~20s wasted). This catches it in under 2s.
    try:
        _ = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0.0,
        )
        print(f"[screen] preflight OK", file=sys.stderr)
    except Exception as e:
        err = type(e).__name__
        msg = str(e)
        # Surface the most actionable diagnostic possible.
        print(f"\n{'='*70}\n[screen] PREFLIGHT FAILED — model is unreachable.\n{'='*70}",
              file=sys.stderr)
        print(f"  error type    : {err}", file=sys.stderr)
        print(f"  error message : {msg[:500]}", file=sys.stderr)
        print(f"  MODEL_NAME    : {model!r}", file=sys.stderr)
        print(f"  OPENAI_BASE_URL: {base_url or '(default openai)'}", file=sys.stderr)
        print(f"  OPENAI_API_KEY : {'set (length ' + str(len(api_key)) + ')' if api_key else '(unset)'}",
              file=sys.stderr)
        print(f"\n  Common fixes:", file=sys.stderr)
        print(f"    - Wrong MODEL_NAME for this provider. DeepSeek wants "
              f"`deepseek-chat` (not `deepseek-reasoner` unless you have R1 "
              f"access). OpenAI wants `gpt-4o-mini` etc.", file=sys.stderr)
        print(f"    - OPENAI_BASE_URL pointing at the wrong provider.",
              file=sys.stderr)
        print(f"    - API key for one provider with model name from another.",
              file=sys.stderr)
        print(f"    - Update repo Variables/Secrets at:", file=sys.stderr)
        print(f"      Settings → Secrets and variables → Actions",
              file=sys.stderr)
        print(f"{'='*70}\n", file=sys.stderr)
        return 2

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
                # Worker exception: same handling as a per-paper LLM failure.
                # No fabricated score; let it pass through unscored.
                screen = {"relevance": None, "significance": None,
                          "bucket": None, "tldr": None, "ok": False}
            results[i] = (items[i], screen)

    # Sanity check: if more than half the LLM calls failed, the screen
    # output is meaningless (every paper got the same default 5/5 score)
    # and continuing would silently bypass the screen entirely AND make
    # enhance.py spend tokens on the full set. Abort loudly instead.
    n_failures = sum(1 for _, s in results if not s.get("ok"))
    failure_rate = n_failures / max(1, len(results))
    print(f"[screen] LLM call success: {len(results) - n_failures}/{len(results)} "
          f"(failure rate {failure_rate:.0%})", file=sys.stderr)
    if failure_rate > 0.5:
        print(f"[screen] >50% of LLM calls failed — aborting so the daily "
              f"feed isn't built on garbage scores. Check API key / quota / model name.",
              file=sys.stderr)
        return 2

    # Apply thresholds + safety floor
    survivors: list[dict] = []
    dropped:  list[dict] = []
    for item, screen in results:
        # Attach screen to item for downstream use. Keep `ok` flag so the
        # frontend can tell real LLM scores from no-score (failed) ones.
        item = dict(item)
        item["screen"] = dict(screen)
        # Override category bucket with the LLM's read of it (only if we got one)
        if screen.get("bucket"):
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
        excess = len(dropped) - max_drop
        # Score-based sort; treat None as 0 so unscored papers don't crash.
        dropped.sort(
            key=lambda i: ((i["screen"].get("relevance") or 0)
                           + (i["screen"].get("significance") or 0)),
            reverse=True,
        )
        survivors.extend(dropped[:excess])
        dropped = dropped[excess:]
        print(f"[screen] safety floor: pulled {excess} borderline papers back "
              f"(would have dropped {max_drop + excess}/{len(items)} otherwise)",
              file=sys.stderr)

    # Sort survivors by significance then relevance descending. Unscored
    # papers (None) sort to the bottom so enhance.py prioritizes the
    # high-confidence picks if a CI run times out.
    def _sort_key(i):
        s = i.get("screen") or {}
        # Unscored → -1 sort key so they go AFTER any real scores.
        sig = s.get("significance") if s.get("significance") is not None else -1
        rel = s.get("relevance")    if s.get("relevance")    is not None else -1
        return (sig, rel)
    survivors.sort(key=_sort_key, reverse=True)

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

    # Summary — only average over papers that actually got LLM scores
    scored = [i for i in survivors
              if (i["screen"].get("relevance") is not None
                  and i["screen"].get("significance") is not None)]
    n_scored = len(scored)
    n_unscored = len(survivors) - n_scored
    if n_scored:
        avg_rel = sum(i["screen"]["relevance"]    for i in scored) / n_scored
        avg_sig = sum(i["screen"]["significance"] for i in scored) / n_scored
        print(f"[screen] {len(items)} → {len(survivors)} kept "
              f"({n_scored} LLM-scored, {n_unscored} unscored due to LLM failure, "
              f"{len(dropped)} dropped). "
              f"avg relevance={avg_rel:.1f} significance={avg_sig:.1f}", file=sys.stderr)
    else:
        print(f"[screen] {len(items)} → {len(survivors)} kept "
              f"(all unscored due to LLM failure, {len(dropped)} dropped)",
              file=sys.stderr)
    from collections import Counter
    bucket_counts = Counter(i["screen"].get("bucket") or "(none)" for i in survivors)
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
