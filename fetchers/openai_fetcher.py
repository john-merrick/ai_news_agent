import html
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests

from config import (
    OPENAI_NEWS_URL,
    OPENAI_LOOKBACK_HOURS,
    MAX_ARTICLES_PER_SOURCE,
)

OPENAI_TIMEOUT = 10  # seconds
SOURCE = "OpenAI News"

# Module logger. The daily agent runs headless via cron, so failures MUST be
# emitted at ERROR/CRITICAL level (not just print) to be visible in the captured
# logs and surfaced by the sys-ops alerting on the wrapper script.
_logger = logging.getLogger(__name__)

# OpenAI's /news index is a JS-rendered Next.js app, but the server response
# still contains the article cards as anchors to /index/<slug> permalinks. Each
# card wraps a title, a date (either in a <time> tag or a "Mon DD, YYYY" text
# label) and sometimes a short body <p>. We parse with stdlib regex to avoid
# adding a new dependency (beautifulsoup4 is not part of requirements), matching
# the approach used by the Anthropic fetcher.
#
# Article permalinks live under /index/<slug> (absolute or relative). We ignore
# section/nav links such as "/news" or "/news/product".
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="((?:https?://openai\.com)?/index/[^"#?]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.DOTALL | re.IGNORECASE)
_TIME_RE = re.compile(r"<time[^>]*>(.*?)</time>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
# Text-node tags that may carry the title/date when there's no heading element.
_TEXTNODE_RE = re.compile(r"<(?:span|div|p)[^>]*>(.*?)</(?:span|div|p)>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# A day-granular date label, e.g. "Jan 5, 2025" or "January 5, 2025".
_DATE_TEXT_RE = re.compile(
    r"\b([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})\b",
)


class OpenAINewsParseError(RuntimeError):
    """Raised when the OpenAI news page contains no recognizable article cards.

    This signals a structural break — a bot-challenge shell, a markup change, or
    an empty/blocked response — as opposed to legitimately having no *recent*
    articles (which yields an empty list, not an error).
    """


def _strip_tags(fragment: str) -> str:
    """Remove HTML tags, unescape entities, and collapse whitespace."""
    text = _TAG_RE.sub(" ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: str) -> datetime | None:
    """Parse OpenAI's day-granular date labels, e.g. 'Jan 5, 2025'."""
    raw = _strip_tags(raw)
    match = _DATE_TEXT_RE.search(raw)
    if not match:
        return None
    label = match.group(1)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(label, fmt)
        except ValueError:
            continue
    return None


def _extract_title(inner: str) -> str:
    """Pull the article title out of a card's inner HTML.

    Prefers a heading element; falls back to the longest non-date text node so
    the parser survives OpenAI shipping card markup without an <h*> tag.
    """
    heading = _HEADING_RE.search(inner)
    if heading:
        title = _strip_tags(heading.group(1))
        if title:
            return title

    candidates: list[str] = []
    for frag in _TEXTNODE_RE.findall(inner):
        text = _strip_tags(frag)
        if text and not _DATE_TEXT_RE.fullmatch(text):
            candidates.append(text)
    if candidates:
        return max(candidates, key=len)

    return _strip_tags(inner)


def _extract_date(inner: str) -> datetime | None:
    """Find the publish date from a <time> tag or an inline date label."""
    time_match = _TIME_RE.search(inner)
    if time_match:
        parsed = _parse_date(time_match.group(1))
        if parsed:
            return parsed
    # Fall back to scanning the whole card for a "Mon DD, YYYY" style label.
    return _parse_date(inner)


def parse_openai_news(
    html_text: str,
    base_url: str = OPENAI_NEWS_URL,
    now: datetime | None = None,
    lookback_hours: int = OPENAI_LOOKBACK_HOURS,
    max_articles: int = MAX_ARTICLES_PER_SOURCE,
) -> list[dict]:
    """Parse the OpenAI news index HTML into standard article dicts.

    Pure function (no network) so it can be unit-tested against a fixture.
    Deduplicates by permalink, applies the lookback cutoff (articles with an
    unparseable date are kept), and caps at ``max_articles``.

    Raises:
        OpenAINewsParseError: if the page contains zero recognizable article
            anchors. This distinguishes a structural break / bot-challenge shell
            from a page that simply has no *recent* announcements (which returns
            an empty list). Callers must treat this as a loud failure, not a
            silent empty result.
    """
    now = now or datetime.now()
    # Page dates are day-granular (parsed to 00:00), so floor the cutoff to the
    # start of the day. Otherwise an announcement posted today would be dropped
    # a few hours into the lookback window purely because of the 00:00 timestamp.
    cutoff = (now - timedelta(hours=lookback_hours)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    matches = _ANCHOR_RE.findall(html_text or "")
    if not matches:
        raise OpenAINewsParseError(
            "No /index/<slug> article anchors found on the OpenAI news page — "
            "the page structure may have changed or the response was a "
            "bot-challenge/empty shell."
        )

    articles: list[dict] = []
    seen_paths: set[str] = set()

    for href, inner in matches:
        title = _extract_title(inner)
        if not title:
            continue  # anchors without any text are nav/"read more" links

        url = urljoin(base_url, href)
        path = urlparse(url).path.rstrip("/")
        if path in seen_paths:
            continue

        pub_date = _extract_date(inner)
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


def fetch_openai_news() -> list[dict]:
    """Fetch recent announcements from the OpenAI news page.

    Fails *loudly but non-fatally*: network, HTTP, and structural-parse errors
    are logged at ERROR/CRITICAL level (visible in the daily run logs and to
    sys-ops alerting) and return [] so the rest of the fetch cycle proceeds.
    This is deliberate — a single flaky source must not silently vanish, but it
    also must not abort the whole digest.
    """
    try:
        resp = requests.get(
            OPENAI_NEWS_URL,
            timeout=OPENAI_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 ai_news_agent/1.0"},
        )
        resp.raise_for_status()
    except requests.Timeout:
        _logger.error("[OpenAI] Timeout fetching %s, skipping this source.", OPENAI_NEWS_URL)
        return []
    except Exception as e:
        _logger.error("[OpenAI] Error fetching %s: %s", OPENAI_NEWS_URL, e)
        return []

    try:
        articles = parse_openai_news(resp.text, lookback_hours=OPENAI_LOOKBACK_HOURS)
    except OpenAINewsParseError as e:
        # Structural break: HTTP succeeded but nothing parseable came back.
        # This is the classic "silent failure" mode for a JS-rendered page, so
        # escalate to CRITICAL to guarantee it surfaces.
        _logger.critical("[OpenAI] Failed to parse news page (%s): %s", OPENAI_NEWS_URL, e)
        return []
    except Exception as e:
        _logger.error("[OpenAI] Unexpected error parsing news page: %s", e)
        return []

    _logger.info("[OpenAI] Found %d articles in last %dh", len(articles), OPENAI_LOOKBACK_HOURS)
    print(f"[OpenAI] Found {len(articles)} articles in last {OPENAI_LOOKBACK_HOURS}h")
    return articles
