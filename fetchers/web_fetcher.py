from exa_py import Exa
from datetime import datetime, timedelta
from config import EXA_API_KEY, LOOKBACK_HOURS, RESEARCH_COMPANY_DOMAINS, MAX_ARTICLES_PER_SOURCE


def fetch_web_news() -> list[dict]:
    """Fetch AI news via Exa: general queries + research company blogs."""
    if not EXA_API_KEY:
        print("[Exa] No API key configured, skipping.")
        return []

    exa = Exa(api_key=EXA_API_KEY)
    start_date = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    articles = []

    # General AI news queries
    queries = [
        "new AI model release announcement",
        "artificial intelligence research breakthrough",
        "large language model new capabilities",
    ]

    for query in queries:
        try:
            results = exa.search(
                query,
                num_results=5,
                start_published_date=start_date,
                contents={"text": {"max_characters": 500}},
            )
            for r in results.results:
                articles.append({
                    "title": (r.title or "").strip(),
                    "url": r.url,
                    "summary": (r.text or "")[:500],
                    "source": "Exa Web Search",
                    "published": r.published_date,
                })
        except Exception as e:
            print(f"[Exa] Error for query '{query}': {e}")

    # Targeted search on research company/lab sites
    try:
        results = exa.search(
            "new AI model announcement blog post",
            num_results=MAX_ARTICLES_PER_SOURCE,
            start_published_date=start_date,
            include_domains=RESEARCH_COMPANY_DOMAINS,
            contents={"text": {"max_characters": 500}},
        )
        for r in results.results:
            articles.append({
                "title": (r.title or "").strip(),
                "url": r.url,
                "summary": (r.text or "")[:500],
                "source": "Research Lab Blog",
                "published": r.published_date,
            })
    except Exception as e:
        print(f"[Exa] Error fetching research company blogs: {e}")

    return articles
