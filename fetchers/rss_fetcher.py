import feedparser
from datetime import datetime, timedelta
from config import RSS_FEEDS, MAX_ARTICLES_PER_SOURCE, LOOKBACK_HOURS


def fetch_rss_news() -> list[dict]:
    """Fetch AI news from configured RSS feeds."""
    articles = []
    cutoff = datetime.now() - timedelta(hours=LOOKBACK_HOURS)

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                if count >= MAX_ARTICLES_PER_SOURCE:
                    break

                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                if pub_date and pub_date < cutoff:
                    continue

                articles.append({
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:500],
                    "source": feed.feed.get("title", feed_url),
                    "published": pub_date.isoformat() if pub_date else None,
                })
                count += 1
        except Exception as e:
            print(f"[RSS] Error fetching {feed_url}: {e}")

    return articles
