"""Auditable evidence diagnostics for *uncalibrated* BKT estimates.

The categories below are display sufficiency heuristics, not statistical
confidence intervals or evidence that the model parameters were fitted.
Existing BKT states and event histories remain completely unchanged.
"""
from __future__ import annotations

import sqlite3

MIN_EVENTS = 5
MIN_UNIQUE_QUESTIONS = 3
MIN_PRACTICE_DAYS = 2


def evidence_by_knowledge_point(db: sqlite3.Connection, user_id: str,
                                course_id: str, model_key: str) -> dict[str, dict]:
    """Count only actual BKT updates (not blind/subjective/compound attempts).

    Question identity comes from questions.id, not question_versions.id:
    rewriting the answer to the same question must not inflate independence.
    """
    rows = db.execute("""SELECT e.knowledge_point_id,
               COUNT(*) AS observations,
               COUNT(DISTINCT q.id) AS distinct_questions,
               COUNT(DISTINCT i.session_id) AS practice_sessions,
               COUNT(DISTINCT substr(a.answered_at,1,10)) AS practice_days,
               MAX(a.answered_at) AS last_practiced_at
        FROM mastery_events e
        JOIN answer_records a ON a.id=e.answer_record_id
        JOIN quiz_items i ON i.id=a.quiz_item_id
        JOIN quiz_sessions s ON s.id=i.session_id
        JOIN question_versions v ON v.id=i.question_version_id
        JOIN questions q ON q.id=v.question_id
        WHERE e.user_id=? AND e.model_key=?
          AND s.course_id=? AND s.user_id=?
        GROUP BY e.knowledge_point_id
    """, (user_id, model_key, course_id, user_id)).fetchall()
    return {row["knowledge_point_id"]: {
        "observations": row["observations"],
        "distinct_questions": row["distinct_questions"],
        "practice_sessions": row["practice_sessions"],
        "practice_days": row["practice_days"],
        "last_practiced_at": row["last_practiced_at"],
    } for row in rows}


def evidence_level(*, observations: int, distinct_questions: int,
                   practice_days: int) -> str:
    if observations <= 0:
        return "unobserved"
    if (observations >= MIN_EVENTS and
            distinct_questions >= MIN_UNIQUE_QUESTIONS and
            practice_days >= MIN_PRACTICE_DAYS):
        return "repeated"
    return "limited"


def overview_stats(rows: list[dict]) -> dict:
    approved = [row for row in rows if row.get("review_status") == "approved"]
    observed = [row for row in approved if row.get("evidence_level") != "unobserved"]
    repeated = [row for row in observed if row["evidence_level"] == "repeated"]
    # The mean of a sparse subset is particularly misleading: report coverage
    # separately and never call this a course-level mastery rate.
    return {
        "approved_points":len(approved),
        "observed_points":len(observed),
        "repeated_evidence_points":len(repeated),
        "unobserved_points":len(approved)-len(observed),
        "observation_coverage":round(len(observed)/len(approved),4) if approved else None,
        "minimum_evidence": {
            "observations":MIN_EVENTS,
            "distinct_questions":MIN_UNIQUE_QUESTIONS,
            "practice_days":MIN_PRACTICE_DAYS,
        },
        "note":"Coverage counts are factual. BKT parameters are not calibrated; "
               "repeated evidence is only a display threshold, not a certified skill score.",
    }
