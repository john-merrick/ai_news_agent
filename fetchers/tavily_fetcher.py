from datetime import datetime

from tavily import TavilyClient

from config import (
    TAVILY_API_KEY,
    LOOKBACK_HOURS,
    RESEARCH_COMPANY_DOMAINS,
    MAX_ARTICLES_PER_SOURCE,
)

# Rotate query strings by day-of-week so Tavily's stable news ranking doesn't
# surface the same top stories every morning. Indexed by datetime.weekday()
# (0 = Monday, 6 = Sunday).
GENERAL_QUERIES = [
    "AI model release OR LLM announcement this week",        # Mon
    "machine learning research breakthrough OR new paper",   # Tue
    "AI safety OR alignment OR policy OR regulation update", # Wed
    "AI product launch OR startup OR funding round",         # Thu
    "open source AI model OR weights release",               # Fri
    "AI tools OR developer experience OR framework update",  # Sat
    "AI industry analysis OR commentary OR opinion",         # Sun
]

LAB_QUERIES = [
    "new AI model release announcement",                     # Mon
    "research paper OR technical report",                    # Tue
    "AI safety OR responsible deployment",                   # Wed
    "product launch OR feature rollout",                     # Thu
    "open source release OR model weights",                  # Fri
    "developer tool OR API update",                          # Sat
    "company strategy OR research direction",                # Sun
]


def _to_article(result: dict, source: str) -> dict:
    return {
        "title": (result.get("title") or "").strip(),
        "url": result.get("url") or "",
        "summary": (result.get("content") or "")[:500],
        "source": source,
        "published": result.get("published_date"),
    }


def fetch_tavily_news() -> list[dict]:
    """Fetch AI news via Tavily: general news + research company blogs (rotated daily)."""
    if not TAVILY_API_KEY:
        print("[Tavily] No API key configured, skipping.")
        return []

    dow = datetime.now().weekday()
    general_query = GENERAL_QUERIES[dow]
    lab_query = LAB_QUERIES[dow]
    print(f"[Tavily] dow={dow} general='{general_query[:60]}...' lab='{lab_query[:60]}...'")

    client = TavilyClient(api_key=TAVILY_API_KEY)
    articles: list[dict] = []

    try:
        general = client.search(
            query=general_query,
            topic="news",
            time_range="day",
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
            query=lab_query,
            topic="news",
            time_range="day",
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
