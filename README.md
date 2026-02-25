# AI News Agent

A daily AI news digest delivered to your Telegram. Fetches articles from RSS feeds, Exa web search, Reddit, and Twitter, summarizes them with Claude, and sends a curated message every morning.

## How it works

1. **Fetch** — collects AI news from multiple sources (RSS, Exa, Reddit, Twitter)
2. **Summarize** — Claude Sonnet curates and writes a digest (~500 words)
3. **Deliver** — sends the digest to a Telegram chat via bot

## Setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/john-merrick/ai_news_agent.git
cd ai_news_agent
python3 -m venv venv
source venv/bin/activate
pip install langchain-anthropic langchain-core exa-py feedparser praw python-telegram-bot requests python-dotenv apscheduler
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | [console.anthropic.com](https://console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN` | Yes | Create a bot via [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Yes | Your chat/channel ID |
| `EXA_API_KEY` | Yes | [exa.ai](https://exa.ai) |
| `REDDIT_CLIENT_ID` | No | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | No | Same as above |
| `TWITTER_BEARER_TOKEN` | No | [developer.twitter.com](https://developer.twitter.com) |

### 3. Run once to test

```bash
python main.py
```

## Scheduling (macOS)

A `launchd` plist is provided to run the agent daily at 7 AM.

```bash
cp com.ainewsagent.daily.plist ~/Library/LaunchAgents/
# Edit the plist to update paths if needed, then:
launchctl load ~/Library/LaunchAgents/com.ainewsagent.daily.plist
```

**Verify it's registered:**
```bash
launchctl list | grep ainewsagent
```

**Trigger a manual run:**
```bash
launchctl start com.ainewsagent.daily
```

**Check logs:**
```bash
tail -f logs/agent.log
cat logs/agent.error.log
```

**Unload/stop:**
```bash
launchctl unload ~/Library/LaunchAgents/com.ainewsagent.daily.plist
```

> If your Mac is asleep at 7 AM, the job runs when it next wakes. If the Mac is off, the job is skipped until the next day.

## Project structure

```
ai_news_agent/
├── main.py                  # Entry point
├── config.py                # Loads .env, RSS feeds, settings
├── agent/
│   ├── prompts.py           # Claude system + user prompts
│   └── summarizer.py        # LangChain + Claude summarization
├── fetchers/
│   ├── rss_fetcher.py       # RSS feeds (HN, VentureBeat, ArXiv, etc.)
│   ├── web_fetcher.py       # Exa web search + research lab blogs
│   ├── reddit_fetcher.py    # Reddit (optional)
│   └── twitter_fetcher.py   # Twitter/X (optional)
├── delivery/
│   └── telegram.py          # Telegram Bot API delivery
└── logs/                    # Runtime logs (gitignored)
```

## News sources

**RSS feeds** (no API key needed):
- Hacker News (AI/ML posts)
- VentureBeat AI
- The Verge AI
- HuggingFace Blog
- Google DeepMind Blog
- ArXiv CS.AI
- MIT AI News

**Exa web search:**
- General AI news queries
- Targeted search on lab blogs: OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, Stability AI, HuggingFace

**Reddit** (optional): r/artificial, r/MachineLearning, r/LocalLLaMA, r/singularity, r/OpenAI

**Twitter/X** (optional): recent tweets about AI model releases and announcements
