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

# Tavily (web/news search + article extraction)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

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

# Anthropic News page (https://www.anthropic.com/news).
# Anthropic does not publish an official RSS feed, so this is scraped directly
# from the server-rendered news index using stdlib parsing (no extra deps).
# Dates on the page are day-granular, so use a wider default window than the
# global 24h cutoff to avoid dropping announcements posted late the prior day.
ANTHROPIC_NEWS_URL = os.getenv("ANTHROPIC_NEWS_URL", "https://www.anthropic.com/news")
ANTHROPIC_LOOKBACK_HOURS = int(os.getenv("ANTHROPIC_LOOKBACK_HOURS", str(max(LOOKBACK_HOURS, 48))))

# Keyword filter applied to feeds that aren't AI-exclusive (HN frontpage, Simon Willison).
# Case-insensitive substring match on the entry title.
_AI_KEYWORD_FILTER = (
    "ai ", " ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
    "deepmind", "model", "neural", "transformer", "machine learning",
    "agent", "llama", "mistral", "diffusion", "hugging face", "mlx",
)

# RSS feeds to poll. Each entry is:
#   {"url": str, "lookback_hours": int|None, "client_filter": tuple|None}
# lookback_hours=None inherits LOOKBACK_HOURS. client_filter=None means no filter.
RSS_FEEDS = [
    # News & industry
    {"url": "https://hnrss.org/frontpage?points=100", "lookback_hours": None, "client_filter": _AI_KEYWORD_FILTER},
    {"url": "https://venturebeat.com/category/ai/feed/", "lookback_hours": None, "client_filter": None},
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lookback_hours": None, "client_filter": None},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lookback_hours": None, "client_filter": None},
    # Research labs & orgs
    {"url": "https://huggingface.co/blog/feed.xml", "lookback_hours": None, "client_filter": None},
    {"url": "https://deepmind.google/blog/rss.xml", "lookback_hours": None, "client_filter": None},
    {"url": "https://research.google/blog/rss/", "lookback_hours": None, "client_filter": None},
    {"url": "https://openai.com/blog/rss.xml", "lookback_hours": None, "client_filter": None},
    # Independent commentary (broad blogs — filter to AI titles only)
    {"url": "https://simonwillison.net/atom/everything/", "lookback_hours": None, "client_filter": _AI_KEYWORD_FILTER},
    # Newsletters (weekly cadence — extend window so the 24h cutoff doesn't drop them)
    {"url": "https://importai.substack.com/feed", "lookback_hours": 168, "client_filter": None},
    {"url": "https://aisnakeoil.substack.com/feed", "lookback_hours": 168, "client_filter": None},
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
