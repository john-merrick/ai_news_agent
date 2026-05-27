import feedparser
import requests
from datetime import datetime, timedelta
from config import RSS_FEEDS, MAX_ARTICLES_PER_SOURCE, LOOKBACK_HOURS

RSS_TIMEOUT = 10  # seconds per feed


def _title_matches_filter(title: str, keywords: tuple | None) -> bool:
    if not keywords:
        return True
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


def _entry_pub_date(entry) -> datetime | None:
    # Prefer published_parsed; some feeds (DeepMind, Google Research) only set updated_parsed.
    for attr in ("published_parsed", "updated_parsed"):
        ts = getattr(entry, attr, None)
        if ts:
            try:
                return datetime(*ts[:6])
            except (TypeError, ValueError):
                continue
    return None


def fetch_rss_news() -> list[dict]:
    """Fetch AI news from configured RSS feeds.

    RSS_FEEDS is a list of dicts: each entry can override the global LOOKBACK_HOURS
    (for weekly newsletters) and apply a keyword filter on title (for broad blogs
    like HN frontpage that mix AI with non-AI posts).
    """
    articles = []
    now = datetime.now()

    for feed_config in RSS_FEEDS:
        feed_url = feed_config["url"]
        lookback = feed_config.get("lookback_hours") or LOOKBACK_HOURS
        keyword_filter = feed_config.get("client_filter")
        cutoff = now - timedelta(hours=lookback)

        try:
            resp = requests.get(feed_url, timeout=RSS_TIMEOUT, headers={"User-Agent": "ai_news_agent/1.0"})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            count = 0
            for entry in feed.entries:
                if count >= MAX_ARTICLES_PER_SOURCE:
                    break

                pub_date = _entry_pub_date(entry)
                if not pub_date or pub_date < cutoff:
                    continue

                title = entry.get("title", "").strip()
                if not _title_matches_filter(title, keyword_filter):
                    continue

                articles.append({
                    "title": title,
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:500],
                    "source": feed.feed.get("title", feed_url),
                    "published": pub_date.isoformat(),
                })
                count += 1
        except requests.Timeout:
            print(f"[RSS] Timeout fetching {feed_url}, skipping.")
        except Exception as e:
            print(f"[RSS] Error fetching {feed_url}: {e}")

    return articles
