# AI News Agent — Development Plan (`strombolli-plan.md`)

> **Status:** Analysis & planning document only. This plan does **not** modify, rename, or delete any existing source, configuration, or documentation file. It is purely additive.
> **Generated:** 2026-06-30
> **Repository:** `ai_news_agent` (branch analysed: `stromboli/…-action-plan-md`)
> **Scope:** Current-state architecture review, gap & technical-debt analysis, and a concrete, phased improvement roadmap.

---

## 1. Executive Summary

`ai_news_agent` is a single-purpose Python application that assembles a daily AI-news digest and delivers it to a Telegram chat. It fetches from five sources (RSS, Reddit, Tavily web search, Twitter/X, ArXiv), deduplicates and filters against a 7-day URL memory, uses Claude to rank and summarise the most newsworthy items, then ships a Markdown message to Telegram. A best-effort LLM-as-judge eval scores each digest, with optional Langfuse tracing and LiteLLM cost routing.

The codebase is **small, readable, and thoughtfully defensive** in its runtime paths (atomic writes, retries on transient LLM errors, graceful per-source soft-fails, Markdown→plain-text fallback for delivery). The largest gaps are **structural rather than behavioural**: there is **no automated test suite**, **no CI**, **no linting/type-checking**, the **README has drifted from the code**, and several **operational paths are hard-coded to a single developer machine**. There is also at least one **real timezone-correctness bug** in date filtering.

This plan prioritises closing the safety net (tests + CI) first, then correctness fixes, then maintainability and scalability improvements — each phase independently shippable.

---

## 2. Current Architecture

### 2.1 Pipeline (orchestrated by `main.py::run_agent()`)

```
fetchers/*  ──►  dedupe  ──►  url_memory filter  ──►  rank (top-N)  ──►  enrich  ──►  summarize  ──►  deliver  ──►  eval
 (5 sources)     dedup.py      url_memory.py        enricher.py       enricher    summarizer    telegram    daily_eval
```

| Stage | Module | Responsibility |
|---|---|---|
| Fetch | `fetchers/rss_fetcher.py`, `reddit_fetcher.py`, `tavily_fetcher.py`, `twitter_fetcher.py`, `arxiv_fetcher.py` | Collect `list[dict]` articles `{title, url, summary, source, published, …}` from each source. Each source soft-fails independently. |
| Dedupe | `agent/dedup.py` | Collapse duplicate canonical URLs, keeping the highest-priority source (ArXiv > Lab blog > RSS > Reddit > Twitter > Tavily). |
| Memory | `agent/url_memory.py` | Drop URLs already digested in a rolling 7-day JSON window (`logs/seen-urls.json`), atomic write via `os.replace`. |
| Rank | `agent/enricher.py::select_top_articles` | Claude picks the top-N indices; deterministic fallback (priority + recency) when the LLM fails or returns malformed JSON. |
| Enrich | `agent/enricher.py::enrich` | Tavily full-text extract for the selected articles; per-article soft-fail. |
| Summarize | `agent/summarizer.py` | LiteLLM (preferred) or direct Anthropic; tenacity retry on transient errors; optional Langfuse callback. |
| Deliver | `delivery/telegram.py` | Chunk on paragraph/line/word boundaries; Markdown with plain-text fallback. |
| Evaluate | `eval/daily_eval.py` | Haiku binary "useful?" judge; best-effort, never blocks the run; optional Langfuse dataset item + score. |
| Status | `main.py::_atomic_write_json` | Writes `logs/last-run.json` for the cron wrapper's sys-ops alerts. |

### 2.2 Operational layer

- **Config:** `config.py` loads `.env` via `python-dotenv`; centralises feeds, query rotation, keyword filters, and tunables (`LOOKBACK_HOURS`, `MAX_ARTICLES_PER_SOURCE`, schedule).
- **Scheduling:** `bin/cron-run.sh` is a hardened cron wrapper — `PATH` repair, single-instance `mkdir` lock with stale-PID detection, LiteLLM preflight + auto-restart, and structured sys-ops Telegram alerts. `bin/refresh-secrets.sh` resolves 1Password `op://` references into `.env.secrets` (mode 600) so cron never triggers a biometric prompt. `bin/weekly-eval.sh` runs the canned-dataset regression eval.
- **Observability:** Optional Langfuse v4 tracing; LiteLLM proxy for cost tracking. Both degrade gracefully when unconfigured.
- **Eval suite:** `eval/run_eval.py` runs a Langfuse `run_experiment` over hand-crafted `DATASET_ITEMS` with weighted LLM-as-judge criteria from `eval/criteria.py`.

### 2.3 Strengths worth preserving

- Clear separation of concerns (fetch / transform / summarise / deliver / eval).
- Defensive runtime: atomic writes, soft-fail per source and per article, retries on transient LLM errors, deterministic ranking fallback, delivery fallback.
- Good operational hygiene in the cron wrapper (locking, preflight, alerting).
- Sensible source-priority and cross-day de-duplication design.

---

## 3. Gap & Technical-Debt Analysis

### 3.1 Critical — Safety net is absent

| ID | Gap | Evidence | Impact |
|---|---|---|---|
| **C1** | **No automated tests.** `pytest --collect-only` collects **0 tests**; there are no `test_*.py`, `conftest.py`, or test config files. | Repo scan | Pure, highly testable logic (URL canonicalisation, dedupe priority, 7-day memory windowing, Telegram chunk boundaries, `_parse_indices`, `parse_judge_response`, RSS date cutoff) is entirely unguarded. Any refactor risks silent regressions. |
| **C2** | **No CI/CD.** No `.github/workflows`, no pipeline running lint/type/tests on push. | Repo scan | Drift and breakage go undetected until the 7 AM cron run fails in production. |
| **C3** | **No linting or type-checking.** No `ruff`/`flake8`/`mypy`/`pyproject.toml`. | Repo scan | Inconsistent style, latent type errors, and dead imports accumulate unchecked. |

### 3.2 High — Correctness bugs

| ID | Gap | Evidence | Impact |
|---|---|---|---|
| **H1** | **Timezone mismatch in RSS date filtering.** `rss_fetcher._entry_pub_date` builds a **naive** `datetime` from feedparser's UTC `*_parsed` struct, then compares against a **naive local** `datetime.now()` cutoff. | `fetchers/rss_fetcher.py` (`_entry_pub_date`, `fetch_rss_news`) vs `arxiv_fetcher.py` (correctly uses `timezone.utc`) | The lookback window is silently offset by the host's UTC offset, so feeds can be over- or under-included near the cutoff. `arxiv_fetcher` is correct; the codebase is internally inconsistent. |
| **H2** | **Deprecated `datetime.utcnow()`.** | `fetchers/twitter_fetcher.py` | Deprecated in Python 3.12 (the runtime here); warns now and is slated for removal. Should be `datetime.now(timezone.utc)`. |
| **H3** | **Tight coupling via private imports.** `enricher.py` and `eval/*` import `_create_llm`/`_is_transient_llm_error` (underscore-prefixed "private") from `summarizer.py`. | `agent/enricher.py`, `eval/run_eval.py` | LLM-client construction is duplicated/leaked across modules; changing the client wiring requires edits in several places. |
| **H4** | **`MAX_ARTICLES = 50` silent truncation.** The summariser caps the article list with no log line; downstream consumers can't tell items were dropped. | `agent/summarizer.py` (`MAX_ARTICLES`, `_format_articles`) | Silent data loss is invisible in logs and eval, masking source-mix problems. |

### 3.3 Medium — Maintainability & reliability

| ID | Gap | Evidence | Impact |
|---|---|---|---|
| **M1** | **README drift.** README documents `fetchers/web_fetcher.py` + "Exa web search" and a `com.ainewsagent.daily.plist` launchd file — **none of which exist**; the code uses **Tavily**. The `pip install` line lists `exa-py` and omits actually-required `tenacity`/`tavily-python`. | `README.md` vs `config.py`, `fetchers/`, `requirements.txt` | New contributors follow instructions that fail. Onboarding friction; erodes trust in docs. |
| **M2** | **Inconsistent logging.** `main.py` and most fetchers use `print()`; `summarizer.py`/`enricher.py` use the `logging` module. No log levels, no structured fields. | Repo-wide | Hard to filter/aggregate; the cron log is a flat text stream. |
| **M3** | **No startup config validation.** Missing required env vars surface deep in the pipeline (or silently skip sources) rather than failing fast with a clear message. | `config.py` (plain `os.getenv`) | Misconfiguration is diagnosed late, after partial work. |
| **M4** | **Sequential, blocking fetchers.** Five I/O-bound sources run one after another, each with its own timeout. | `main.py::run_agent` | Wall-clock time is the *sum* of all sources; a slow feed delays the whole run. Trivially parallelisable. |
| **M5** | **No fetcher-level retries/backoff.** Only LLM calls retry; transient HTTP failures in RSS/ArXiv/Twitter drop a whole source for the day. | `fetchers/*` | Flaky upstreams reduce digest coverage with no recovery. |
| **M6** | **Unpinned dependencies; no lockfile.** `requirements.txt` uses version *ranges*; no `pyproject.toml`, no hash-pinned lock. | `requirements.txt` | Non-reproducible builds; a minor upstream release can change behaviour overnight. |

### 3.4 Lower — Portability, scalability, security hygiene

| ID | Gap | Evidence | Impact |
|---|---|---|---|
| **L1** | **Machine-coupled operational paths.** `bin/cron-run.sh` hard-codes `LITELLM_COMPOSE_DIR=/Users/isaacboorer/mac-codebase/…`; `scheduled-job-details.txt` references `~/codebase/langchain-projects/…`. | `bin/cron-run.sh`, `scheduled-job-details.txt` | The agent only runs on one developer's Mac; not portable or reproducible. |
| **L2** | **Single-host state.** URL memory and run status live in local `logs/*.json`. | `main.py`, `url_memory.py` | No horizontal scaling; state is lost if the host is replaced; no shared history across environments. |
| **L3** | **No containerisation / cloud schedule.** Relies on macOS launchd/cron staying awake. README itself notes runs are skipped when the Mac is asleep/off. | `README.md`, `scheduled-job-details.txt` | Reliability depends on a personal machine being awake at 07:00. |
| **L4** | **Untrusted text into Markdown.** Article titles/bodies are interpolated into Telegram Markdown; mitigated by the plain-text fallback but not sanitised/escaped. | `delivery/telegram.py`, `agent/prompts.py` | Edge-case titles can force the plain-text path (losing formatting); worth explicit escaping. |
| **L5** | **Binary eval stored as NUMERIC workaround.** Documented SDK limitation, but a latent footgun if Langfuse versions change. | `eval/run_eval.py` (`_make_binary_evaluator`), `daily_eval.py` | Score semantics rely on a workaround comment, not a test. |

---

## 4. Development Plan (Phased Roadmap)

Each phase is independently shippable and ordered so that the **safety net lands before behavioural changes**. Effort is rough (S ≤ half day, M ≤ 2 days, L > 2 days).

### Phase 0 — Tooling & Safety Net *(highest priority)*

**Goal:** Make change safe and reproducible before touching any logic.

1. **Add project metadata & tooling config** *(S)* — introduce `pyproject.toml` with `ruff` (lint + format) and `mypy` configuration; keep `requirements.txt` but add a pinned/locked variant (e.g. `pip-tools` `requirements.lock` or `uv`). Addresses **C3, M6**.
2. **Author a unit-test suite** *(M)* — `tests/` with `pytest`, targeting the pure functions first (no network, no keys):
   - `dedup.py`: `_canonical_url` normalisation, priority-based collapse, no-URL passthrough.
   - `url_memory.py`: 7-day window inclusion/exclusion, prune-on-save, corrupt-file tolerance, atomic round-trip via `tmp_path`.
   - `telegram.py`: `_split_on_boundaries` never exceeds the limit and prefers paragraph > line > word.
   - `enricher.py`: `_parse_indices` (valid, out-of-range, duplicates, fenced JSON, garbage) and `_deterministic_top_n` ordering.
   - `criteria.py`: `parse_judge_response` (raw JSON, ```json fences, embedded block, junk).
   - `rss_fetcher.py`: `_title_matches_filter` and `_entry_pub_date` (fixtures for `published_parsed`/`updated_parsed`).
   - Addresses **C1**.
3. **Add CI** *(S)* — `.github/workflows/ci.yml` running `ruff check`, `mypy`, and `pytest` on push/PR. Cache deps. Addresses **C2**.
4. **Add a `make`/`justfile` or `nox` session** *(S)* — one-command `lint`, `typecheck`, `test`, `run` entry points for contributors. Addresses onboarding.

**Exit criteria:** CI green; meaningful coverage on the pure-logic modules; `ruff` and `mypy` pass.

### Phase 1 — Correctness Fixes *(guarded by Phase 0 tests)*

5. **Fix RSS timezone handling (H1)** *(S)* — make `_entry_pub_date` return timezone-aware UTC datetimes and compare against an aware cutoff; add a regression test pinning the boundary behaviour.
6. **Replace `datetime.utcnow()` (H2)** *(S)* — use `datetime.now(timezone.utc)` in `twitter_fetcher.py`; sweep for other naive/aware mismatches.
7. **Centralise LLM-client construction (H3)** *(M)* — extract a small `agent/llm.py` (`create_llm()`, `is_transient_llm_error()`) and have `summarizer`, `enricher`, and `eval/*` import from it. Removes private cross-module imports and duplication.
8. **Make truncation observable (H4)** *(S)* — log when `MAX_ARTICLES` truncates and record the dropped count in `last-run.json`.

**Exit criteria:** All new behaviour covered by tests; no naive/aware datetime comparisons remain.

### Phase 2 — Documentation & Developer Experience

9. **Reconcile README with reality (M1)** *(M)* — correct the source list (Tavily, not Exa), fix the project-structure tree, fix the `pip install`/dependency list to match `requirements.txt`, and document the actual scheduler (cron wrapper vs launchd) consistently with `scheduled-job-details.txt`. Add an architecture diagram matching §2.1.
10. **Add a `CONTRIBUTING.md` and `.env` validation doc** *(S)* — document the test/lint commands and the required-vs-optional env matrix.

**Exit criteria:** A new contributor can clone, install, configure, and run a dry digest by following the README verbatim.

### Phase 3 — Reliability & Observability

11. **Startup config validation (M3)** *(S)* — a `config.validate()` that asserts required keys (Telegram + at least one LLM route + at least one source) and prints a single actionable error; call it at the top of `run_agent()`.
12. **Unify logging (M2)** *(M)* — replace `print()` with the `logging` module behind a `setup_logging()` helper; keep human-readable console output but add levels and optional JSON for cron. Preserve existing log-file destinations.
13. **Fetcher-level retries (M5)** *(S)* — wrap HTTP fetches in tenacity with bounded backoff (reuse the transient-error predicate), so a single flaky response doesn't drop a whole source.
14. **Add a `--dry-run`/`--no-deliver` flag** *(S)* — run the full pipeline and print the digest without sending to Telegram or writing memory; invaluable for testing and demos.

### Phase 4 — Performance & Scalability

15. **Parallelise fetchers (M4)** *(M)* — run the five sources concurrently (`concurrent.futures.ThreadPoolExecutor`, since they're I/O-bound) with per-source timeouts; collapse wall-clock to the slowest source. Guard with a test using fakes.
16. **Abstract state storage (L2)** *(L)* — introduce a `Store` interface for URL memory + run status with the current JSON file as the default backend, enabling a future SQLite/Redis/cloud backend without touching pipeline code.
17. **Containerise + portable scheduling (L1, L3)** *(L)* — add a `Dockerfile` and parameterise machine-specific paths via env vars; document a cloud-cron / GitHub-Actions-schedule deployment so the digest no longer depends on a personal Mac being awake.

### Phase 5 — Quality & Hardening

18. **Markdown escaping for delivery (L4)** *(S)* — escape Telegram Markdown special characters in interpolated titles/links so formatting survives edge-case content; keep the plain-text fallback as a backstop.
19. **Eval hardening (L5)** *(M)* — add tests for the binary-score round-trip and the composite/weight maths; consider migrating to the SDK's native boolean type once verified, removing the NUMERIC workaround.
20. **Coverage gate & dependency scanning** *(S)* — add a coverage threshold to CI and a `pip-audit`/Dependabot scan for vulnerable dependencies.

---

## 5. Prioritised Backlog (at a glance)

| Priority | Items | Theme |
|---|---|---|
| **P0 (do first)** | C1, C2, C3 → Phase 0 (tests, CI, lint/type, lockfile) | Safety net |
| **P1** | H1, H2, H3, H4 → Phase 1 | Correctness |
| **P2** | M1, M3, M2, M5 → Phases 2–3 | Docs & reliability |
| **P3** | M4, L1, L2, L3 → Phase 4 | Performance & scale |
| **P4** | L4, L5 → Phase 5 | Hardening |

---

## 6. Risks, Assumptions & Non-Goals

- **Assumption:** The runtime defensiveness already in place (atomic writes, soft-fails, retries) is intentional and should be preserved, not refactored away.
- **Risk:** Phases 1+ change observable behaviour (which articles pass the date filter). Mitigated by landing Phase 0 tests first and adding regression tests with each fix.
- **Risk:** Parallelising fetchers (item 15) can change source ordering; dedupe priority already makes ordering irrelevant for correctness, but tests should pin it.
- **Non-goal:** This plan does not redesign the digest format, change the LLM provider strategy, or alter the product scope. It hardens and de-risks the existing design.

---

## 7. Suggested First PR

A tightly-scoped, low-risk opener that delivers the most leverage:

1. Add `pyproject.toml` (ruff + mypy + pytest config) and a pinned lockfile.
2. Add `tests/` covering `dedup.py`, `url_memory.py`, and `telegram.py` (no network, no keys required).
3. Add `.github/workflows/ci.yml` running lint + type-check + tests.
4. Fix **H1** (RSS timezone) and **H2** (`utcnow`) with accompanying regression tests.

This establishes the safety net and removes the two clearest correctness bugs in a single reviewable change, without altering the product's behaviour for a correctly-configured run.
