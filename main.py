import json
import os
import sys
import time
from datetime import datetime

from fetchers.rss_fetcher import fetch_rss_news
from fetchers.reddit_fetcher import fetch_reddit_news
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.twitter_fetcher import fetch_twitter_news
from fetchers.arxiv_fetcher import fetch_arxiv_papers
from fetchers.anthropic_fetcher import fetch_anthropic_news
from agent.dedup import dedupe
from agent.enricher import select_top_articles, enrich
from agent.summarizer import summarize_news
from agent.url_memory import load_seen, save_seen, filter_unseen
from delivery.telegram import send_telegram_message

ENRICH_TOP_N = 10
STATUS_FILE = "logs/last-run.json"
SEEN_URLS_FILE = "logs/seen-urls.json"


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, path)


def run_agent() -> int:
    """Fetch news, summarize with Claude, deliver via Telegram.

    Returns shell-style exit code (0 = success, non-zero = some failure).
    Always writes logs/last-run.json so the cron wrapper has stats for sys-ops alerts.
    """
    started_monotonic = time.monotonic()
    started_at = datetime.now()
    print(f"[{started_at.strftime('%Y-%m-%d %H:%M:%S')}] Starting AI news agent...")

    status: dict = {
        "status": "running",
        "started_at": started_at.isoformat(),
        "ended_at": None,
        "duration_sec": None,
        "counts": {"rss": 0, "reddit": 0, "tavily": 0, "twitter": 0, "arxiv": 0, "anthropic": 0},
        "total_collected": 0,
        "after_dedupe": 0,
        "enriched_succeeded": 0,
        "enriched_attempted": 0,
        "top_headlines": [],
        "digest_preview": "",
        "delivery_ok": None,
        "error": None,
    }

    exit_code = 0

    try:
        all_articles: list[dict] = []

        print("Fetching RSS feeds...")
        rss = fetch_rss_news()
        status["counts"]["rss"] = len(rss)
        print(f"  {len(rss)} articles")
        all_articles.extend(rss)

        print("Fetching Reddit...")
        reddit = fetch_reddit_news()
        status["counts"]["reddit"] = len(reddit)
        print(f"  {len(reddit)} posts")
        all_articles.extend(reddit)

        print("Fetching web news via Tavily...")
        tavily = fetch_tavily_news()
        status["counts"]["tavily"] = len(tavily)
        print(f"  {len(tavily)} articles")
        all_articles.extend(tavily)

        print("Fetching Twitter/X...")
        twitter = fetch_twitter_news()
        status["counts"]["twitter"] = len(twitter)
        print(f"  {len(twitter)} tweets")
        all_articles.extend(twitter)

        print("Fetching ArXiv papers...")
        arxiv = fetch_arxiv_papers()
        status["counts"]["arxiv"] = len(arxiv)
        print(f"  {len(arxiv)} papers")
        all_articles.extend(arxiv)

        print("Fetching Anthropic News...")
        anthropic = fetch_anthropic_news()
        status["counts"]["anthropic"] = len(anthropic)
        print(f"  {len(anthropic)} articles")
        all_articles.extend(anthropic)

        status["total_collected"] = len(all_articles)
        print(f"\nTotal collected: {len(all_articles)} articles")

        if not all_articles:
            print("No articles collected. Check your API keys and internet connection.")
            status["status"] = "no_articles"
            status["error"] = "No articles collected from any source"
            return 1

        before = len(all_articles)
        all_articles = dedupe(all_articles)
        print(f"Deduplicated: {before} → {len(all_articles)} articles")

        seen = load_seen(SEEN_URLS_FILE)
        pre_memory = len(all_articles)
        all_articles = filter_unseen(all_articles, seen)
        status["after_dedupe"] = len(all_articles)
        status["filtered_by_memory"] = pre_memory - len(all_articles)
        print(f"URL memory: filtered {pre_memory} → {len(all_articles)} (dropped {pre_memory - len(all_articles)} seen in last 7d)")

        if not all_articles:
            print("All articles already seen in last 7 days — nothing fresh to digest.")
            status["status"] = "no_fresh_articles"
            status["error"] = "Every article was already in the 7-day URL memory"
            return 1

        print(f"Selecting top {ENRICH_TOP_N} for enrichment...")
        top_indices = select_top_articles(all_articles, n=ENRICH_TOP_N)
        print(f"  selected {len(top_indices)} indices")

        # Snapshot top headlines BEFORE enrich() rewrites the summary fields with extracted body text.
        status["top_headlines"] = [
            {"title": all_articles[i].get("title", ""), "source": all_articles[i].get("source", "")}
            for i in top_indices[:5]
        ]

        print("Enriching with Tavily extract...")
        all_articles, succeeded, attempted = enrich(all_articles, top_indices)
        status["enriched_succeeded"] = succeeded
        status["enriched_attempted"] = attempted
        print(f"  enriched {succeeded}/{attempted} articles")

        print("Summarizing with Claude...")
        digest = summarize_news(all_articles)

        status["digest_preview"] = digest[:400]

        # Persist the URLs we just digested so they're suppressed from the next 7 days.
        # Only after summarisation succeeds — don't poison memory on transient LLM errors.
        try:
            save_seen(SEEN_URLS_FILE, [a.get("url", "") for a in all_articles])
        except Exception as e:
            print(f"[url_memory] save failed (non-fatal): {e}")

        print("\n--- DIGEST PREVIEW ---")
        print(digest[:600] + "\n[...]" if len(digest) > 600 else digest)
        print("--- END PREVIEW ---\n")

        print("Sending to Telegram...")
        delivery_ok = send_telegram_message(digest)
        status["delivery_ok"] = delivery_ok
        if delivery_ok:
            print("✓ Message sent successfully!")
            status["status"] = "ok"
        else:
            print("✗ Failed to send Telegram message.")
            status["status"] = "delivery_failed"
            status["error"] = "send_telegram_message returned False"
            exit_code = 2

        # Best-effort: judge today's digest. Never blocks the run.
        try:
            from eval.daily_eval import evaluate_today

            eval_result = evaluate_today(all_articles, digest)
            status["eval_score"] = eval_result.get("score")
            status["eval_reasoning"] = eval_result.get("reasoning")
            if eval_result.get("error"):
                status["eval_error"] = eval_result["error"]
            print(f"[eval] score={status['eval_score']} reasoning={status.get('eval_reasoning', '')[:120]}")
        except Exception as e:
            status["eval_error"] = f"{type(e).__name__}: {e}"
            print(f"[eval] failed (non-fatal): {e}")

    except Exception as e:
        status["status"] = "error"
        status["error"] = f"{type(e).__name__}: {e}"
        print(f"FATAL: {status['error']}")
        exit_code = 3
    finally:
        ended_at = datetime.now()
        status["ended_at"] = ended_at.isoformat()
        status["duration_sec"] = round(time.monotonic() - started_monotonic, 2)
        try:
            _atomic_write_json(STATUS_FILE, status)
        except Exception as e:
            print(f"[status] Failed to write {STATUS_FILE}: {e}")

        # Flush Langfuse traces before exit (important for script/serverless mode)
        try:
            from langfuse import Langfuse

            Langfuse().flush()
        except Exception:
            pass

    return exit_code


def schedule_daily():
    """Run the agent on a daily schedule, with an immediate first run."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from config import DAILY_HOUR, DAILY_MINUTE

    print(f"Scheduling daily digest at {DAILY_HOUR:02d}:{DAILY_MINUTE:02d}...")
    run_agent()  # Run immediately on start

    scheduler = BlockingScheduler()
    scheduler.add_job(run_agent, "cron", hour=DAILY_HOUR, minute=DAILY_MINUTE)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        schedule_daily()
    else:
        sys.exit(run_agent())
