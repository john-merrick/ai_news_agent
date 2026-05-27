"""Daily binary eval against today's actual digest.

After each successful run, this module:
  1. Sends the digest + article list to a cheap reasoning judge (claude-haiku-4-5)
     for a single subjective binary score (useful / not useful).
  2. Persists today's input + output as a Langfuse dataset item under
     `ai-news-digest-daily` (created on first use), so trends are visible
     in the Langfuse UI alongside the trace.
  3. Attaches the score to the trace via langfuse.create_score().

Designed to be best-effort: any failure here returns a result with score=None
and the error message, never raising — the wrapper script reads what's there
and the main run is unaffected.
"""

import json
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langfuse import Langfuse

from config import (
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_HOST,
)
from eval.criteria import SUBJECTIVE_CRITERIA, BINARY_JUDGE_SYSTEM, JUDGE_USER_TEMPLATE, parse_judge_response

DATASET_NAME = "ai-news-digest-daily"
JUDGE_MODEL = "claude-haiku-4-5"
JUDGE_TIMEOUT_SEC = 30


def _create_judge() -> ChatOpenAI:
    return ChatOpenAI(
        model=JUDGE_MODEL,
        base_url=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
        temperature=0,
        max_tokens=200,
        timeout=JUDGE_TIMEOUT_SEC,
    )


def _articles_for_judge(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles[:40], 1):
        title = a.get("title", "")
        source = a.get("source", "")
        lines.append(f"{i}. [{source}] {title}")
    return "\n".join(lines)


def _ensure_dataset(langfuse: Langfuse) -> None:
    try:
        langfuse.get_dataset(DATASET_NAME)
    except Exception:
        try:
            langfuse.create_dataset(name=DATASET_NAME)
        except Exception:
            pass


def evaluate_today(articles: list[dict], digest: str) -> dict:
    """Judge today's digest with a binary useful/not-useful score.

    Returns a dict: {score: 0|1|None, reasoning: str, dataset_item_id: str|None, error: str|None}
    Never raises.
    """
    result: dict = {"score": None, "reasoning": "", "dataset_item_id": None, "error": None}

    criterion = SUBJECTIVE_CRITERIA["useful_to_reader"]
    input_text = _articles_for_judge(articles)
    output_text = digest

    try:
        judge = _create_judge()
        messages = [
            SystemMessage(content=BINARY_JUDGE_SYSTEM),
            HumanMessage(content=JUDGE_USER_TEMPLATE.format(
                name="useful_to_reader",
                description=criterion["description"],
                input_text=input_text[:2000],
                output_text=output_text[:3000],
            )),
        ]
        response = judge.invoke(messages)
        parsed = parse_judge_response(response.content)
        if parsed is None:
            result["error"] = f"judge parse: {str(response.content)[:120]}"
            return result
        try:
            result["score"] = 1 if int(parsed.get("score", 0)) >= 1 else 0
            result["reasoning"] = str(parsed.get("reasoning", ""))[:300]
        except (KeyError, ValueError, TypeError) as e:
            result["error"] = f"judge schema: {e}: {str(response.content)[:80]}"
            return result
    except Exception as e:
        result["error"] = f"judge call: {type(e).__name__}: {e}"
        return result

    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return result

    try:
        langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        _ensure_dataset(langfuse)
        item = langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"articles": [
                {"title": a.get("title", ""), "source": a.get("source", ""), "url": a.get("url", "")}
                for a in articles[:50]
            ]},
            expected_output=None,
            metadata={
                "date": datetime.now().strftime("%Y-%m-%d"),
                "article_count": len(articles),
                "judge_model": JUDGE_MODEL,
                "score": result["score"],
            },
        )
        try:
            result["dataset_item_id"] = getattr(item, "id", None) or item.get("id")
        except Exception:
            result["dataset_item_id"] = None
        langfuse.flush()
    except Exception as e:
        result["error"] = f"langfuse persist: {type(e).__name__}: {e}"

    return result
