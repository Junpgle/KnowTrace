"""Validation of AI-generated questions before they enter the review queue.

Verbatim evidence and structurally valid answers are prerequisites, NOT proof
that an AI-authored distractor, explanation, or correct answer is true.
"""
from __future__ import annotations

import json
from typing import Any

from .ai import AIServiceError, compact

VALID_TYPES = ("single_choice", "true_false")
CHOICE_KEYS = ("A", "B", "C", "D")


def validate_question_fields(question_type: Any, stem: Any, options: Any,
                             answer: Any, explanation: Any, difficulty: Any) -> str | None:
    """Return why a question's structure is unacceptable; otherwise None."""
    if question_type not in VALID_TYPES:
        return "unsupported question type"
    if not isinstance(stem, str) or not (8 <= len(stem.strip()) <= 600):
        return "invalid stem"
    if not isinstance(explanation, str) or not (8 <= len(explanation.strip()) <= 1500):
        return "invalid explanation"
    if type(difficulty) is not int or difficulty not in (1, 2, 3):
        return "invalid difficulty"
    if question_type == "single_choice":
        if (not isinstance(options, dict) or set(options) != set(CHOICE_KEYS)
                or any(not isinstance(options[key], str) or not options[key].strip()
                       or len(options[key]) > 250 for key in CHOICE_KEYS)):
            return "single-choice requires four labeled options A, B, C, D"
        if len({compact(options[key]) for key in CHOICE_KEYS}) != 4:
            return "duplicate choices"
        if not isinstance(answer, str) or answer not in CHOICE_KEYS:
            return "single-choice answer must be A, B, C or D"
    else:
        if options not in ({"true": "正确", "false": "错误"}, {}, None):
            return "true/false options must be empty or the standard labels"
        if type(answer) is not bool:
            return "true/false answer must be JSON boolean"
    return None


def validate_generated_questions(response: str, sources: dict[str, dict],
                                 *, permitted_types: set[str], count: int,
                                 existing_stems: set[str] | None = None
                                 ) -> tuple[list[dict], list[str]]:
    """Reject malformed/ungrounded/duplicate questions without persisting them."""
    try:
        data = json.loads(response)
    except (ValueError, TypeError) as exc:
        raise AIServiceError("Question model did not return JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise AIServiceError("Question JSON must contain items array")
    accepted: list[dict] = []
    warnings: list[str] = []
    seen = set(existing_stems or ())
    for index, item in enumerate(data["items"][:max(count * 5, 10)], start=1):
        if not isinstance(item, dict):
            warnings.append(f"candidate {index}: not an object")
            continue
        question_type = item.get("question_type")
        if question_type not in permitted_types:
            warnings.append(f"candidate {index}: disallowed question type")
            continue
        err = validate_question_fields(question_type, item.get("stem"), item.get("options"),
                                       item.get("correct_answer"), item.get("explanation"),
                                       item.get("difficulty"))
        if err:
            warnings.append(f"candidate {index}: {err}")
            continue
        chunk_id, quote = item.get("source_chunk_id"), item.get("quote")
        if (not isinstance(chunk_id, str) or chunk_id not in sources
                or not isinstance(quote, str) or len(compact(quote)) < 10
                or compact(quote) not in compact(sources[chunk_id]["content_text"])):
            warnings.append(f"candidate {index}: exact source quotation not found")
            continue
        stem_key = compact(item["stem"])
        if stem_key in seen:
            warnings.append(f"candidate {index}: duplicate question")
            continue
        seen.add(stem_key)
        accepted.append({
            "question_type": question_type, "stem": item["stem"].strip(),
            "options": (dict(item["options"]) if question_type == "single_choice"
                        else {"true": "正确", "false": "错误"}),
            "correct_answer": item["correct_answer"],
            "explanation": item["explanation"].strip(),
            "difficulty": item["difficulty"], "source_chunk_id": chunk_id,
            "quote": quote.strip(),
        })
        if len(accepted) >= count:
            break
    return accepted, warnings
