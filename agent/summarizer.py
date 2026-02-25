from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
from agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from config import ANTHROPIC_API_KEY

MAX_ARTICLES = 50  # Cap sent to Claude to stay within token limits


def _format_articles(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles[:MAX_ARTICLES], 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}")
        lines.append(f"   URL: {a['url']}")
        if a.get("summary"):
            lines.append(f"   Summary: {a['summary'][:300]}")
        lines.append("")
    return "\n".join(lines)


def summarize_news(articles: list[dict]) -> str:
    """Use Claude to summarize and curate articles into a Telegram-ready digest."""
    if not articles:
        return "No AI news articles were collected today."

    llm = ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=ANTHROPIC_API_KEY,
        max_tokens=1500,
    )

    date_str = datetime.now().strftime("%B %d, %Y")
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=USER_PROMPT_TEMPLATE.format(
                articles_text=_format_articles(articles),
                date=date_str,
            )
        ),
    ]

    response = llm.invoke(messages)
    return response.content
