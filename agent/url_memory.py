"""Cross-day URL memory to stop the digest from repeating the same stories.

A 7-day rolling window of canonical URLs is persisted as JSON; new fetches are
filtered against the union of the last 7 days. Atomic write via os.replace so
an interrupted run can't corrupt the file. Single-writer (cron lock guarantees
no concurrent invocations) so no file locking needed.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Iterable

from agent.dedup import _canonical_url

DEFAULT_WINDOW_DAYS = 7


def load_seen(path: str, window_days: int = DEFAULT_WINDOW_DAYS) -> set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()

    cutoff = (datetime.now() - timedelta(days=window_days)).date()
    seen: set[str] = set()
    for day_str, urls in data.items():
        try:
            day = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= cutoff:
            seen.update(urls)
    return seen


def save_seen(path: str, urls: Iterable[str], window_days: int = DEFAULT_WINDOW_DAYS) -> None:
    """Merge today's URLs into the store and prune entries older than window_days."""
    today = datetime.now().date().isoformat()
    cutoff = (datetime.now() - timedelta(days=window_days)).date()

    data: dict[str, list[str]] = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    pruned: dict[str, list[str]] = {}
    for day_str, day_urls in data.items():
        try:
            day = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= cutoff:
            pruned[day_str] = day_urls

    canon_today = {_canonical_url(u) for u in urls if u}
    canon_today.discard("")
    existing = set(pruned.get(today, []))
    pruned[today] = sorted(existing | canon_today)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(pruned, f, indent=2)
    os.replace(tmp, path)


def filter_unseen(articles: list[dict], seen: set[str]) -> list[dict]:
    if not seen:
        return articles
    out = []
    for art in articles:
        canon = _canonical_url(art.get("url", ""))
        if canon and canon in seen:
            continue
        out.append(art)
    return out
