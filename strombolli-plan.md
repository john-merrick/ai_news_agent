# AI News Agent — Development Plan

> **Status:** Analysis & planning document. No source code is modified by this plan.
> **Generated:** 2026-06-30
> **Scope:** Architecture review, gap analysis, and a phased improvement roadmap for the `ai_news_agent` repository.

---

## 1. Architecture Overview

`ai_news_agent` is a Python application that builds and delivers a daily AI-news digest to Telegram. Each run follows a linear **fetch → dedupe → filter → rank → enrich → summarize → deliver → evaluate** pipeline orchestrated by `main.py::run_agent()`.

### 1.1 High-level data flow

```
                ┌─────────────────────────── fetchers/ ───────────────────────────┐
                │  rss_fetcher   reddit_fetcher   tavily_fetcher   twitter_fetcher  arxiv_fetcher │
                └───────┬──────────────┬───────────────┬───────────────┬────────────┬──────────┘
                        │              │               │               │            │
                        ▼              ▼               ▼               ▼            ▼
                       all_articles : list[dict]  (title, url, summary, source, published, …)
                        │
                        ▼
        agent/dedup.py  ──► collapse duplicate canonical URLs (keep highest-priority source)
                        │
                        ▼
        agent/url_memory.py ──► drop URLs already digested in the last 7 days (rolling JSON store)
                        │
                        ▼
        agent/enricher.py ──► select_top_articles() (Claude rank, deterministic fallback)
                        │            └► enrich() (Tavily full-text extract for top N)
                        ▼
        agent/summarizer.py ──► summarize_news() (LiteLLM/Claude → Telegram-ready Markdown digest)
                        │
                        ▼
        delivery/telegram.py ──► send_telegram_message() (chunked, Markdown→plain fallback)
                        │
                        ▼
        eval/daily_eval.py ──► evaluate_today() (Haiku binary "useful?" judge, best-effort, non-blocking)
                        │
                        ▼
        main.py ──► logs/last-run.json  (structured status for the cron wrapper's sys-ops alerts)
```

### 1.2 Components & modules

| Layer | File | Responsibility |
|---|---|---|
| **Entry point** | `main.py` | Orchestrates the pipeline, writes `logs/last-run.json`, flushes Langfuse, exposes `--schedule` mode (APScheduler). |
| **Config** | `config.py` | Loads `.env`, defines RSS feed list, keyword filter, research-lab domains, schedule, and source limits. |
| **Fetchers** | `fetchers/rss_fetcher.py` | Polls 11 RSS feeds with per-feed lookback windows + optional title keyword filter. |
| | `fetchers/arxiv_fetcher.py` | Queries ArXiv API (cs.AI/LG/CL, stat.ML), 48h moderation-aware window. |
| | `fetchers/reddit_fetcher.py` | PRAW; top posts from 5 subreddits, min-score gate. Optional (skips without creds). |
| | `fetchers/tavily_fetcher.py` | Tavily news search: general + lab-domain queries, day-of-week rotated. |
| | `fetchers/twitter_fetcher.py` | Twitter/X recent-search API. Optional (skips without bearer token). |
| **Agent logic** | `agent/dedup.py` | Canonical-URL dedup with source-priority ranking. |
| | `agent/url_memory.py` | 7-day rolling URL memory (atomic JSON) to suppress repeats across days. |
| | `agent/enricher.py` | LLM ranking of top-N articles + Tavily full-text extraction; deterministic fallback. |
| | `agent/summarizer.py` | LangChain LLM client factory (LiteLLM-preferred, Anthropic fallback), retry, Langfuse tracing, digest generation. |
| | `agent/prompts.py` | System + user prompt templates for the digest. |
| **Delivery** | `delivery/telegram.py` | Boundary-aware chunking, Markdown-with-plain-text fallback delivery. |
| **Evaluation** | `eval/criteria.py` | Universal + domain + subjective criteria, judge prompts, tolerant JSON parser. |
| | `eval/daily_eval.py` | Per-run binary "useful to reader?" judge + Langfuse dataset persistence. |
| | `eval/run_eval.py` | Weekly canned-dataset regression eval via Langfuse experiments. |
| **Ops** | `bin/cron-run.sh` | Cron wrapper: single-instance lock, secret loading, LiteLLM preflight/restart, sys-ops Telegram alerts. |
| | `bin/refresh-secrets.sh` | Resolves 1Password `op://` refs → `.env.secrets` (mode 600). |
| | `bin/weekly-eval.sh` | Cron wrapper for the weekly eval. |

### 1.3 Technology stack

- **Language:** Python 3.12 (uses `X | None` syntax, `list[dict]` generics).
- **LLM access:** LangChain (`langchain-openai` against a LiteLLM proxy, `langchain-anthropic` as fallback). Default model alias `claude-sonnet`; judge `claude-haiku-4-5`.
- **Observability:** Langfuse v4 (tracing + datasets + scores), optional.
- **Resilience:** `tenacity` for exponential-backoff retries on transient LLM/Tavily errors.
- **Data sources:** `feedparser`, `requests`, `praw`, `tavily-python`, raw ArXiv/Twitter HTTP.
- **Scheduling:** system `cron` (primary, via `bin/cron-run.sh`) or APScheduler (`main.py --schedule`).
- **Secrets:** 1Password CLI (`op inject`) → `.env.secrets`, sourced by cron.

### 1.4 Notable design strengths

- **Soft-fail everywhere:** every fetcher and the eval step swallow their own exceptions and return empty/partial results, so one dead source never breaks the run.
- **Deterministic fallback** for LLM ranking (`_deterministic_top_n`) keeps the pipeline alive when the model is unavailable or returns malformed output.
- **Atomic writes** (`os.replace`) for `last-run.json` and the URL-memory store prevent corruption on interrupted runs.
- **Operational hardening** in `cron-run.sh`: single-instance lock with stale-PID detection, LiteLLM health preflight + auto-restart, and a separate sys-ops alert channel.
- **Structured run status** (`logs/last-run.json`) gives the cron wrapper rich per-run telemetry.

---

## 2. Identified Gaps

### 2.1 Testing (highest priority)

- **No automated tests exist at all.** There is no `tests/` directory, no `test_*.py` files, no `conftest.py`, no `pytest.ini`/`pyproject.toml`, and no CI workflow — yet `pytest` is available in the environment. The "run the existing test suite" step is currently a no-op.
- The codebase contains a large amount of **pure, highly testable logic** that is entirely uncovered:
  - `agent/dedup.py::_canonical_url` / `dedupe` (URL normalization, priority collapsing).
  - `agent/url_memory.py` (7-day windowing, pruning, atomic merge).
  - `delivery/telegram.py::_split_on_boundaries` (chunk-boundary logic).
  - `agent/enricher.py::_parse_indices` / `_deterministic_top_n`.
  - `eval/criteria.py::parse_judge_response` (fence-stripping, fallback extraction).
  - `fetchers/rss_fetcher.py::_title_matches_filter` / `_entry_pub_date`.
- No regression safety net means refactors (like the news-feed cleanup this branch implies) carry real risk.

### 2.2 Correctness bugs

- **`_canonical_url` uses `str.lstrip("www.")` (confirmed bug).** `lstrip` removes any leading characters in the set `{'w', '.'}`, **not** the literal prefix `"www."`. Verified examples:
  - `wired.com` → `ired.com`
  - `washingtonpost.com` → `ashingtonpost.com`
  - `www.example.com` → `example.com` (correct only by coincidence).
  This corrupts canonical hostnames for any domain beginning with `w`, which silently weakens **both** dedup and the 7-day URL memory (the same article can slip through as "unseen").
- **Naive/aware datetime inconsistency.** `rss_fetcher` and `reddit_fetcher` use naive local `datetime.now()`; `arxiv_fetcher` uses tz-aware UTC; `twitter_fetcher` uses the deprecated `datetime.utcnow()`. Lookback cutoffs are therefore computed against mixed time references. It works today only because each fetcher compares within its own frame, but it is fragile and timezone-dependent.
- **URL-only deduplication.** `dedupe` collapses by canonical URL only; the same story published at different URLs (the exact `dedup_required` scenario in `eval/run_eval.py`) survives and relies entirely on the LLM prompt to merge. No title-similarity / near-duplicate pass exists.

### 2.3 Packaging & robustness

- **Missing `__init__.py`** in `agent/`, `fetchers/`, and `delivery/` (only `eval/` has one). Imports work solely because the process runs from the repo root; running from elsewhere or packaging the project would break.
- **No dependency pinning / lockfile.** `requirements.txt` uses range specifiers with no `requirements.lock`/hashes, so builds are not reproducible.
- **Sequential network I/O.** ~11 RSS feeds + ArXiv + Reddit + Tavily + Twitter are fetched one after another, each with up to a 10–20s timeout. Worst-case wall-clock is the sum of all timeouts. No concurrency (`ThreadPoolExecutor`/`asyncio`) and no per-feed caching/ETags.
- **No retry on RSS/ArXiv/Twitter HTTP.** Only the LLM and Tavily-extract calls use `tenacity`; transient network blips on feeds are simply dropped.

### 2.4 Configuration & maintainability

- **Hardcoded operational paths.** `bin/cron-run.sh` hardcodes `LITELLM_COMPOSE_DIR="/Users/isaacboorer/mac-codebase/dev-ops/observability"`, tying the repo to one machine/user.
- **Hardcoded source lists in code.** RSS feeds, subreddits, Tavily query rotations, and research domains live in `config.py`/fetcher modules rather than an external, user-editable config (YAML/TOML). Tuning "news feeds" requires code edits.
- **Magic numbers scattered** (`ENRICH_TOP_N=10`, `MAX_ARTICLES=50`, `MIN_SCORE=50`, `EXTRACT_MAX_CHARS=3000`) without centralization.

### 2.5 Documentation drift

- `README.md` is out of date relative to the actual code:
  - References `fetchers/web_fetcher.py` and **Exa** web search — the repo now uses `fetchers/tavily_fetcher.py` and **Tavily**.
  - The `pip install …` line lists `langchain-core langchain-openai langfuse exa-py … python-telegram-bot` which diverges from `requirements.txt` (no `exa-py`, no `python-telegram-bot`; Telegram is done via raw `requests`).
  - "Scheduling (macOS)" documents a `com.ainewsagent.daily.plist` **launchd** file that does not exist in the repo; the real scheduling path is **cron** via `bin/cron-run.sh`.
  - The "Project structure" tree omits `dedup.py`, `enricher.py`, `url_memory.py`, `arxiv_fetcher.py`, the entire `eval/` package, and `bin/`.

### 2.6 Security & secrets

- Secret handling is generally **good** (1Password injection, `.env.secrets` mode 600, `.gitignore` covers `.env`/`.env.secrets`, bot-token shape validation in `cron-run.sh`). Remaining gaps:
  - **No automated secret scanning** (e.g. `gitleaks`/`trufflehog`) or pre-commit guard to catch an accidental key commit.
  - **No SSRF/URL hardening** on `enrich()` — Tavily extracts arbitrary fetched URLs; low risk given sources, but unvalidated.
  - **No dependency vulnerability scanning** (`pip-audit`/Dependabot).

### 2.7 Observability & quality tooling

- **No linter/formatter/type-checker config** (`ruff`, `black`, `mypy`). Style is consistent by hand but unenforced.
- **No CI** to run tests/lint on push.
- Eval is **best-effort and Langfuse-coupled**; there is no local, assertion-based quality gate that fails a build when digest quality regresses.

### 2.8 Incomplete / latent features

- `MAX_ARTICLES_PER_SOURCE` and `LOOKBACK_HOURS` are read by some fetchers inconsistently (e.g. Tavily general query hardcodes `max_results=20`, bypassing the per-source cap).
- No de-noising of low-signal Reddit/Twitter items beyond a score threshold.
- No persistence/archive of past digests (only the latest `last-run.json`), so trend analysis over time is limited to Langfuse.

---

## 3. Recommendations & Phased Plan

The phases are ordered to **establish a safety net first**, then fix correctness, then improve robustness and maintainability. Every change should preserve the existing soft-fail philosophy.

### Phase 0 — Test harness & safety net *(foundational)*

**Goal:** make the "run the test suite" step meaningful and lock in current behavior before any refactor.

1. Add a `tests/` package and a `pyproject.toml` (or `pytest.ini`) with pytest config and coverage settings.
2. Write **pure-function unit tests** (no network, no LLM) for:
   - `dedup` — canonical URL normalization (including the `www.` cases below) and priority collapsing.
   - `url_memory` — windowing, pruning, atomic merge, malformed-file tolerance.
   - `telegram._split_on_boundaries` — long text, markdown-pair preservation, no-split short text.
   - `enricher._parse_indices` and `_deterministic_top_n`.
   - `criteria.parse_judge_response` — fenced JSON, raw JSON, garbage.
   - `rss_fetcher._title_matches_filter` / `_entry_pub_date`.
3. Add **dev dependencies** (`requirements-dev.txt`): `pytest`, `pytest-cov`, `responses`/`requests-mock` for HTTP fetchers.
4. Add a minimal **GitHub Actions CI** workflow running `pytest` + lint on push/PR.

*Acceptance:* `pytest` runs green with ≥70% coverage on `agent/`, `delivery/`, and `eval/criteria.py`.

### Phase 1 — Correctness fixes *(guarded by Phase 0 tests)*

1. **Fix `_canonical_url`** to strip the literal `www.` prefix (e.g. `host[4:] if host.startswith("www.") else host`). Add regression tests for `wired.com`, `washingtonpost.com`, `www.example.com`.
2. **Standardize on timezone-aware UTC** across all fetchers; replace deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)`; compute all lookback cutoffs in UTC.
3. **Add a near-duplicate pass** (normalized-title similarity, e.g. token-set ratio) on top of URL dedup, so cross-URL duplicate stories collapse before the LLM step.

### Phase 2 — Robustness & performance

1. Add `__init__.py` to `agent/`, `fetchers/`, `delivery/`; make the project importable as a package.
2. **Parallelize fetchers** with a bounded `ThreadPoolExecutor`, preserving per-source soft-fail. Expect a large wall-clock reduction.
3. Extend `tenacity` retries to RSS/ArXiv/Twitter HTTP calls.
4. Add a `requirements.lock` (pinned + hashes) for reproducible installs; wire `pip-audit` into CI.
5. Honor `MAX_ARTICLES_PER_SOURCE` consistently (including Tavily general query).

### Phase 3 — Configuration & maintainability

1. Externalize feeds/subreddits/queries/domains into a `sources.yaml` (or `[tool.ai_news_agent]` in `pyproject.toml`), loaded by `config.py`. Keep current values as defaults.
2. Remove the hardcoded `LITELLM_COMPOSE_DIR` from `cron-run.sh`; make it an env var with a documented default.
3. Centralize magic numbers into `config.py` with env overrides.

### Phase 4 — Documentation & developer experience

1. Rewrite `README.md` to match reality: Tavily (not Exa), correct dependency list, cron (not launchd) scheduling, and a complete project-structure tree including `agent/`, `eval/`, and `bin/`.
2. Add a `CONTRIBUTING.md` / developer setup section (venv, dev deps, running tests).
3. Add `ruff` + `black` + `mypy` configs and a `.pre-commit-config.yaml` (format, lint, secret scan via `gitleaks`).

### Phase 5 — Quality, security & observability hardening

1. Add a **local assertion-based quality gate** (structural checks on the digest: required sections present, length bounds, links well-formed) that can run without Langfuse.
2. Add secret scanning (`gitleaks`) and dependency scanning (Dependabot/`pip-audit`) to CI.
3. Optionally **archive each digest** (date-stamped under `logs/`) to enable local trend analysis independent of Langfuse.
4. Add lightweight SSRF guards/allowlist around `enrich()` URL extraction.

---

## 3a. Test Coverage Assessment

The project ships with **zero automated tests** today. The table below maps the highest-value, network-free units to the test files that Phase 0 should create. "Risk if untested" reflects the likelihood × impact of an undetected regression.

| Module / function | Kind | Suggested test file | Risk if untested |
|---|---|---|---|
| `agent/dedup.py::_canonical_url` | pure | `tests/test_dedup.py` | **High** — confirmed `www.` bug already corrupts keys |
| `agent/dedup.py::dedupe` | pure | `tests/test_dedup.py` | High — silent loss of articles on priority collapse |
| `agent/url_memory.py` (load/save/filter) | pure + tmp file | `tests/test_url_memory.py` | High — repeats or over-suppression of stories |
| `delivery/telegram.py::_split_on_boundaries` | pure | `tests/test_telegram.py` | Medium — broken Markdown → 400 delivery failures |
| `agent/enricher.py::_parse_indices` | pure | `tests/test_enricher.py` | Medium — bad LLM output silently drops ranking |
| `agent/enricher.py::_deterministic_top_n` | pure | `tests/test_enricher.py` | Medium — fallback ordering correctness |
| `eval/criteria.py::parse_judge_response` | pure | `tests/test_criteria.py` | Medium — fenced/garbage JSON handling |
| `fetchers/rss_fetcher.py::_title_matches_filter` | pure | `tests/test_rss.py` | Low — keyword filter correctness |
| `fetchers/rss_fetcher.py::_entry_pub_date` | pure | `tests/test_rss.py` | Low — date parsing across feed variants |
| `agent/summarizer.py::_is_transient_llm_error` | pure | `tests/test_summarizer.py` | Low — retry classification |

**Coverage target for Phase 0:** ≥70% line coverage on `agent/`, `delivery/`, and `eval/criteria.py` using `pytest --cov`. Fetchers that perform network I/O should be tested with `responses`/`requests-mock` to stay hermetic.

### Illustrative test stub (for reference only — not added to the repo by this plan)

```python
# tests/test_dedup.py
from agent.dedup import _canonical_url, dedupe

def test_www_prefix_is_stripped_literally():
    # Regression for the lstrip("www.") bug.
    assert _canonical_url("https://www.example.com/x") == "https://example.com/x"
    assert _canonical_url("https://wired.com/x") == "https://wired.com/x"          # must NOT become ired.com
    assert _canonical_url("https://washingtonpost.com/x").endswith("washingtonpost.com/x")

def test_dedupe_keeps_higher_priority_source():
    items = [
        {"url": "https://a.com/1", "source": "Tavily News"},
        {"url": "https://a.com/1", "source": "ArXiv"},
    ]
    out = dedupe(items)
    assert len(out) == 1 and out[0]["source"] == "ArXiv"
```

---

## 3b. Risk Matrix

| ID | Risk | Likelihood | Impact | Mitigation (phase) |
|---|---|---|---|---|
| R1 | Undetected regression from refactors (no tests) | High | High | Phase 0 test harness + CI |
| R2 | `www.` canonicalization bug weakens dedup/memory | **Occurring now** | Medium | Phase 1 fix + regression test |
| R3 | Timezone/`utcnow()` drift miscounts lookback window | Medium | Medium | Phase 1 UTC normalization |
| R4 | One slow/blocking feed inflates run wall-clock | Medium | Low | Phase 2 parallel fetch + timeouts |
| R5 | Accidental secret commit | Low | High | Phase 4 gitleaks pre-commit + CI |
| R6 | Dependency CVE via unpinned ranges | Medium | Medium | Phase 2 lockfile + `pip-audit` |
| R7 | README drift misleads setup (Exa vs Tavily, launchd vs cron) | **Occurring now** | Low | Phase 4 docs sync |

---

## 4. Suggested Priority Order

| Priority | Item | Rationale |
|---|---|---|
| **P0** | Phase 0 (tests + CI) | No safety net today; prerequisite for safe change. |
| **P0** | `_canonical_url` `www.` fix | Confirmed correctness bug degrading dedup & memory. |
| **P1** | Timezone normalization | Latent, timezone-dependent correctness risk; removes deprecation. |
| **P1** | README/doc sync | Active drift misleads new contributors and setup. |
| **P2** | Fetcher parallelism + retries | Meaningful runtime + reliability win. |
| **P2** | Packaging (`__init__.py`, lockfile) | Reproducibility and import safety. |
| **P3** | Externalized source config | Lets feed tuning happen without code edits. |
| **P3** | Near-duplicate dedup | Quality improvement, depends on Phase 0 tests. |
| **P4** | Lint/format/type/secret tooling | Long-term maintainability. |

---

## 5. Verification Notes for This Plan

- This document is **additive only**: it creates `strombolli-plan.md` and modifies no existing source, configuration, or asset.
- `git status` should show exactly one new untracked/added file: `strombolli-plan.md`.
- The repository currently has **no test suite**, so running tests neither passes nor fails on existing code — establishing one is the first recommended action (Phase 0). The bug noted in §2.2 was confirmed empirically against the existing `agent/dedup.py` logic without modifying it.
