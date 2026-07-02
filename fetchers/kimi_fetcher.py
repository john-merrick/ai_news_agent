import html
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests

from config import (
    KIMI_NEWS_URL,
    KIMI_LOOKBACK_HOURS,
    MAX_ARTICLES_PER_SOURCE,
)

KIMI_TIMEOUT = 10  # seconds
SOURCE = "Kimi Blog"

# Module logger. The daily agent runs headless via cron, so failures MUST be
# emitted at ERROR/CRITICAL level (not just print) to be visible in the captured
# logs and surfaced by the sys-ops alerting on the wrapper script.
_logger = logging.getLogger(__name__)

# Kimi's (Moonshot AI) /blog index is a server-rendered app. Each post is an
# anchor to a /blog/<slug> permalink carrying a "menu-card" wrapper with a
# card-title heading, an optional card-desc body, and a card-date label in
# "YYYY/MM/DD" format. We parse with stdlib regex to avoid adding a new
# dependency (beautifulsoup4 is not part of requirements), mirroring the
# Anthropic/OpenAI fetchers.
#
# Article permalinks live under /blog/<slug> (absolute or relative). The bare
# /blog/ index and the sidebar nav links (which use plain <span> text instead of
# a card-title heading) are ignored — nav links have no card-title so they fall
# out naturally.
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="((?:https?://www\.kimi\.com)?/blog/[^"#?]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
# Class-keyed extractors. Titles/dates/descriptions are text-only nodes, so a
# non-greedy up-to-next-tag capture is sufficient and avoids nested-tag issues.
_TITLE_RE = re.compile(r'class="[^"]*\bcard-title\b[^"]*"[^>]*>([^<]*)<', re.IGNORECASE)
_DESC_RE = re.compile(r'class="[^"]*\bcard-desc\b[^"]*"[^>]*>([^<]*)<', re.IGNORECASE)
_DATE_RE = re.compile(r'class="[^"]*\bcard-date\b[^"]*"[^>]*>([^<]*)<', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class KimiFetchError(RuntimeError):
    """Raised when fetching the Kimi blog fails.

    Unlike the graceful Anthropic/OpenAI sources (which log and return []),
    the Kimi source is required to fail *loudly*: network errors, non-200 HTTP
    responses, and structural parse failures all raise so the caller cannot
    silently ignore a broken source.
    """


class KimiNewsParseError(KimiFetchError):
    """Raised when the Kimi blog page contains no recognizable article cards.

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
    """Parse Kimi's day-granular date labels, e.g. '2026/04/20'."""
    raw = _strip_tags(raw)
    if not raw:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_kimi_news(
    html_text: str,
    base_url: str = KIMI_NEWS_URL,
    now: datetime | None = None,
    lookback_hours: int = KIMI_LOOKBACK_HOURS,
    max_articles: int = MAX_ARTICLES_PER_SOURCE,
) -> list[dict]:
    """Parse the Kimi blog index HTML into standard article dicts.

    Pure function (no network) so it can be unit-tested against a fixture.
    Deduplicates by permalink, applies the lookback cutoff (articles with an
    unparseable date are kept), and caps at ``max_articles``.

    Raises:
        KimiNewsParseError: if the page contains zero recognizable article
            cards. This distinguishes a structural break / bot-challenge shell
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

    articles: list[dict] = []
    seen_paths: set[str] = set()
    saw_card = False

    for href, inner in _ANCHOR_RE.findall(html_text or ""):
        title_match = _TITLE_RE.search(inner)
        if not title_match:
            continue  # nav/sidebar links have no card-title heading
        title = _strip_tags(title_match.group(1))
        if not title:
            continue

        # A recognizable article card (has a card-title) was present, even if it
        # later gets filtered by the cutoff or the dedup/cap logic.
        saw_card = True

        url = urljoin(base_url, href)
        path = urlparse(url).path.rstrip("/")
        if path in seen_paths:
            continue

        date_match = _DATE_RE.search(inner)
        pub_date = _parse_date(date_match.group(1)) if date_match else None
        if pub_date and pub_date < cutoff:
            continue

        desc_match = _DESC_RE.search(inner)
        summary = _strip_tags(desc_match.group(1)) if desc_match else ""

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

    if not saw_card:
        raise KimiNewsParseError(
            "No /blog/<slug> article cards found on the Kimi blog page — the "
            "page structure may have changed or the response was a "
            "bot-challenge/empty shell."
        )

    return articles


def fetch_kimi_news() -> list[dict]:
    """Fetch recent announcements from the Kimi (Moonshot AI) blog.

    Fails *loudly*: network errors, non-200 HTTP responses, and structural parse
    failures are logged at ERROR/CRITICAL level and re-raised as
    ``KimiFetchError``. This source is deliberately NOT swallowed the way the
    Anthropic/OpenAI fetchers are — a broken Kimi source must surface as an
    explicit failure of the fetch step rather than a silent empty result.

    Returns:
        A (possibly empty) list of article dicts when the page loads and parses
        successfully but has no *recent* posts. An empty list here is a genuine
        "no new posts", never a masked error.

    Raises:
        KimiFetchError: on any network error, non-200 HTTP status, or parse
            failure (including the structural-break ``KimiNewsParseError``).
    """
    try:
        resp = requests.get(
            KIMI_NEWS_URL,
            timeout=KIMI_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 ai_news_agent/1.0"},
        )
        resp.raise_for_status()
    except requests.Timeout as e:
        _logger.error("[Kimi] Timeout fetching %s", KIMI_NEWS_URL)
        raise KimiFetchError(f"Timeout fetching {KIMI_NEWS_URL}") from e
    except requests.RequestException as e:
        # Covers connection errors AND non-200 responses (raise_for_status).
        _logger.error("[Kimi] Error fetching %s: %s", KIMI_NEWS_URL, e)
        raise KimiFetchError(f"Error fetching {KIMI_NEWS_URL}: {e}") from e

    try:
        articles = parse_kimi_news(resp.text, lookback_hours=KIMI_LOOKBACK_HOURS)
    except KimiNewsParseError as e:
        # Structural break: HTTP succeeded but nothing parseable came back.
        # This is the classic "silent failure" mode for a rendered page, so
        # escalate to CRITICAL and re-raise to fail the fetch step loudly.
        _logger.critical("[Kimi] Failed to parse blog page (%s): %s", KIMI_NEWS_URL, e)
        raise
    except Exception as e:
        _logger.error("[Kimi] Unexpected error parsing blog page: %s", e)
        raise KimiFetchError(f"Unexpected error parsing {KIMI_NEWS_URL}: {e}") from e

    _logger.info("[Kimi] Found %d articles in last %dh", len(articles), KIMI_LOOKBACK_HOURS)
    print(f"[Kimi] Found {len(articles)} articles in last {KIMI_LOOKBACK_HOURS}h")
    return articles
