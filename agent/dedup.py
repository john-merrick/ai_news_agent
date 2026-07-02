from urllib.parse import urlparse, urlunparse

# Higher rank = preferred when collapsing duplicates.
# "First sources" (papers and direct lab/company posts) outrank aggregators.
_SOURCE_PRIORITY = {
    "ArXiv": 100,
    "Anthropic News": 95,  # first-party lab announcements
    "Research Lab Blog": 90,
    "RSS": 70,
    "Reddit": 50,
    "Twitter/X": 40,
    "Tavily News": 30,
}


def _canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip().lower()
    host = (p.netloc or "").lower().lstrip("www.")
    path = (p.path or "").rstrip("/")
    return urlunparse((p.scheme.lower() or "https", host, path, "", "", ""))


def _priority(article: dict) -> int:
    return _SOURCE_PRIORITY.get(article.get("source", ""), 0)


def dedupe(articles: list[dict]) -> list[dict]:
    """Collapse duplicate URLs, keeping the entry from the highest-priority source.

    Articles without a URL are kept as-is (no reliable identity).
    """
    by_url: dict[str, dict] = {}
    no_url: list[dict] = []

    for art in articles:
        canon = _canonical_url(art.get("url", ""))
        if not canon:
            no_url.append(art)
            continue
        existing = by_url.get(canon)
        if existing is None or _priority(art) > _priority(existing):
            by_url[canon] = art

    return list(by_url.values()) + no_url
