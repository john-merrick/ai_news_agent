import sys
from datetime import datetime

from fetchers.rss_fetcher import fetch_rss_news
from fetchers.reddit_fetcher import fetch_reddit_news
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.twitter_fetcher import fetch_twitter_news
from fetchers.arxiv_fetcher import fetch_arxiv_papers
from agent.dedup import dedupe
from agent.enricher import select_top_articles, enrich
from agent.summarizer import summarize_news
from delivery.telegram import send_telegram_message

ENRICH_TOP_N = 10


def run_agent():
    """Fetch news from all sources, summarize with Claude, and send via Telegram."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting AI news agent...")

    all_articles = []

    print("Fetching RSS feeds...")
    rss = fetch_rss_news()
    print(f"  {len(rss)} articles")
    all_articles.extend(rss)

    print("Fetching Reddit...")
    reddit = fetch_reddit_news()
    print(f"  {len(reddit)} posts")
    all_articles.extend(reddit)

    print("Fetching web news via Tavily...")
    tavily = fetch_tavily_news()
    print(f"  {len(tavily)} articles")
    all_articles.extend(tavily)

    print("Fetching Twitter/X...")
    twitter = fetch_twitter_news()
    print(f"  {len(twitter)} tweets")
    all_articles.extend(twitter)

    print("Fetching ArXiv papers...")
    arxiv = fetch_arxiv_papers()
    print(f"  {len(arxiv)} papers")
    all_articles.extend(arxiv)

    print(f"\nTotal collected: {len(all_articles)} articles")

    if not all_articles:
        print("No articles collected. Check your API keys and internet connection.")
        return

    before = len(all_articles)
    all_articles = dedupe(all_articles)
    print(f"Deduplicated: {before} → {len(all_articles)} articles")

    print(f"Selecting top {ENRICH_TOP_N} for enrichment...")
    top_indices = select_top_articles(all_articles, n=ENRICH_TOP_N)
    print(f"  selected {len(top_indices)} indices")

    print("Enriching with Tavily extract...")
    all_articles, succeeded, attempted = enrich(all_articles, top_indices)
    print(f"  enriched {succeeded}/{attempted} articles")

    print("Summarizing with Claude...")
    digest = summarize_news(all_articles)

    print("\n--- DIGEST PREVIEW ---")
    print(digest[:600] + "\n[...]" if len(digest) > 600 else digest)
    print("--- END PREVIEW ---\n")

    print("Sending to Telegram...")
    if send_telegram_message(digest):
        print("✓ Message sent successfully!")
    else:
        print("✗ Failed to send Telegram message.")

    # Flush Langfuse traces before exit (important for script/serverless mode)
    try:
        from langfuse import Langfuse

        Langfuse().flush()
    except Exception:
        pass


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
        run_agent()
