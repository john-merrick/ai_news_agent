import requests
from datetime import datetime, timedelta
from config import TWITTER_BEARER_TOKEN, LOOKBACK_HOURS, MAX_ARTICLES_PER_SOURCE

TWITTER_QUERY = (
    "(AI OR LLM OR \"language model\" OR \"artificial intelligence\") "
    "(announcement OR release OR launched OR new) "
    "-is:retweet lang:en"
)


def fetch_twitter_news() -> list[dict]:
    """Fetch AI news tweets. Skips if bearer token is not configured."""
    if not TWITTER_BEARER_TOKEN:
        print("[Twitter] No bearer token configured, skipping.")
        return []

    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    start_time = (datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    params = {
        "query": TWITTER_QUERY,
        "max_results": min(MAX_ARTICLES_PER_SOURCE, 10),
        "start_time": start_time,
        "tweet.fields": "created_at,author_id,public_metrics",
        "expansions": "author_id",
        "user.fields": "name,username",
    }

    try:
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers=headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Twitter] Request error: {e}")
        return []

    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    articles = []

    for tweet in data.get("data", []):
        author = users.get(tweet.get("author_id", ""), {})
        articles.append({
            "title": tweet["text"][:100],
            "url": f"https://twitter.com/i/web/status/{tweet['id']}",
            "summary": tweet["text"],
            "source": f"Twitter/@{author.get('username', 'unknown')}",
            "published": tweet.get("created_at"),
            "likes": tweet.get("public_metrics", {}).get("like_count", 0),
        })

    return articles
