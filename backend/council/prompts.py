"""Council prompts, moved verbatim from the old ``backend/council.py`` and defined once.

The ranking prompt's strict "FINAL RANKING:" format is what :mod:`backend.council.ranking` parses.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

RANKING_PROMPT_TEMPLATE = """You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

CHAIRMAN_PROMPT_TEMPLATE = """You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

TITLE_PROMPT_TEMPLATE = """Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""


def response_labels(count: int) -> List[str]:
    """Anonymous labels A, B, C, … in Stage-1 success order."""
    return [chr(65 + i) for i in range(count)]


def build_label_to_model(stage1_results: List[Dict[str, Any]]) -> Dict[str, str]:
    """``{"Response A": "<model id>", …}`` for de-anonymizing rankings."""
    return {f"Response {label}": result["model"]
            for label, result in zip(response_labels(len(stage1_results)), stage1_results)}


def build_ranking_prompt(user_query: str, stage1_results: List[Dict[str, Any]]) -> str:
    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(response_labels(len(stage1_results)), stage1_results)
    ])
    return RANKING_PROMPT_TEMPLATE.format(user_query=user_query, responses_text=responses_text)


def build_ranking(user_query: str, stage1_results: List[Dict[str, Any]]) -> Tuple[str, Dict[str, str]]:
    """The Stage-2 prompt and its label → model mapping."""
    return build_ranking_prompt(user_query, stage1_results), build_label_to_model(stage1_results)


def build_chairman_prompt(user_query: str, stage1_results: List[Dict[str, Any]],
                          stage2_results: List[Dict[str, Any]]) -> str:
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])
    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])
    return CHAIRMAN_PROMPT_TEMPLATE.format(user_query=user_query, stage1_text=stage1_text, stage2_text=stage2_text)


def build_title_prompt(user_query: str) -> str:
    return TITLE_PROMPT_TEMPLATE.format(user_query=user_query)
