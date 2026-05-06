import json
import logging
import re
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from tavily import TavilyClient
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from agent.summarizer import _create_llm, _is_transient_llm_error
from config import TAVILY_API_KEY

_logger = logging.getLogger(__name__)

EXTRACT_MAX_CHARS = 3000

_RANK_SYSTEM = (
    "You are a senior AI-news editor. Given a numbered list of articles "
    "(title + short blurb), select the most newsworthy items for a daily "
    "AI digest. Favour: major model releases, novel research findings, "
    "consequential industry moves, and direct posts from labs/companies "
    "over aggregator/commentary posts. Return ONLY a JSON array of the "
    "selected indices (integers), no prose, no markdown. Example: [3, 7, 12]."
)

_RANK_USER_TEMPLATE = (
    "Pick the top {n} most newsworthy article indices from the list below.\n\n"
    "{listing}\n\n"
    "Return only a JSON array of {n} integer indices."
)


def _format_listing(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles):
        title = a.get("title") or "(no title)"
        source = a.get("source") or "?"
        blurb = (a.get("summary") or "")[:240].replace("\n", " ")
        lines.append(f"{i}. [{source}] {title}\n   {blurb}")
    return "\n".join(lines)


def _parse_indices(text: str, valid_max: int, n: int) -> list[int] | None:
    if not text:
        return None
    match = re.search(r"\[[^\]]*\]", text)
    candidate = match.group(0) if match else text
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    out: list[int] = []
    seen: set[int] = set()
    for v in parsed:
        if isinstance(v, int) and 0 <= v < valid_max and v not in seen:
            out.append(v)
            seen.add(v)
        if len(out) >= n:
            break
    return out or None


def _deterministic_top_n(articles: list[dict], n: int) -> list[int]:
    from agent.dedup import _SOURCE_PRIORITY

    def sort_key(idx_art):
        idx, art = idx_art
        priority = _SOURCE_PRIORITY.get(art.get("source", ""), 0)
        published = art.get("published") or ""
        return (-priority, -len(published), -idx)

    indexed = list(enumerate(articles))
    indexed.sort(key=sort_key)
    return [i for i, _ in indexed[:n]]


def select_top_articles(articles: list[dict], n: int = 10) -> list[int]:
    """Use Claude to pick the top N most newsworthy article indices.

    Falls back to deterministic ranking (source priority + recency) if the
    LLM call fails or returns malformed output, so the run never breaks here.
    """
    if not articles:
        return []
    n = min(n, len(articles))

    try:
        llm = _create_llm()
        response = llm.invoke([
            SystemMessage(content=_RANK_SYSTEM),
            HumanMessage(content=_RANK_USER_TEMPLATE.format(
                n=n,
                listing=_format_listing(articles),
            )),
        ])
        indices = _parse_indices(response.content, valid_max=len(articles), n=n)
        if indices and len(indices) >= max(1, n // 2):
            return indices
        print(f"[Enricher] Rank parse weak ({len(indices) if indices else 0}/{n}), using deterministic fallback.")
    except Exception as e:
        print(f"[Enricher] Rank LLM failed: {e} — using deterministic fallback.")

    return _deterministic_top_n(articles, n)


@retry(
    retry=retry_if_exception(_is_transient_llm_error),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _extract_batch(client: TavilyClient, urls: list[str]) -> dict:
    return client.extract(urls=urls)


def enrich(articles: list[dict], indices: list[int]) -> tuple[list[dict], int, int]:
    """Replace `summary` with full extracted body text for the selected articles.

    Returns (articles, succeeded_count, attempted_count). Articles whose extract
    failed are returned unchanged (soft-fail per article).
    """
    if not indices or not TAVILY_API_KEY:
        return articles, 0, 0

    urls = [articles[i].get("url", "") for i in indices]
    pairs = [(i, u) for i, u in zip(indices, urls) if u]
    if not pairs:
        return articles, 0, 0

    client = TavilyClient(api_key=TAVILY_API_KEY)
    try:
        result = _extract_batch(client, [u for _, u in pairs])
    except Exception as e:
        print(f"[Enricher] Tavily extract batch failed entirely: {e}")
        return articles, 0, len(pairs)

    by_url: dict[str, str] = {}
    for r in result.get("results", []) or []:
        url = r.get("url")
        body = r.get("raw_content") or ""
        if url and body:
            by_url[url] = body[:EXTRACT_MAX_CHARS]

    succeeded = 0
    for idx, url in pairs:
        body = by_url.get(url)
        if body:
            articles[idx]["summary"] = body
            succeeded += 1

    return articles, succeeded, len(pairs)
