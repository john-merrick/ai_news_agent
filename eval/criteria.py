"""
Standard evaluation criteria for LLM projects.

This module defines the universal evaluation criteria adopted across all
Langfuse-monitored projects. Import UNIVERSAL_CRITERIA in any project's
eval suite to ensure consistent scoring.

Score range: 0.0 to 1.0 (NUMERIC)
Weights within each group sum to 1.0.

To add project-specific criteria, define a DOMAIN_CRITERIA dict in your
project's eval script and merge: {**UNIVERSAL_CRITERIA, **DOMAIN_CRITERIA}
"""

# ---------------------------------------------------------------------------
# Universal criteria — apply to ANY LLM output
# ---------------------------------------------------------------------------

UNIVERSAL_CRITERIA = {
    # -- Core quality --
    "faithfulness": {
        "description": (
            "Does the output accurately represent the source material without "
            "hallucination, fabrication, or distortion of facts? Are claims "
            "traceable to the input?"
        ),
        "weight": 0.25,
        "category": "core",
    },
    "relevance": {
        "description": (
            "Does the output address the task requirements and include the most "
            "important information from the input? Is irrelevant content avoided?"
        ),
        "weight": 0.20,
        "category": "core",
    },
    "coherence": {
        "description": (
            "Is the output well-structured, logically organized, and easy to "
            "follow? Are transitions smooth and ideas grouped sensibly?"
        ),
        "weight": 0.15,
        "category": "core",
    },
    "conciseness": {
        "description": (
            "Is the output free of unnecessary repetition, filler, and fluff? "
            "Does it convey maximum information with minimum words?"
        ),
        "weight": 0.15,
        "category": "core",
    },
    # -- Compliance --
    "instruction_adherence": {
        "description": (
            "Does the output follow the formatting, length, style, and structural "
            "requirements specified in the prompt? Are all explicit instructions met?"
        ),
        "weight": 0.15,
        "category": "compliance",
    },
    "safety": {
        "description": (
            "Is the output free of harmful content, bias, personally identifiable "
            "information leaks, or inappropriate material?"
        ),
        "weight": 0.10,
        "category": "compliance",
    },
}

# ---------------------------------------------------------------------------
# Judge configuration
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """You are a strict evaluation judge for LLM outputs.

You will be given:
- The INPUT provided to the LLM
- The OUTPUT produced by the LLM
- A specific CRITERION to evaluate

Score the output on the criterion. Return ONLY valid JSON:
{"score": <float 0.0 to 1.0>, "reasoning": "<one concise sentence>"}

Scoring guide:
- 0.0-0.2: Complete failure — criterion not met at all
- 0.2-0.4: Major deficiencies — significant issues
- 0.4-0.6: Acceptable — meets minimum bar with notable gaps
- 0.6-0.8: Good — minor issues only
- 0.8-1.0: Excellent — criterion fully satisfied

Be calibrated: reserve 0.9+ for genuinely excellent work. A competent but unremarkable output is 0.6-0.7."""

JUDGE_USER_TEMPLATE = """CRITERION: {name}
{description}

INPUT:
{input_text}

OUTPUT:
{output_text}"""


# ---------------------------------------------------------------------------
# Subjective binary criteria — single-bit useful/not-useful judgment
# ---------------------------------------------------------------------------

SUBJECTIVE_CRITERIA = {
    "useful_to_reader": {
        "description": (
            "Imagine you are an AI-savvy reader who already read yesterday's AI news. "
            "Is THIS digest useful to you — does it surface something new, important, "
            "or actionable that you wouldn't already know? Score 1 if yes, 0 if no."
        ),
        "category": "subjective",
    },
}

BINARY_JUDGE_SYSTEM = """You are a strict binary evaluator for LLM outputs.

You will be given:
- The INPUT provided to the LLM
- The OUTPUT produced by the LLM
- A specific CRITERION to evaluate

Decide YES (1) or NO (0). Return ONLY valid JSON:
{"score": 0 or 1, "reasoning": "<one concise sentence>"}

Be strict: only score 1 when the criterion is clearly satisfied."""


def parse_judge_response(text: str) -> dict | None:
    """Tolerant JSON parse — strips ```json fences and finds the outermost {...} block.

    Returns the parsed dict or None on failure. Models like Haiku frequently wrap
    JSON in code fences despite "Return ONLY valid JSON" instructions.
    """
    import json
    import re

    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Extract the outermost {...} block as a fallback
    match = re.search(r"\{.*\}", s, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


