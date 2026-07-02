import logging
from datetime import datetime

import pytest
import requests

from fetchers import openai_fetcher
from fetchers.openai_fetcher import (
    fetch_openai_news,
    parse_openai_news,
    OpenAINewsParseError,
    SOURCE,
)

# Mirrors the real OpenAI /news markup: cards are anchors to /index/<slug>
# permalinks. Covers both a heading-based card and a heading-less span-based
# card (title carried in a <span>), a <time>-tagged date and an inline
# "Mon DD, YYYY" date label, plus a duplicate permalink and a stale card to
# exercise dedup + cutoff, and a section/nav anchor that must be ignored.
SAMPLE_HTML = """
<main>
  <a href="/index/introducing-gpt-6/" class="card">
    <h3 class="title">Introducing GPT-6</h3>
    <span class="tag">Product</span>
    <time datetime="2026-06-30">Jun 30, 2026</time>
    <p class="body">GPT-6 delivers frontier reasoning across coding &amp; agents.</p>
  </a>
  <a href="https://openai.com/index/new-safety-framework/" class="card">
    <span class="tag">Safety</span>
    <span class="title">A new safety framework</span>
    <span class="date">Jun 29, 2026</span>
    <p class="body">New evaluations for frontier model deployments.</p>
  </a>
  <a href="/index/introducing-gpt-6" class="card">
    <h3 class="title">Introducing GPT-6 (duplicate)</h3>
    <time>Jun 30, 2026</time>
  </a>
  <a href="/index/ancient-announcement/" class="card">
    <h3 class="title">A very old announcement</h3>
    <time>Jan 01, 2020</time>
    <p class="body">This should be filtered out by the lookback cutoff.</p>
  </a>
  <a href="/news/product" class="nav">Product news</a>
  <a href="/news" class="nav">All news</a>
</main>
"""

# A JS-rendered bot-challenge shell — HTTP 200 but no article anchors at all.
# This is OpenAI's real failure mode for plain HTTP clients and must NOT be
# treated as "no news today".
CHALLENGE_HTML = """
<html><head><meta http-equiv="refresh" content="360"></head>
<body><div class="container"><div class="logo"><svg></svg></div>
<div class="data"></div></div></body></html>
"""

NOW = datetime(2026, 7, 1, 8, 0, 0)


def _parse():
    return parse_openai_news(SAMPLE_HTML, now=NOW, lookback_hours=48, max_articles=10)


def test_extracts_core_fields_heading_card():
    by_url = {a["url"]: a for a in _parse()}
    art = by_url["https://openai.com/index/introducing-gpt-6/"]
    assert art["title"] == "Introducing GPT-6"
    assert art["source"] == SOURCE
    assert art["published"] == "2026-06-30T00:00:00"
    assert "frontier reasoning" in art["summary"]
    # HTML entities are unescaped in the summary.
    assert "coding & agents" in art["summary"]


def test_extracts_title_from_headingless_card():
    by_url = {a["url"]: a for a in _parse()}
    art = by_url["https://openai.com/index/new-safety-framework/"]
    assert art["title"] == "A new safety framework"
    assert art["published"] == "2026-06-29T00:00:00"


def test_schema_matches_pipeline_contract():
    # Must match the dict shape produced by the other fetchers.
    expected_keys = {"title", "url", "summary", "source", "published"}
    for art in _parse():
        assert set(art.keys()) == expected_keys
        assert isinstance(art["summary"], str)
        assert len(art["summary"]) <= 500


def test_deduplicates_by_permalink():
    urls = [a["url"] for a in _parse()]
    assert urls.count("https://openai.com/index/introducing-gpt-6/") == 1


def test_applies_lookback_cutoff():
    titles = [a["title"] for a in _parse()]
    assert "A very old announcement" not in titles


def test_ignores_section_and_nav_anchors():
    urls = [a["url"] for a in _parse()]
    assert all("/index/" in u for u in urls)
    assert "https://openai.com/news" not in urls
    assert "https://openai.com/news/product" not in urls


def test_respects_max_articles():
    articles = parse_openai_news(SAMPLE_HTML, now=NOW, lookback_hours=48, max_articles=1)
    assert len(articles) == 1


def test_parse_raises_on_structural_break():
    # A page with no /index/ article anchors is a structural failure, not
    # "no recent news" — it must raise so the caller can log loudly.
    with pytest.raises(OpenAINewsParseError):
        parse_openai_news(CHALLENGE_HTML, now=NOW, lookback_hours=48)


def test_fetch_handles_network_error(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(openai_fetcher.requests, "get", boom)
    with caplog.at_level(logging.ERROR, logger=openai_fetcher._logger.name):
        assert fetch_openai_news() == []
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_fetch_handles_timeout(monkeypatch, caplog):
    def timeout(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(openai_fetcher.requests, "get", timeout)
    with caplog.at_level(logging.ERROR, logger=openai_fetcher._logger.name):
        assert fetch_openai_news() == []
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_fetch_logs_critical_on_structural_break(monkeypatch, caplog):
    class FakeResp:
        text = CHALLENGE_HTML

        def raise_for_status(self):
            return None

    monkeypatch.setattr(openai_fetcher.requests, "get", lambda *a, **k: FakeResp())
    with caplog.at_level(logging.CRITICAL, logger=openai_fetcher._logger.name):
        # HTTP succeeded but nothing parseable — must NOT silently return [] with
        # no signal. It returns [] (so the pipeline continues) but logs CRITICAL.
        assert fetch_openai_news() == []
    assert any(r.levelno >= logging.CRITICAL for r in caplog.records)


def test_fetch_parses_successful_response(monkeypatch):
    class FakeResp:
        text = SAMPLE_HTML

        def raise_for_status(self):
            return None

    monkeypatch.setattr(openai_fetcher.requests, "get", lambda *a, **k: FakeResp())
    # Wide lookback so the fixture's dated cards survive regardless of run date.
    monkeypatch.setattr(openai_fetcher, "OPENAI_LOOKBACK_HOURS", 24 * 365 * 20)
    articles = fetch_openai_news()
    assert len(articles) >= 2
    assert all(a["source"] == SOURCE for a in articles)
