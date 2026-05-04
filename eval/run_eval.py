"""
Evaluate ai_news_agent digest quality using Langfuse v4 experiments.

Uses Langfuse's run_experiment() with LLM-as-judge evaluators that produce
scores attached to traced runs — visible in the Langfuse UI under Datasets.

Usage:
    cd /path/to/ai_news_agent
    python -m eval.run_eval

Prerequisites:
    1. Observability stack running (docker compose up -d)
    2. .env populated with LANGFUSE_* and LITELLM_* credentials
    3. pip install langfuse langchain-openai langchain
"""

import json
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from langfuse import Langfuse
from langfuse.experiment import Evaluation
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from config import (
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_HOST,
)
from agent.summarizer import summarize_news
from eval.criteria import (
    UNIVERSAL_CRITERIA,
    JUDGE_SYSTEM_PROMPT as JUDGE_SYSTEM,
    JUDGE_USER_TEMPLATE as JUDGE_USER,
)

# -- Domain-specific criteria (ai_news_agent) --

DOMAIN_CRITERIA = {
    "news_judgment": {
        "description": "Does the digest lead with the most newsworthy stories? Are stories prioritized by significance rather than recency alone?",
        "weight": 0.5,
    },
    "source_diversity": {
        "description": "Does the digest draw from multiple sources rather than over-relying on one? Are different perspectives represented?",
        "weight": 0.25,
    },
    "link_accuracy": {
        "description": "Are source links present and correctly attributed? Does each major story include a readable link?",
        "weight": 0.25,
    },
}


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

DATASET_NAME = "ai-news-digest-eval"

DATASET_ITEMS = [
    {
        "input": {
            "articles": [
                {
                    "source": "TechCrunch",
                    "title": "OpenAI launches GPT-5 with improved reasoning capabilities",
                    "url": "https://example.com/1",
                    "summary": "OpenAI announced GPT-5, featuring significant improvements in multi-step reasoning and reduced hallucination rates. The model shows 40% improvement on graduate-level math benchmarks.",
                },
                {
                    "source": "The Verge",
                    "title": "Google DeepMind achieves breakthrough in protein folding prediction",
                    "url": "https://example.com/2",
                    "summary": "DeepMind's latest AlphaFold model can now predict protein interactions with 95% accuracy, opening new drug discovery pathways.",
                },
                {
                    "source": "Hacker News",
                    "title": "Anthropic releases Claude with 1M context window",
                    "url": "https://example.com/3",
                    "summary": "Anthropic's Claude now supports 1 million token context windows, enabling analysis of entire codebases and long documents.",
                },
                {
                    "source": "VentureBeat",
                    "title": "EU AI Act enforcement begins with first compliance audits",
                    "url": "https://example.com/4",
                    "summary": "European regulators have started the first round of compliance audits under the EU AI Act, targeting high-risk AI systems in healthcare and finance.",
                },
                {
                    "source": "ArXiv",
                    "title": "Sparse Mixture of Experts reduces LLM inference costs by 60%",
                    "url": "https://example.com/5",
                    "summary": "Researchers demonstrate a sparse MoE architecture that cuts inference costs while maintaining quality on standard benchmarks.",
                },
            ]
        },
        "expected_output": None,
        "metadata": {"scenario": "standard_5_articles", "difficulty": "normal"},
    },
    {
        "input": {
            "articles": [
                {
                    "source": "Reuters",
                    "title": "NVIDIA announces next-gen B300 GPU for AI training",
                    "url": "https://example.com/6",
                    "summary": "NVIDIA unveiled the B300 GPU with 2x training throughput over the H100, priced at $40,000 per unit.",
                },
                {
                    "source": "TechCrunch",
                    "title": "NVIDIA B300 GPU benchmarks show 2x improvement",
                    "url": "https://example.com/7",
                    "summary": "Independent benchmarks confirm NVIDIA's B300 delivers on its 2x training speed claims across transformer architectures.",
                },
                {
                    "source": "The Verge",
                    "title": "NVIDIA stock jumps 8% on B300 announcement",
                    "url": "https://example.com/8",
                    "summary": "NVIDIA shares rose 8% in after-hours trading following the B300 GPU announcement.",
                },
            ]
        },
        "expected_output": None,
        "metadata": {"scenario": "duplicate_stories", "difficulty": "dedup_required"},
    },
]


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


def _create_judge():
    """Create the LLM judge (separate from the model being evaluated)."""
    return ChatOpenAI(
        model="gpt-4o",
        base_url=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
        temperature=0,
        max_tokens=200,
    )


def _get_judge_langfuse_handler():
    """Create Langfuse callback handler for judge LLM calls."""
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(
            tags=["eval", "judge"],
            session_id="eval-run",
            user_id="ai_news_agent",
        )
    except Exception:
        return None


def _judge_criterion(judge, input_text: str, output_text: str, name: str, description: str) -> dict:
    """Score an output on a single criterion using LLM-as-judge."""
    messages = [
        SystemMessage(content=JUDGE_SYSTEM),
        HumanMessage(content=JUDGE_USER.format(
            name=name,
            description=description,
            input_text=input_text[:2000],
            output_text=output_text[:3000],
        )),
    ]
    handler = _get_judge_langfuse_handler()
    config = {"callbacks": [handler]} if handler else {}
    response = judge.invoke(messages, config=config)
    try:
        result = json.loads(response.content)
        score = max(0.0, min(1.0, float(result["score"])))
        return {"score": score, "reasoning": result["reasoning"]}
    except (json.JSONDecodeError, KeyError, ValueError):
        return {"score": 0.0, "reasoning": f"Judge parse error: {response.content[:80]}"}


# Cache the judge across evaluator calls
_judge_llm = None


def _get_judge():
    global _judge_llm
    if _judge_llm is None:
        _judge_llm = _create_judge()
    return _judge_llm


def _make_evaluator(criterion_name: str, criterion_info: dict):
    """Factory: create a Langfuse evaluator function for a given criterion."""

    def evaluator(*, input, output, expected_output=None, **kwargs):
        input_text = json.dumps(input, indent=1)[:2000] if isinstance(input, dict) else str(input)[:2000]
        output_text = str(output)

        result = _judge_criterion(
            _get_judge(), input_text, output_text, criterion_name, criterion_info["description"]
        )
        return Evaluation(
            name=criterion_name,
            value=result["score"],
            comment=result["reasoning"],
            data_type="NUMERIC",
        )

    evaluator.__name__ = f"eval_{criterion_name}"
    return evaluator


def _make_composite_evaluator(criteria: dict, prefix: str):
    """Factory: create a weighted composite score from individual evaluations."""

    def composite(*, input, output, expected_output, metadata, evaluations, **kwargs):
        total, weight_sum = 0.0, 0.0
        for ev in evaluations:
            if ev.name in criteria and isinstance(ev.value, (int, float)):
                w = criteria[ev.name]["weight"]
                total += ev.value * w
                weight_sum += w

        score = total / weight_sum if weight_sum > 0 else 0.0
        return Evaluation(
            name=f"{prefix}_composite",
            value=round(score, 3),
            comment=f"Weighted average of {len(evaluations)} criteria",
            data_type="NUMERIC",
        )

    return composite


def _make_run_evaluator(criteria: dict, prefix: str):
    """Factory: create a run-level aggregator that averages across all items."""

    def run_evaluator(*, item_results, **kwargs):
        evals = []
        # Average each criterion across items
        by_name = {}
        for result in item_results:
            for ev in result.evaluations:
                if ev.name in criteria and isinstance(ev.value, (int, float)):
                    by_name.setdefault(ev.name, []).append(ev.value)

        for name, values in by_name.items():
            avg = sum(values) / len(values)
            evals.append(Evaluation(
                name=f"avg_{name}",
                value=round(avg, 3),
                comment=f"Average across {len(values)} items",
                data_type="NUMERIC",
            ))

        # Overall run average
        all_vals = [v for vals in by_name.values() for v in vals]
        if all_vals:
            evals.append(Evaluation(
                name=f"avg_{prefix}_overall",
                value=round(sum(all_vals) / len(all_vals), 3),
                comment=f"Overall average across all criteria and items",
                data_type="NUMERIC",
            ))

        return evals

    return run_evaluator


# ---------------------------------------------------------------------------
# Task function (the thing being evaluated)
# ---------------------------------------------------------------------------


def digest_task(*, item, **kwargs):
    """Run the summarizer on a dataset item's articles."""
    # Langfuse DatasetItem uses attribute access, not dict subscript
    input_data = item.input if hasattr(item, "input") else item["input"]
    articles = input_data["articles"]
    return summarize_news(articles)


# ---------------------------------------------------------------------------
# Setup & Run
# ---------------------------------------------------------------------------


def setup_score_configs(langfuse: Langfuse):
    """Register score configurations in Langfuse for consistent display."""
    all_criteria = {**UNIVERSAL_CRITERIA, **DOMAIN_CRITERIA}

    for name, info in all_criteria.items():
        try:
            langfuse.api.score_configs.create(
                name=name,
                data_type="NUMERIC",
                min_value=0.0,
                max_value=1.0,
                description=info["description"],
            )
            print(f"  Created score config: {name}")
        except Exception:
            # Already exists
            pass


def setup_dataset(langfuse: Langfuse):
    """Create or fetch the evaluation dataset."""
    try:
        dataset = langfuse.get_dataset(DATASET_NAME)
        print(f"Using existing dataset: {DATASET_NAME} ({len(dataset.items)} items)")
        return dataset
    except Exception:
        pass

    print(f"Creating dataset: {DATASET_NAME}")
    langfuse.create_dataset(name=DATASET_NAME)

    for i, item_data in enumerate(DATASET_ITEMS):
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input=item_data["input"],
            expected_output=item_data["expected_output"],
            metadata=item_data["metadata"],
        )
        print(f"  Added item {i + 1}: {item_data['metadata']['scenario']}")

    return langfuse.get_dataset(DATASET_NAME)


def run():
    print("=" * 60)
    print("AI News Agent — Evaluation Suite")
    print("=" * 60)

    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST,
    )

    # 1. Register score configs
    print("\n[1/3] Setting up score configs...")
    setup_score_configs(langfuse)

    # 2. Setup dataset
    print("\n[2/3] Setting up dataset...")
    dataset = setup_dataset(langfuse)

    # 3. Build evaluators
    all_criteria = {**UNIVERSAL_CRITERIA, **DOMAIN_CRITERIA}
    item_evaluators = [_make_evaluator(name, info) for name, info in all_criteria.items()]

    # 4. Run experiment
    print(f"\n[3/3] Running experiment ({len(dataset.items)} items x {len(item_evaluators)} criteria)...")
    print(f"  This will make ~{len(dataset.items) * len(item_evaluators)} judge calls.\n")

    result = dataset.run_experiment(
        name="ai-news-digest",
        task=digest_task,
        evaluators=item_evaluators,
        composite_evaluator=_make_composite_evaluator(all_criteria, "quality"),
        run_evaluators=[_make_run_evaluator(all_criteria, "quality")],
        max_concurrency=5,
    )

    # 5. Print results
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(result.format(include_item_results=True))

    if result.dataset_run_url:
        print(f"\nView in Langfuse: {result.dataset_run_url}")
    else:
        print(f"\nView in Langfuse: {LANGFUSE_HOST}")

    langfuse.shutdown()


if __name__ == "__main__":
    run()
