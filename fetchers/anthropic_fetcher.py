import html
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests

from config import (
    ANTHROPIC_NEWS_URL,
    ANTHROPIC_LOOKBACK_HOURS,
    MAX_ARTICLES_PER_SOURCE,
)

ANTHROPIC_TIMEOUT = 10  # seconds
SOURCE = "Anthropic News"

# Anthropic's /news index is server-rendered HTML. Each card is an anchor to a
# /news/<slug> permalink wrapping a heading (title), a <time> (publish date) and
# a body <p> (description). We parse with stdlib regex to avoid adding a new
# dependency (beautifulsoup4 is not part of requirements).
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="(/news/[^"#?]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.DOTALL | re.IGNORECASE)
_TIME_RE = re.compile(r"<time[^>]*>(.*?)</time>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(fragment: str) -> str:
    """Remove HTML tags, unescape entities, and collapse whitespace."""
    text = _TAG_RE.sub(" ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: str) -> datetime | None:
    """Parse Anthropic's day-granular date labels, e.g. 'Jun 30, 2026'."""
    raw = _strip_tags(raw)
    if not raw:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_anthropic_news(
    html_text: str,
    base_url: str = ANTHROPIC_NEWS_URL,
    now: datetime | None = None,
    lookback_hours: int = ANTHROPIC_LOOKBACK_HOURS,
    max_articles: int = MAX_ARTICLES_PER_SOURCE,
) -> list[dict]:
    """Parse the Anthropic news index HTML into standard article dicts.

    Pure function (no network) so it can be unit-tested against a fixture.
    Deduplicates by permalink, applies the lookback cutoff (articles with an
    unparseable date are kept), and caps at ``max_articles``.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(hours=lookback_hours)

    articles: list[dict] = []
    seen_paths: set[str] = set()

    for href, inner in _ANCHOR_RE.findall(html_text or ""):
        title_match = _TITLE_RE.search(inner)
        if not title_match:
            continue  # anchors without a heading are nav/"read more" links
        title = _strip_tags(title_match.group(1))
        if not title:
            continue

        url = urljoin(base_url, href)
        path = urlparse(url).path.rstrip("/")
        if path in seen_paths:
            continue

        pub_date = _parse_date(_TIME_RE.search(inner).group(1)) if _TIME_RE.search(inner) else None
        if pub_date and pub_date < cutoff:
            continue

        body_match = _BODY_RE.search(inner)
        summary = _strip_tags(body_match.group(1)) if body_match else ""

        seen_paths.add(path)
        articles.append({
            "title": title,
            "url": url,
            "summary": summary[:500],
            "source": SOURCE,
            "published": pub_date.isoformat() if pub_date else None,
        })

        if len(articles) >= max_articles:
            break

    return articles


def fetch_anthropic_news() -> list[dict]:
    """Fetch recent announcements from the Anthropic news page.

    Fails gracefully: any network/parse error is logged and returns [] so the
    rest of the daily fetch cycle is unaffected.
    """
    try:
        resp = requests.get(
            ANTHROPIC_NEWS_URL,
            timeout=ANTHROPIC_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 ai_news_agent/1.0"},
        )
        resp.raise_for_status()
    except requests.Timeout:
        print(f"[Anthropic] Timeout fetching {ANTHROPIC_NEWS_URL}, skipping.")
        return []
    except Exception as e:
        print(f"[Anthropic] Error fetching {ANTHROPIC_NEWS_URL}: {e}")
        return []

    try:
        articles = parse_anthropic_news(resp.text)
    except Exception as e:
        print(f"[Anthropic] Error parsing news page: {e}")
        return []

    print(f"[Anthropic] Found {len(articles)} articles in last {ANTHROPIC_LOOKBACK_HOURS}h")
    return articles
