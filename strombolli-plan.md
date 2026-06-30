# AI News Agent — Architectural Analysis & Development Roadmap

> **Document type:** Analysis & planning only. This file is **purely additive** — it does not modify, move, or delete any existing source, configuration, script, or documentation file in the repository.
> **Generated:** 2026-06-30
> **Repository:** `ai_news_agent`
> **Branch analysed:** `stromboli/38e36e2b-…-action-plan-md`
> **Scope:** Current-state architecture review → concrete gap & technical-debt analysis → phased, independently-shippable improvement roadmap.

---

## 1. Executive Summary

`ai_news_agent` is a compact, single-purpose Python application that builds a daily AI-news digest and delivers it to Telegram. Each run:

1. **Fetches** from five sources — RSS (11 feeds), Reddit, Tavily web/news search, Twitter/X, and ArXiv.
2. **Deduplicates** by canonical URL (source-priority tie-break) and **filters** against a rolling 7-day "seen URLs" memory.
3. **Ranks** the candidate pool with Claude to pick the top *N* most newsworthy items (deterministic fallback if the LLM misbehaves).
4. **Enriches** those top items with full body text via Tavily `extract`.
5. **Summarises** the pool into a ~500-word Markdown digest with Claude.
6. **Delivers** the digest to Telegram (chunked, with a Markdown→plain-text fallback).
7. **Evaluates** the digest best-effort with an LLM-as-judge, optionally logging to Langfuse and routing model calls/cost through a local LiteLLM proxy.

The codebase is **small, readable, and notably defensive in its runtime paths**: atomic JSON writes (`os.replace`), `tenacity` retries on transient LLM/extract errors, per-source soft-fail isolation, a deterministic ranking fallback, and a Markdown→plain-text delivery fallback. A hardened cron wrapper (`bin/cron-run.sh`) adds single-instance locking, a LiteLLM preflight/auto-restart, and a separate sys-ops alert channel.

The biggest weaknesses are **structural and correctness-related, not behavioural**:

- **No automated tests, no CI, no linting or type-checking** — `pytest` collects **0 tests**. Every change is unverified.
- **Fully synchronous, serial execution** — five fetchers run one after another, and within RSS the 11 feeds are fetched serially, each with a 10s timeout. Worst-case wall-clock is the *sum* of all network latencies.
- **At least two real correctness bugs** — a timezone-naive date comparison in RSS filtering, and a `str.lstrip("www.")` misuse that corrupts hostnames during URL canonicalisation (affecting dedup **and** the seen-URL memory).
- **Deduplication is URL-only** — the same story arriving under different URLs survives, leaning entirely on the summariser LLM to merge it.
- **Documentation drift** — `README.md` references files and dependencies that do not exist in the tree (`web_fetcher.py`, Exa, a `.plist`), while omitting ones that do.
- **Machine-coupled operations** — absolute paths and host assumptions are baked into `bin/cron-run.sh`.

This roadmap front-loads the **safety net (tests + CI + lint)**, then **correctness fixes**, then **performance and maintainability**, then **scalability/observability**. Each phase is independently shippable and ordered so that later work is protected by the tests added earlier.

---

## 2. Current Architecture

### 2.1 Component map

```
ai_news_agent/
├── main.py                  # Orchestrator: run_agent() + --schedule (APScheduler)
├── config.py                # Env loading, RSS_FEEDS, keyword filter, research domains
├── fetchers/
│   ├── rss_fetcher.py       # 11 RSS feeds, per-feed lookback + title keyword filter
│   ├── reddit_fetcher.py    # praw; 5 subreddits, MIN_SCORE=50
│   ├── tavily_fetcher.py    # Tavily news search: general + lab-domain, rotated by weekday
│   ├── twitter_fetcher.py   # Twitter/X recent search API v2
│   └── arxiv_fetcher.py     # ArXiv Atom API: cs.AI/cs.LG/cs.CL/stat.ML, 48h window
├── agent/
│   ├── dedup.py             # Canonical-URL dedup with source-priority tie-break
│   ├── url_memory.py        # 7-day rolling "seen URLs" JSON store
│   ├── enricher.py          # LLM top-N ranking + Tavily extract enrichment
│   ├── summarizer.py        # LangChain LLM client, tenacity retry, Langfuse hook
│   └── prompts.py           # SYSTEM_PROMPT + USER_PROMPT_TEMPLATE
├── delivery/
│   └── telegram.py          # Boundary-aware chunking + Markdown→plain-text fallback
├── eval/
│   ├── criteria.py          # Universal + subjective criteria, judge prompts, JSON parse
│   ├── daily_eval.py        # Per-run binary "useful?" judge (claude-haiku-4-5)
│   └── run_eval.py          # Langfuse run_experiment over a canned dataset
├── bin/
│   ├── cron-run.sh          # Hardened cron wrapper (lock, preflight, sys-ops alerts)
│   ├── refresh-secrets.sh   # Pre-resolve secrets into .env.secrets (1Password)
│   └── weekly-eval.sh       # Weekly regression eval via eval/run_eval.py
├── requirements.txt
├── .env.example / .env.secrets.tmpl
└── README.md / scheduled-job-details.txt
```

### 2.2 Data flow

```
fetchers → list[dict]  ──► dedupe() ──► filter_unseen(seen) ──► select_top_articles() (LLM rank)
                                                                      │
                                          enrich(top_indices) ◄───────┘  (Tavily extract → summary)
                                                  │
                                          summarize_news() (Claude) ──► send_telegram_message()
                                                  │                               │
                                          save_seen(urls)                  evaluate_today() (judge)
                                                  │
                                       _atomic_write_json(last-run.json)  (always, in finally)
```

The universal in-memory contract is a plain `dict` per article:
`{"title", "url", "summary", "source", "published"}` (plus optional `score`/`likes`). There is **no schema or validation** — every consumer reaches into the dict with `.get(...)`.

### 2.3 Component-by-component notes

**`main.py` — orchestrator.** A single `run_agent()` returns a shell-style exit code (0 ok, 1 no-articles/all-seen, 2 delivery-failed, 3 fatal). It accumulates a rich `status` dict and **always** writes `logs/last-run.json` in a `finally` block (so the cron wrapper can alert on stats). Good separation of *outcome* from *delivery*. The eval step is wrapped so it can never break the run. `--schedule` runs an immediate pass then hands off to a `BlockingScheduler`. **Observation:** all orchestration logging is via `print()`, the fetch sequence is hard-coded and serial, and per-stage constants (`ENRICH_TOP_N = 10`) are module-level literals.

**`config.py` — configuration.** Cleanly env-driven via `python-dotenv`. `RSS_FEEDS` is a well-structured list of dicts (per-feed `lookback_hours` override + optional title `client_filter`), and weekly newsletters correctly extend their window to 168h. **Observation:** the LLM model names live in `agent/` and `eval/`, not here; `RESEARCH_COMPANY_DOMAINS` and the keyword filter are static.

**Fetchers.** Each returns `list[dict]` and **soft-fails to an empty list** on error, so one dead source never sinks the run — a strong design choice. RSS applies per-feed lookback + keyword filtering; ArXiv correctly uses timezone-aware datetimes and a 48h moderation window with early-exit on the sorted feed; Tavily rotates queries by `weekday()` to avoid repetition. **Observations:** RSS date handling is timezone-naive (see §3, Bug #1); Twitter uses the deprecated `datetime.utcnow()`; all network fetches inside RSS are serial.

**`agent/dedup.py`.** `_canonical_url()` lowercases scheme/host, strips trailing slash, and drops query/fragment; `dedupe()` keeps the highest-priority source per canonical URL (ArXiv 100 → Tavily News 30) and preserves URL-less items. **Observation:** `host.lstrip("www.")` is a character-class strip, not a prefix strip (see §3, Bug #2). Dedup is URL-only — no title/near-duplicate detection.

**`agent/url_memory.py`.** A 7-day rolling JSON map `{date: [canonical_urls]}`, pruned on every write, atomic via `os.replace`. Memory is only persisted **after** summarisation succeeds, so transient LLM failures don't poison it. **Observation:** it shares `_canonical_url`, so it inherits Bug #2; storage is a single local file (single-writer, non-portable).

**`agent/enricher.py`.** `select_top_articles()` asks Claude for a JSON array of indices, validates/clamps them, and falls back to deterministic ranking (source priority + recency) if the response is weak or errors. `enrich()` batches Tavily `extract` (with retry) and replaces `summary` with body text, soft-failing per article. **Observations:** the ranking LLM call uses a **raw `llm.invoke`** — it is **not** wrapped in the `tenacity` retry used elsewhere, and it is **not** traced to Langfuse; `enrich()` mutates the input list in place.

**`agent/summarizer.py`.** Central LLM factory `_create_llm()` (LiteLLM proxy preferred, direct Anthropic fallback), a shared transient-error classifier `_is_transient_llm_error`, and a `tenacity`-retried `_invoke_llm`. Langfuse tracing is attached via `propagate_attributes` when configured. **Observation:** model identifiers are string literals (`"claude-sonnet"`, `"claude-sonnet-4-6"`); `MAX_ARTICLES = 50` caps prompt size.

**`delivery/telegram.py`.** Splits on paragraph→line→word boundaries to avoid cutting through Markdown pairs, and retries each chunk as plain text if Markdown parsing 400s — delivery almost always succeeds. **Observation:** no retry on transient network/5xx/429; partial multi-chunk delivery can leave a half-sent digest with no rollback signal beyond `all_ok=False`.

**`eval/`.** `daily_eval.evaluate_today()` runs a cheap binary "useful?" judge and optionally records a Langfuse dataset item — fully best-effort, never raises. `run_eval.py` drives a Langfuse `run_experiment` over a hand-built dataset (including a `duplicate_stories` scenario), with weighted composite and run-level aggregators. `criteria.py` defines reusable universal/subjective criteria and a tolerant judge-JSON parser. This is the most mature subsystem. **Observation:** these are quality evals, **not** unit tests — they require live API keys and a running stack, and exercise none of the deterministic logic (dedup, canonicalisation, chunking, URL memory, index parsing).

**`bin/` + scheduling.** `cron-run.sh` is genuinely battle-tested (lock, stale-PID detection, LiteLLM preflight/restart, sys-ops alerting, structured-status reporting via `jq`). **Observation:** it hard-codes `/Users/isaacboorer/mac-codebase/dev-ops/observability` and assumes `${PROJECT_DIR}/venv` and Homebrew paths — non-portable.

---

## 3. Architectural Gaps, Bugs & Technical Debt

Ordered by severity. Each item cites the concrete location and the user-visible impact.

### 3.1 Correctness bugs (highest priority)

**Bug #1 — Timezone-naive RSS date filtering (`fetchers/rss_fetcher.py`).**
`_entry_pub_date()` builds a **naive** datetime from feedparser's `*_parsed` struct, which is in **UTC** (`datetime(*ts[:6])`), then `fetch_rss_news()` compares it against `now = datetime.now()`, which is **local time**. For any non-UTC host the cutoff is offset by the UTC delta — silently dropping or admitting feed entries near the boundary. ArXiv does this correctly (timezone-aware) and Twitter is inconsistent (`datetime.utcnow()`), so behaviour differs per source. **Impact:** non-deterministic, location-dependent inclusion of borderline articles.

**Bug #2 — `lstrip("www.")` corrupts hostnames (`agent/dedup.py`, `_canonical_url`).**
`host.lstrip("www.")` strips any leading run of the *character set* `{w, .}`, not the literal prefix `"www."`. So `"wired.com"` → `"ired.com"`, `"www.foo.com"` → `"foo.com"` (intended), and `"web.dev"` → `"eb.dev"`. Because both `dedupe()` and `url_memory` canonicalise URLs, this **mis-canonicalises** affected hosts in two places: duplicates may not collapse, and the 7-day "seen" filter may fail to suppress (or wrongly suppress) stories. **Impact:** silent dedup/memory misses for any host beginning with `w`/`.` characters. Correct fix: `host.removeprefix("www.")`.

**Bug #3 — Deprecated `datetime.utcnow()` (`fetchers/twitter_fetcher.py`).**
Deprecated as of Python 3.12 and returns a naive value. Works today but emits warnings and is inconsistent with the timezone-aware approach ArXiv uses. **Impact:** future breakage + correctness drift.

### 3.2 No automated test suite, CI, or static analysis (highest structural priority)

`pytest` collects **0 tests** (verified on this branch); there is no `tests/` directory, no `pyproject.toml`/`pytest.ini`, no CI workflow, no linter or type-checker config. The `eval/` suite is a *quality* harness needing live keys — it does not protect the deterministic core. Highly testable pure functions are entirely uncovered:

- `dedup._canonical_url`, `dedup.dedupe`, `dedup._priority`
- `url_memory.load_seen` / `save_seen` / `filter_unseen` (pruning, atomic write)
- `enricher._parse_indices`, `_deterministic_top_n`, `_format_listing`
- `telegram._split_on_boundaries` (Markdown-safe chunking)
- `criteria.parse_judge_response` (fence-stripping JSON parse)
- `rss_fetcher._title_matches_filter`, `_entry_pub_date`

**Impact:** Bugs #1 and #2 would have been caught instantly by a unit test. Every refactor in this roadmap is currently unguarded.

### 3.3 Synchronous, serial execution (performance)

`run_agent()` calls the five fetchers sequentially (`main.py` lines ~62–90), and `fetch_rss_news()` loops the 11 feeds serially, each with a 10s timeout. Worst-case fetch wall-clock is the **sum** of all source latencies (RSS alone can approach `11 × 10s` if multiple feeds are slow). Network I/O is the dominant cost and is trivially parallelisable. **Impact:** slow runs; higher chance of brushing the cron LiteLLM wait budget; poor latency headroom as feeds are added.

### 3.4 Incomplete resilience / retry coverage

- The **ranking** LLM call (`enricher.select_top_articles`) uses a bare `llm.invoke` — **no** `tenacity` retry, unlike `summarizer._invoke_llm` and `enricher._extract_batch`. A transient blip forces the deterministic fallback unnecessarily.
- **Telegram** delivery has no retry on transient 429/5xx/network errors — only the Markdown→plain-text fallback.
- There is **no global run timeout / watchdog**; a stuck retry or a missing per-call timeout can stall the whole run (the cron lock prevents pile-ups but the day's digest is lost).

### 3.5 Deduplication is URL-only (content quality)

`dedupe()` matches exact canonical URLs. The same announcement republished by VentureBeat, The Verge, and a Tavily result under different URLs is **not** merged — the burden falls entirely on the summariser LLM (the eval dataset's `duplicate_stories` scenario is explicit acknowledgement of this). **Impact:** near-duplicate stories inflate the candidate pool, waste enrichment/ranking budget, and risk repetition in the digest.

### 3.6 Rate limiting & API-usage optimisation

No client-side rate limiting, request budgeting, or response caching. ArXiv pulls up to 60 entries every run with no caching; Tavily issues two searches + one extract batch per run; three distinct Claude calls (rank, summarise, judge) happen every run with no prompt/result caching across retries. There is no coordination if multiple sources hit shared limits. **Impact:** avoidable cost and exposure to provider 429s, mitigated only partially by retries.

### 3.7 State persistence is local-file-only

`url_memory` (`logs/seen-urls.json`) and `last-run.json` are local, gitignored files with an explicit single-writer assumption. This is fine for one machine but blocks horizontal scaling, multi-host failover, or moving to serverless. **Impact:** state is non-portable and tied to one host's filesystem.

### 3.8 Hard-coded configuration & machine coupling

- **Model names** as string literals across modules: `"claude-sonnet"` and `"claude-sonnet-4-6"` (`summarizer._create_llm`), `"claude-haiku-4-5"` (`daily_eval`, `run_eval`). `"claude-sonnet-4-6"` is the kind of typo-prone literal worth centralising.
- **Tuning constants** scattered as literals: `ENRICH_TOP_N` (main), `MAX_ARTICLES` (summarizer), `MIN_SCORE`/`SUBREDDITS` (reddit), `GENERAL_QUERIES`/`LAB_QUERIES` (tavily), `ARXIV_CATEGORIES`/`max_results=60` (arxiv), `MAX_CHUNK` (telegram).
- **`bin/cron-run.sh`** hard-codes `/Users/isaacboorer/mac-codebase/dev-ops/observability`, `${PROJECT_DIR}/venv`, and Homebrew paths. **Impact:** the repo only runs unmodified on its author's machine.

### 3.9 Observability: `print()` instead of structured logging

Almost all runtime diagnostics use `print()`; only `summarizer`/`enricher` create a `logging` logger (used by `tenacity`). There are no log levels, no structured fields, no correlation/run IDs across the pipeline. The status JSON + sys-ops alerts are good, but log triage relies on grepping free-text stdout. **Impact:** hard to filter/aggregate; no severity control; weak debuggability beyond the status file.

### 3.10 Documentation drift (`README.md`)

The README describes a structure and stack that diverge from the code:
- References **`fetchers/web_fetcher.py`** and **Exa** web search — neither exists; the implementation uses **Tavily**. `.env.example` correctly lists `TAVILY_API_KEY`, but the README's variable table lists `EXA_API_KEY`.
- The setup `pip install` line lists `exa-py`, `python-telegram-bot`, and `langchain` extras while **omitting** `tavily-python` and `tenacity` that `requirements.txt` actually pins.
- Scheduling docs describe a macOS **`.plist` / launchd** flow, but the real, hardened scheduler is the **`bin/cron-run.sh`** cron wrapper; no `.plist` is in the tree.
- Source lists (e.g. "MIT AI News") are inconsistent with `config.RSS_FEEDS`. **Impact:** a new contributor following the README cannot reproduce the working setup.

### 3.11 Minor / housekeeping

- `enrich()` mutates the caller's list in place (surprising side effect; `main.py` reassigns the return so it's masked).
- No `pyproject.toml`/packaging metadata; imports rely on running from the repo root (`sys.path.insert(0, ".")` in `run_eval.py`).
- `status` dict keys are added ad-hoc (`filtered_by_memory`, `eval_*`) — no typed schema, so the `jq` consumer in `cron-run.sh` is coupled to undocumented field names.
- ArXiv `max_results=60` candidates can dominate the pre-dedup pool versus other sources.

---

## 4. Proposed Improvements

Mapped 1:1 to the gaps above, with concrete, idiomatic approaches that fit the existing style.

### 4.1 Fix the correctness bugs
- **Bug #1:** make all date handling timezone-aware. In `rss_fetcher`, build UTC-aware datetimes (`datetime(*ts[:6], tzinfo=timezone.utc)`) and compare against `datetime.now(timezone.utc)`. Apply the same convention everywhere; replace Twitter's `datetime.utcnow()` with `datetime.now(timezone.utc)`.
- **Bug #2:** replace `host.lstrip("www.")` with `host.removeprefix("www.")` in `dedup._canonical_url`. Add a regression test asserting `"wired.com"` is preserved.
- Centralise a tiny `utils/time.py` with `now_utc()` and a `parse_to_utc()` helper to prevent recurrence.

### 4.2 Establish the test + CI safety net (do this first)
- Add `tests/` with `pytest` unit tests for every pure function listed in §3.2. Target the deterministic core first — no network, no API keys.
- Add `pyproject.toml` (or `pytest.ini`) with `pytest` config and coverage settings; set an initial coverage floor (e.g. 70%) and ratchet up.
- Introduce **`ruff`** (lint + format) and **`mypy`** (typed, starting non-strict) with config in `pyproject.toml`.
- Add a **GitHub Actions** workflow running `ruff check`, `mypy`, and `pytest --cov` on push/PR.
- Mock external clients (`requests`, `TavilyClient`, LangChain LLM, Telegram) with fixtures; add fixture RSS/ArXiv payloads under `tests/fixtures/`.

### 4.3 Parallelise fetching
- Run the five fetchers concurrently with a `ThreadPoolExecutor` (they are blocking I/O; threads are the lowest-risk change and require no `async` rewrite). Parallelise the 11 RSS feeds the same way inside `fetch_rss_news`.
- Preserve soft-fail isolation: gather per-source results, log failures, never let one cancel others. Expected wall-clock drops from *sum* to *max* of source latencies.
- (Optional, later) An `asyncio`/`httpx` rewrite if/when fetch count grows substantially.

### 4.4 Complete resilience coverage
- Wrap `select_top_articles`'s LLM call in the shared `_invoke_llm` (retry + Langfuse trace) so ranking gets the same robustness as summarisation.
- Add bounded retry with backoff to Telegram `_post` for 429/5xx/network (honouring `Retry-After` when present).
- Add per-stage and whole-run timeouts/watchdog so a single stuck call can't consume the run; record a timeout outcome in `status`.

### 4.5 Add near-duplicate detection
- Layer a cheap title-similarity pass on top of URL dedup: normalise titles (lowercase, strip punctuation/stopwords) and collapse via token-set ratio (e.g. `rapidfuzz`) or a shingled hash, keeping the highest-priority source — reuse `_SOURCE_PRIORITY`. Keep URL dedup as the first, exact pass.
- Validate against the existing `duplicate_stories` eval scenario to confirm fewer near-dupes reach the summariser.

### 4.6 Rate limiting & caching
- Add a small client-side limiter/backoff wrapper around outbound calls; centralise per-provider limits in config.
- Cache ArXiv/Tavily responses for the run window (content-hash or short TTL on disk) so retries and re-runs don't re-pay; this also makes tests reproducible.

### 4.7 Pluggable persistence
- Extract a `SeenStore` interface with the current JSON file as the default implementation, plus a SQLite implementation (single-file, std-lib, still local but transactional and queryable). This keeps the simple default while unlocking a path to a shared store (Redis/Postgres) without touching call sites.

### 4.8 Centralise configuration
- Move all model identifiers and tuning constants into `config.py` (env-overridable): `RANK_MODEL`, `SUMMARY_MODEL`, `JUDGE_MODEL`, `ENRICH_TOP_N`, `MAX_ARTICLES`, `MIN_SCORE`, `MAX_CHUNK`, etc.
- Replace machine-specific literals in `bin/cron-run.sh` with env vars (`OBSERVABILITY_DIR`, `PYTHON_BIN`) sourced from `.env`/`.env.secrets`, with sensible defaults.
- Consider `pydantic-settings` for typed, validated config loading (fail fast on missing required keys).

### 4.9 Structured logging
- Replace `print()` with the stdlib `logging` module configured once in `main.py` (level via `LOG_LEVEL` env). Emit a per-run correlation ID and attach it to each stage's log records and to the `status` dict.
- Optionally JSON-format logs for machine ingestion while keeping human-readable console output in dev.

### 4.10 Typed article model
- Introduce a `pydantic` `Article` model (or a `@dataclass`) for the inter-stage contract, with validation at fetcher boundaries. Type the `status` payload as a `TypedDict`/model so the `jq` consumer in `cron-run.sh` has a documented schema.

### 4.11 Documentation realignment
- Rewrite the README to match reality: Tavily (not Exa), the actual `requirements.txt`, the `bin/cron-run.sh` cron flow (not a `.plist`), and the true module list. Add an architecture diagram and a "running tests" section. (Per constraints, this roadmap only *proposes* the rewrite; it does not edit `README.md`.)

---

## 5. Phased Implementation Roadmap

Each phase is independently shippable. Phases 1–2 are protected by the tests added in Phase 1, so all later refactors are guarded.

### Phase 0 — Baseline & guardrails *(½ day)*
- [ ] Add `pyproject.toml` with `pytest`, `ruff`, `mypy`, and coverage config.
- [ ] Add a GitHub Actions CI workflow: `ruff check` → `mypy` → `pytest --cov`.
- [ ] Create `tests/` skeleton + `tests/fixtures/` with sample RSS/ArXiv payloads and a captured Telegram/Tavily response.
- **Exit criteria:** CI runs green on an empty-but-present test suite; lint/type baseline recorded.

### Phase 1 — Test the deterministic core *(1–2 days)*
- [ ] Unit tests for `dedup` (incl. a `"wired.com"` canonicalisation regression), `url_memory` (pruning + atomic write via `tmp_path`), `enricher._parse_indices`/`_deterministic_top_n`, `telegram._split_on_boundaries`, `criteria.parse_judge_response`, `rss_fetcher._title_matches_filter`/`_entry_pub_date`.
- [ ] Mock-based tests for each fetcher's happy path + error soft-fail.
- **Exit criteria:** ≥70% line coverage on `agent/`, `delivery/`, `fetchers/`; Bugs #1 and #2 reproduced by failing tests.

### Phase 2 — Correctness fixes *(½–1 day)*
- [ ] Fix Bug #2 (`removeprefix`), Bug #1 (timezone-aware RSS), Bug #3 (`now(timezone.utc)`); add `utils/time.py`.
- [ ] Turn the Phase-1 failing tests green.
- **Exit criteria:** all correctness tests pass; date filtering is host-timezone-independent.

### Phase 3 — Performance: parallel fetching *(1 day)*
- [ ] `ThreadPoolExecutor` across the five fetchers and across RSS feeds, preserving soft-fail isolation and per-source counts.
- [ ] Add per-call timeouts everywhere and a whole-run watchdog.
- **Exit criteria:** fetch wall-clock ≈ slowest source (not the sum); tests confirm isolation (one failing source doesn't fail others).

### Phase 4 — Resilience & rate limiting *(1 day)*
- [ ] Route ranking through retried/traced `_invoke_llm`; add Telegram transient-retry with `Retry-After`.
- [ ] Add a client-side limiter/backoff + short-TTL response cache for ArXiv/Tavily.
- **Exit criteria:** simulated 429/5xx in tests trigger bounded retry then succeed; ranking call is traced.

### Phase 5 — Content quality: near-duplicate dedup *(1 day)*
- [ ] Title-similarity second pass after URL dedup, reusing `_SOURCE_PRIORITY`.
- [ ] Validate against the `duplicate_stories` eval scenario.
- **Exit criteria:** near-dupes measurably reduced on fixtures without dropping distinct stories.

### Phase 6 — Configuration & persistence *(1–2 days)*
- [ ] Centralise models/constants in `config.py` (env-overridable), ideally via `pydantic-settings`.
- [ ] De-hardcode `bin/cron-run.sh` paths into env vars with defaults.
- [ ] Extract `SeenStore` interface; add a SQLite backend behind the existing JSON default.
- **Exit criteria:** no hard-coded model names or host paths in code/scripts; persistence backend is swappable behind one interface.

### Phase 7 — Observability & typed contracts *(1 day)*
- [ ] Replace `print()` with `logging` + per-run correlation ID; `LOG_LEVEL` env.
- [ ] Introduce a typed `Article` model and a `TypedDict`/model for `status`.
- **Exit criteria:** logs carry levels + run ID; article/status schemas are validated and documented.

### Phase 8 — Documentation *(½ day)*
- [ ] Rewrite `README.md` to match the actual stack/flow; add architecture diagram and a "running tests" section; document the `status` schema consumed by `cron-run.sh`.
- **Exit criteria:** a fresh contributor can set up, run, and test from the README alone.

### Suggested sequencing
`Phase 0 → 1 → 2` form the critical path (safety net + correctness). Phases 3–7 can be parallelised across contributors once the test net exists. Phase 8 should trail whatever lands to avoid re-drift.

---

## 6. Risk & Effort Summary

| Phase | Theme | Effort | Risk | Primary payoff |
|------:|-------|:------:|:----:|----------------|
| 0 | CI / lint / types scaffold | ½ d | Low | Foundation for everything |
| 1 | Test the core | 1–2 d | Low | Catches existing + future bugs |
| 2 | Correctness fixes | ½–1 d | Low | Removes silent data-loss bugs |
| 3 | Parallel fetch | 1 d | Med | Sum→max latency reduction |
| 4 | Resilience + rate limit | 1 d | Med | Fewer dropped/failed runs |
| 5 | Near-dup dedup | 1 d | Med | Higher digest quality |
| 6 | Config + persistence | 1–2 d | Med | Portability + scalability |
| 7 | Logging + typed models | 1 d | Low | Debuggability + safety |
| 8 | Docs | ½ d | Low | Onboarding + accuracy |

**Lowest-effort / highest-impact first moves:** Phase 2 Bug #2 (`removeprefix`, a one-line fix to a silent dedup/memory defect) and Phase 0–1 (the test net that would have caught it).

---

## 7. Appendix — Notable Strengths to Preserve

These existing patterns are good and should be retained through any refactor:

- **Per-source soft-fail isolation** in every fetcher — one dead source never sinks the run.
- **Atomic JSON writes** (`os.replace`) for `last-run.json` and the seen-URL store.
- **Deterministic ranking fallback** when the LLM ranker errors or returns junk.
- **Markdown→plain-text delivery fallback** guaranteeing the digest is sent.
- **`status` written in `finally`** so the cron wrapper always has stats to alert on.
- **Seen-URL memory persisted only after successful summarisation** — no poisoning on transient failures.
- **Hardened cron wrapper** — single-instance lock, LiteLLM preflight/auto-restart, separate sys-ops alert channel.
- **A real eval harness** (`eval/`) — a strong base to extend with the deterministic unit tests this plan adds.
