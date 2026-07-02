from datetime import datetime

import requests

from fetchers import anthropic_fetcher
from fetchers.anthropic_fetcher import (
    fetch_anthropic_news,
    parse_anthropic_news,
    SOURCE,
)

# Mirrors the real Anthropic /news markup: a featured anchor (h2 title) plus
# side-link cards (h4 title), each with a <time> date and a body <p>. Includes
# a duplicate permalink and a stale (old-dated) card to exercise dedup + cutoff,
# and a bare nav anchor with no heading that must be ignored.
SAMPLE_HTML = """
<div class="grid">
  <a href="/news/redeploying-fable-5" class="content">
    <h2 class="featuredTitle">Redeploying Fable 5</h2>
    <div class="meta"><span class="caption bold">Announcements</span>
      <time class="date">Jun 30, 2026</time></div>
    <p class="body-3 serif body">Fable 5 returns globally July 1, plus a jailbreak severity framework.</p>
  </a>
  <a href="/news/claude-sonnet-5" class="sideLink">
    <div class="meta"><span class="caption bold">Product</span>
      <time class="date">Jun 30, 2026</time></div>
    <h4 class="title">Introducing Claude Sonnet 5</h4>
    <p class="body-3 serif body">Sonnet 5 delivers frontier performance across coding &amp; agents.</p>
  </a>
  <a href="/news/claude-sonnet-5/" class="sideLink">
    <h4 class="title">Introducing Claude Sonnet 5 (duplicate)</h4>
    <time class="date">Jun 30, 2026</time>
  </a>
  <a href="/news/ancient-history" class="sideLink">
    <h4 class="title">A very old announcement</h4>
    <time class="date">Jan 01, 2020</time>
    <p class="body">This should be filtered out by the lookback cutoff.</p>
  </a>
  <a href="/news" class="nav">All news</a>
</div>
"""

NOW = datetime(2026, 7, 1, 8, 0, 0)


def _parse():
    return parse_anthropic_news(SAMPLE_HTML, now=NOW, lookback_hours=48, max_articles=10)


def test_extracts_core_fields():
    articles = _parse()
    by_url = {a["url"]: a for a in articles}

    featured = by_url["https://www.anthropic.com/news/redeploying-fable-5"]
    assert featured["title"] == "Redeploying Fable 5"
    assert featured["source"] == SOURCE
    assert featured["published"] == "2026-06-30T00:00:00"
    assert "Fable 5 returns globally" in featured["summary"]

    sonnet = by_url["https://www.anthropic.com/news/claude-sonnet-5"]
    assert sonnet["title"] == "Introducing Claude Sonnet 5"
    # HTML entities are unescaped in the summary.
    assert "coding & agents" in sonnet["summary"]


def test_schema_matches_pipeline_contract():
    # Must match the dict shape produced by the other fetchers.
    expected_keys = {"title", "url", "summary", "source", "published"}
    for art in _parse():
        assert set(art.keys()) == expected_keys
        assert isinstance(art["summary"], str)
        assert len(art["summary"]) <= 500


def test_deduplicates_by_permalink():
    urls = [a["url"] for a in _parse()]
    assert urls.count("https://www.anthropic.com/news/claude-sonnet-5") == 1


def test_applies_lookback_cutoff():
    titles = [a["title"] for a in _parse()]
    assert "A very old announcement" not in titles


def test_ignores_anchors_without_heading():
    urls = [a["url"] for a in _parse()]
    assert "https://www.anthropic.com/news" not in urls


def test_respects_max_articles():
    articles = parse_anthropic_news(SAMPLE_HTML, now=NOW, lookback_hours=48, max_articles=1)
    assert len(articles) == 1


def test_fetch_handles_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(anthropic_fetcher.requests, "get", boom)
    assert fetch_anthropic_news() == []


def test_fetch_handles_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(anthropic_fetcher.requests, "get", timeout)
    assert fetch_anthropic_news() == []


def test_fetch_parses_successful_response(monkeypatch):
    class FakeResp:
        text = SAMPLE_HTML

        def raise_for_status(self):
            return None

    monkeypatch.setattr(anthropic_fetcher.requests, "get", lambda *a, **k: FakeResp())
    # Wide lookback so the fixture's dated cards survive regardless of run date.
    monkeypatch.setattr(anthropic_fetcher, "ANTHROPIC_LOOKBACK_HOURS", 24 * 365 * 20)
    articles = fetch_anthropic_news()
    assert len(articles) >= 2
    assert all(a["source"] == SOURCE for a in articles)
