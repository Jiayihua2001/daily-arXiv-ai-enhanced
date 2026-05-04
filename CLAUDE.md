# daily-arXiv-ai-enhanced — what this tool does

A **fork** of `dw-dengwei/daily-arXiv-ai-enhanced`, retargeted at **MCSP × AI4Sci** (molecular crystal structure prediction × AI for science). The original was a generic CS-paper crawler; this fork adds multi-source fetching, a topical pre-filter, and a personalized reading UI for Jade.

## Goal in one sentence
Every day, automatically pull new papers in a niche research area, throw away the irrelevant ones, summarize the rest with a  LLM, and serve the result as a static website — all on free GitHub infra, no server.

## End-to-end pipeline (runs daily at 7:00 EST via GitHub Actions)

```
 fetch_all.py        →  check_stats.py    →  filter_relevance.py  →  ai/enhance.py     →  to_md/convert.py  →  static site
 (multi-source         (dedup vs            (drop papers whose      (LLM: tldr,           (per-day .md          (HTML/JS reads
  crawl — arxiv +       yesterday's          title+abstract miss     motivation,          grouped by            data branch's
  openalex, optional    file; exit 1 if      every keyword, or relevent meaning;          method, result,      arXiv category)       JSONL files,
  chemrxiv/biorxiv any other you think necessary)     nothing new)         exit 1 if zero left)    conclusion as JSON)                        renders cards)
```

Outputs land on the `data` branch as `data/<YYYY-MM-DD>.jsonl` and `data/<YYYY-MM-DD>_AI_enhanced_<lang>.jsonl`; the `main` branch hosts the static frontend served by GitHub Pages.

## Components

### Backend (Python, runs in CI)
- **[daily_arxiv/fetch_all.py](daily_arxiv/fetch_all.py)** — replaces the old Scrapy spider. One process, three source modules (arXiv API, OpenAlex, ChemRxiv), unified JSONL schema with a `source` field. Looks back `LOOKBACK_DAYS` days. Has a legacy Scrapy spider in [daily_arxiv/daily_arxiv/spiders/arxiv.py](daily_arxiv/daily_arxiv/spiders/arxiv.py) still used by [run.sh](run.sh) for local testing.
- **[daily_arxiv/daily_arxiv/check_stats.py](daily_arxiv/daily_arxiv/check_stats.py)** — diff today vs. yesterday by paper ID, drop dupes. Exit codes 0/1/2 are how the pipeline decides whether to keep going.
- **[daily_arxiv/filter_relevance.py](daily_arxiv/filter_relevance.py)** + **[daily_arxiv/keywords.yaml](daily_arxiv/keywords.yaml)** — substring match on title+abstract against ~130 hand-curated MCSP/AI4Sci keywords (5 tiers, from `crystal structure prediction` down to specific model names like `MACE`, `NequIP`). The cost-saver: drops the long tail of `cs.LG` noise *before* spending LLM tokens.
- **[ai/enhance.py](ai/enhance.py)** — calls an OpenAI-compatible chat endpoint (defaults to DeepSeek) and asks for a JSON object with `tldr/motivation/method/result/conclusion`. Has its own JSON extractor (`_extract_json_obj`) because LangChain's structured output kept failing silently. Also: posts each summary to `spam.dw-dengwei.workers.dev` for sensitive-content filtering, and tries to extract a GitHub URL + star count from the abstract.
- **[to_md/convert.py](to_md/convert.py)** — groups papers by primary category, renders one Markdown file per day from [to_md/paper_template.md](to_md/paper_template.md).

### Frontend (static, served by GitHub Pages from `main`)
- **[index.html](index.html) / [settings.html](settings.html) / [statistic.html](statistic.html) / [login.html](login.html)** — vanilla JS reading app. Loads `assets/file-list.txt` to discover available days, fetches each day's JSONL from the `data` branch via `js/data-config.js`.
- **Auth:** SHA-256 hash of `ACCESS_PASSWORD` is injected into `js/auth-config.js` at build time; client-side gate.
- **Personalization:** user keywords/authors stored in localStorage (privacy-preserving), used to highlight matching papers.

### Skill packaging
- **[SKILL/](SKILL/)** — exposes the published JSON feed as a tool: `bash SKILL/scripts/fetch.sh "https://.../?category=cs.CV&keywords=foo"`. Uses puppeteer because the site needs JS execution to assemble the response.

## Configuration surface

| Where | Variable | Purpose |
|---|---|---|
| Repo Secrets | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ACCESS_PASSWORD`, `TOKEN_GITHUB` | LLM creds, site password, GitHub API for star counts |
| Repo Variables | `CATEGORIES`, `LANGUAGE`, `MODEL_NAME`, `EMAIL`, `NAME`, `LOOKBACK_DAYS`, `SOURCES` | What to fetch, how to summarize, who commits |
| In-repo | [daily_arxiv/keywords.yaml](daily_arxiv/keywords.yaml), [daily_arxiv/config.yaml](daily_arxiv/config.yaml) | Topical filter list, default categories |

## Two-branch model
- `main` — code + frontend HTML/JS. Workflow commits `data-config.js` updates here.
- `data` — only `data/*.jsonl` and `assets/file-list.txt`. Keeps `main` history clean and avoids bloating clones.

## What's noteworthy / fragile

- The fetch-step has **two parallel implementations**: `fetch_all.py` (used in CI) and the old Scrapy spider (used by `run.sh`). They diverged.
- `enhance.py` is `~370` lines because it had to work around LangChain + DeepSeek incompatibilities — the JSON parsing is now hand-rolled.
- The pre-filter is pure substring match — no stemming, no negation, no embedding similarity. Tier 5 keywords like `DFT calculation` will let in papers about deep-fake transformers if they happen to mention the phrase.
- The pipeline is **all-or-nothing per day**: one bad LLM call returns a `"Summary generation failed"` row that still gets published.
- Sensitive-content checking calls a third-party Cloudflare worker (`spam.dw-dengwei.workers.dev`) maintained by the upstream author; if it goes down, every paper is dropped (`return True` on error).
- Frontend reads from a hardcoded data branch via raw GitHub URLs — no CDN, can be slow.
