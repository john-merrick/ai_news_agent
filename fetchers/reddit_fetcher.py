import praw
from datetime import datetime
from config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    LOOKBACK_HOURS,
    MAX_ARTICLES_PER_SOURCE,
)

SUBREDDITS = ["artificial", "MachineLearning", "LocalLLaMA", "singularity", "OpenAI"]
MIN_SCORE = 50


def fetch_reddit_news() -> list[dict]:
    """Fetch top AI posts from Reddit. Skips if credentials are not configured."""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        print("[Reddit] No credentials configured, skipping.")
        return []

    try:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
    except Exception as e:
        print(f"[Reddit] Setup error: {e}")
        return []

    cutoff = datetime.now().timestamp() - (LOOKBACK_HOURS * 3600)
    articles = []

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.hot(limit=MAX_ARTICLES_PER_SOURCE):
                if post.created_utc < cutoff:
                    continue
                if post.score < MIN_SCORE:
                    continue
                articles.append({
                    "title": post.title,
                    "url": post.url,
                    "summary": post.selftext[:500] if post.is_self else "",
                    "source": f"r/{sub_name}",
                    "published": datetime.fromtimestamp(post.created_utc).isoformat(),
                    "score": post.score,
                })
        except Exception as e:
            if "401" in str(e) or "403" in str(e):
                print("[Reddit] Invalid credentials, skipping.")
                return articles
            print(f"[Reddit] Error fetching r/{sub_name}: {e}")

    return articles
