import os
from dotenv import load_dotenv

load_dotenv()

# LiteLLM Proxy (preferred — routes through local proxy for cost tracking + tracing)
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")

# Langfuse Tracing (optional)
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3100")

# Anthropic (fallback — used if LITELLM_API_KEY is not set)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Exa
EXA_API_KEY = os.getenv("EXA_API_KEY")

# Reddit (optional)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ai_news_agent/1.0")

# Twitter (optional)
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

# Schedule (24h format)
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))

# News settings
MAX_ARTICLES_PER_SOURCE = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "10"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))

# RSS feeds to poll (ArXiv is handled separately via the API fetcher)
RSS_FEEDS = [
    # News & industry
    "https://hnrss.org/newest?q=AI+LLM+machine+learning&count=20",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    # Research labs & orgs
    "https://huggingface.co/blog/feed.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://blog.research.google/feeds/posts/default",
    "https://news.mit.edu/rss/topic/artificial-intelligence",
    # Research curation
    "https://paperswithcode.com/rss",
    # Newsletters
    "https://importai.substack.com/feed",
    "https://aisnakeoil.substack.com/feed",
]

# Research/lab domains for targeted Exa search
RESEARCH_COMPANY_DOMAINS = [
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "ai.meta.com",
    "mistral.ai",
    "ai.google",
    "blog.google",
    "stability.ai",
    "huggingface.co",
]
