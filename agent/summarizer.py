from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
from agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from config import (
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
    ANTHROPIC_API_KEY,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_HOST,
)

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


def _create_llm():
    """Create LLM client — LiteLLM proxy preferred, direct Anthropic as fallback."""
    if LITELLM_API_KEY:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model="claude-sonnet",
            base_url=LITELLM_BASE_URL,
            api_key=LITELLM_API_KEY,
            max_tokens=1500,
            extra_body={
                "metadata": {
                    "product_area": "ai_news_agent",
                    "workflow": "ai_news_agent",
                },
            },
        )

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=ANTHROPIC_API_KEY,
        max_tokens=1500,
    )


def _get_langfuse_handler():
    """Create Langfuse v4 callback handler if configured."""
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as e:
        print(f"[Langfuse] Failed to create handler: {e}")
        return None


def summarize_news(articles: list[dict]) -> str:
    """Use Claude to summarize and curate articles into a Telegram-ready digest."""
    if not articles:
        return "No AI news articles were collected today."

    llm = _create_llm()

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

    langfuse_handler = _get_langfuse_handler()

    if langfuse_handler:
        from langfuse import propagate_attributes

        with propagate_attributes(
            user_id="ai_news_agent",
            tags=["ai_news_agent"],
            session_id=f"daily-digest-{datetime.now().strftime('%Y-%m-%d')}",
            trace_name="summarize_news",
            metadata={"article_count": str(len(articles))},
        ):
            response = llm.invoke(messages, config={"callbacks": [langfuse_handler]})
    else:
        response = llm.invoke(messages)

    return response.content
