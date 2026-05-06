from datetime import datetime, timedelta

from tavily import TavilyClient

from config import (
    TAVILY_API_KEY,
    LOOKBACK_HOURS,
    RESEARCH_COMPANY_DOMAINS,
    MAX_ARTICLES_PER_SOURCE,
)

GENERAL_QUERY = (
    "AI model release OR LLM announcement OR machine learning breakthrough "
    "OR artificial intelligence research"
)
LAB_QUERY = "new AI model OR research announcement OR product launch"


def _to_article(result: dict, source: str) -> dict:
    return {
        "title": (result.get("title") or "").strip(),
        "url": result.get("url") or "",
        "summary": (result.get("content") or "")[:500],
        "source": source,
        "published": result.get("published_date"),
    }


def fetch_tavily_news() -> list[dict]:
    """Fetch AI news via Tavily: general news + research company blogs (last 24h)."""
    if not TAVILY_API_KEY:
        print("[Tavily] No API key configured, skipping.")
        return []

    days = max(1, LOOKBACK_HOURS // 24)
    client = TavilyClient(api_key=TAVILY_API_KEY)
    articles: list[dict] = []

    try:
        general = client.search(
            query=GENERAL_QUERY,
            topic="news",
            days=days,
            max_results=20,
            include_answer=False,
            include_raw_content=False,
        )
        for r in general.get("results", []):
            articles.append(_to_article(r, "Tavily News"))
    except Exception as e:
        print(f"[Tavily] Error on general news query: {e}")

    try:
        labs = client.search(
            query=LAB_QUERY,
            topic="news",
            days=days,
            max_results=MAX_ARTICLES_PER_SOURCE,
            include_domains=RESEARCH_COMPANY_DOMAINS,
            include_answer=False,
            include_raw_content=False,
        )
        for r in labs.get("results", []):
            articles.append(_to_article(r, "Research Lab Blog"))
    except Exception as e:
        print(f"[Tavily] Error on lab/company query: {e}")

    return articles
