"""Local single-user quizzes, immutable answer events, and BKT mastery estimates.

Approved questions power normal/BKT assessments. Pending questions may
optionally be used in a separate blind trial: answers are hidden until the
WHOLE trial is submitted and never update mastery. This prototype has no
auth; the administrative review endpoint remains accessible locally.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictStr

from .bkt import BKTParameters, update_mastery
from .db import transaction
from .mastery_evidence import evidence_by_knowledge_point, evidence_level, overview_stats

USER_ID = "local"
MODEL_KEY = "bkt_v1"
BLIND_REASON = "blind_pending_v1"
BKT = BKTParameters()


class QuizStart(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    mode: Literal["normal", "adaptive"] = "normal"
    source: Literal["approved", "pending"] = "approved"


class SubmitAnswer(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    response: StrictStr | StrictBool | list[StrictStr]
    elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)


def _uuid() -> str:
    return str(uuid4())


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _session(db: sqlite3.Connection, session_id: str) -> dict:
    row = db.execute("SELECT * FROM quiz_sessions WHERE id=? AND user_id=?",
                     (session_id, USER_ID)).fetchone()
    if row is None:
        raise HTTPException(404, "Quiz session not found")
    return dict(row)


def _quiz_detail(db: sqlite3.Connection, session_id: str) -> dict:
    session = _session(db, session_id)
    records = _rows(db.execute("""SELECT i.id AS quiz_item_id,i.position,i.shown_at,
                i.question_version_id,i.primary_knowledge_point_id,
                i.selection_reason,v.question_type,v.stem,v.review_status,
                v.options_json,v.explanation,v.correct_answer_json,
                a.id AS answer_id,a.response_json,a.is_correct,a.answered_at,a.elapsed_ms,
                ev.mode AS evaluation_mode,ev.verdict AS evaluation_verdict,ev.feedback AS evaluation_feedback
                FROM quiz_items i JOIN question_versions v ON v.id=i.question_version_id
                LEFT JOIN answer_records a ON a.quiz_item_id=i.id
                LEFT JOIN short_answer_evaluations ev ON ev.quiz_item_id=i.id
                WHERE i.session_id=? ORDER BY i.position""", (session_id,)))
    blind = bool(records and all(record["selection_reason"] == BLIND_REASON for record in records))
    reveal = not blind or session["status"] == "completed"
    items = []
    for record in records:
        answered = record["answer_id"] is not None
        obj = {"quiz_item_id": record["quiz_item_id"], "position": record["position"],
               "question_version_id": record["question_version_id"],
               "primary_knowledge_point_id": record["primary_knowledge_point_id"],
               "question_type": record["question_type"], "stem": record["stem"],
               "options": json.loads(record["options_json"]), "answered": answered,
               "provisional": record["review_status"] != "approved",
               "selection_reason": ("待审核题目盲测：完成整套后揭晓答案"
                                    if blind else record["selection_reason"])}
        if answered:
            obj["submitted_response"] = json.loads(record["response_json"])
        if answered and reveal:
            if record["question_type"] == "short_answer":
                obj["evaluation"] = ({
                    "mode":record["evaluation_mode"],"verdict":record["evaluation_verdict"],
                    "feedback":record["evaluation_feedback"]
                } if record["evaluation_verdict"] is not None else None)
            obj["result"] = {"answer_id": record["answer_id"],
                             "response": json.loads(record["response_json"]),
                             "is_correct": (None if record["question_type"] == "short_answer"
                                            else bool(record["is_correct"])),
                             "correct_answer": json.loads(record["correct_answer_json"]),
                             "explanation": record["explanation"],
                             "answered_at": record["answered_at"],
                             "elapsed_ms": record["elapsed_ms"]}
        items.append(obj)
    answered_count = sum(item["answered"] for item in items)
    correct_count = sum(item["result"]["is_correct"] is True for item in items
                        if item["answered"] and "result" in item)
    objective_total = sum(item["question_type"] != "short_answer" for item in items)
    subjective_total = len(items) - objective_total
    evaluated_subjective = sum(item.get("evaluation") is not None for item in items)
    return {"id": session["id"], "course_id": session["course_id"],
            "mode": session["mode"], "blind_pending": blind, "status": session["status"],
            "started_at": session["started_at"], "finished_at": session["finished_at"],
            "total": len(items), "answered": answered_count,
            "correct": correct_count if reveal else None,
            "objective_total":objective_total,"subjective_total":subjective_total,
            "evaluated_subjective":evaluated_subjective if reveal else None,
            "items": items}


def _answer_result(db: sqlite3.Connection, answer_id: str) -> dict:
    row = db.execute("""SELECT a.id AS answer_id,a.quiz_item_id,a.response_json,
           a.is_correct,a.answered_at,v.correct_answer_json,v.explanation,
           e.p_before,e.p_posterior,e.p_after,
           i.primary_knowledge_point_id AS knowledge_point_id,
           v.question_type,i.selection_reason,s.status AS session_status
           FROM answer_records a JOIN quiz_items i ON i.id=a.quiz_item_id
           JOIN quiz_sessions s ON s.id=i.session_id
           JOIN question_versions v ON v.id=i.question_version_id
           LEFT JOIN mastery_events e ON e.answer_record_id=a.id AND e.model_key=?
           WHERE a.id=?""", (MODEL_KEY, answer_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Answer not found")
    if row["selection_reason"] == BLIND_REASON and row["session_status"] != "completed":
        return {"answer_id": row["answer_id"], "quiz_item_id": row["quiz_item_id"],
                "response": json.loads(row["response_json"]),
                "answered_at": row["answered_at"], "deferred": True,
                "mastery": None, "diagnostic": False}
    return {"answer_id": row["answer_id"], "quiz_item_id": row["quiz_item_id"],
            "response": json.loads(row["response_json"]),
            "is_correct": (None if row["question_type"] == "short_answer"
                           else bool(row["is_correct"])),
            "correct_answer": json.loads(row["correct_answer_json"]),
            "explanation": row["explanation"], "knowledge_point_id": row["knowledge_point_id"],
            "mastery": {"model": MODEL_KEY, "before": row["p_before"],
                        "posterior": row["p_posterior"], "after": row["p_after"]}
                        if row["p_before"] is not None else None,
            "diagnostic": row["p_before"] is not None,
            "answered_at": row["answered_at"]}


def register_quiz_routes(app: FastAPI, database: Path) -> None:
    @app.post("/api/courses/{course_id}/quiz-sessions", status_code=201)
    def start_quiz(course_id: str, payload: QuizStart):
        with transaction(database) as db:
            if db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                          (course_id,)).fetchone() is None:
                raise HTTPException(404, "Course not found")
            if payload.source == "pending" and payload.mode != "normal":
                raise HTTPException(422, "Blind pending questions cannot use adaptive BKT mode")
            rows = _rows(db.execute("""SELECT v.id AS version_id,k.knowledge_point_id
                FROM question_versions v
                JOIN questions q ON q.id=v.question_id
                JOIN question_knowledge_points k ON k.question_version_id=v.id AND k.is_primary=1
                JOIN knowledge_points kp ON kp.id=k.knowledge_point_id
                WHERE q.course_id=? AND q.archived_at IS NULL AND v.review_status=?
                AND kp.review_status='approved' AND kp.archived_at IS NULL
                AND v.version_no=(SELECT MAX(v2.version_no)
                                   FROM question_versions v2 WHERE v2.question_id=q.id)
                ORDER BY v.created_at,v.id""", (course_id,payload.source)))
            if payload.source == "pending":
                # Prefer previously untouched candidates. Once all have been
                # attempted, older blind attempts may be repeated for practice.
                # Among equally fresh candidates show newly generated items
                # first, so browsing an older admin item need not dominate.
                prior_attempts = {
                    row["question_version_id"]: row["attempts"]
                    for row in db.execute("""SELECT i.question_version_id,
                        COUNT(a.id) AS attempts
                        FROM quiz_items i JOIN quiz_sessions s ON s.id=i.session_id
                        JOIN answer_records a ON a.quiz_item_id=i.id
                        WHERE s.course_id=? AND s.user_id=?
                        AND i.selection_reason=?
                        GROUP BY i.question_version_id""",
                        (course_id, USER_ID, BLIND_REASON))
                }
                rows.reverse()
                rows.sort(key=lambda item: prior_attempts.get(item["version_id"], 0))
            if payload.mode == "adaptive":
                # Reliable adaptive diagnosis only uses questions that measure
                # exactly one reviewed concept. A compound answer is not a
                # valid independent BKT observation for every Q-matrix entry.
                eligible = _rows(db.execute("""SELECT v.id AS version_id,
                    k.knowledge_point_id,
                    COALESCE(m.p_mastery, ?) AS mastery,
                    COALESCE(m.observations,0) AS observations,
                    COUNT(a.id) AS attempts,
                    MAX(a.answered_at) AS last_answered,
                    v.difficulty
                    FROM question_versions v
                    JOIN questions q ON q.id=v.question_id
                    JOIN question_knowledge_points k ON k.question_version_id=v.id
                       AND k.is_primary=1
                    JOIN knowledge_points kp ON kp.id=k.knowledge_point_id
                    LEFT JOIN mastery_states m ON m.knowledge_point_id=k.knowledge_point_id
                       AND m.user_id=? AND m.model_key=?
                    LEFT JOIN quiz_items i ON i.question_version_id=v.id
                    LEFT JOIN answer_records a ON a.quiz_item_id=i.id
                    WHERE q.course_id=? AND q.archived_at IS NULL
                    AND v.review_status='approved'
                    AND v.question_type IN ('single_choice','true_false')
                    AND kp.review_status='approved' AND kp.archived_at IS NULL
                    AND v.version_no=(SELECT MAX(v2.version_no)
                                     FROM question_versions v2 WHERE v2.question_id=q.id)
                    AND (SELECT COUNT(*) FROM question_knowledge_points
                         WHERE question_version_id=v.id)=1
                    GROUP BY v.id,k.knowledge_point_id,m.p_mastery,m.observations,v.difficulty
                    """, (BKT.p_initial,USER_ID,MODEL_KEY,course_id)))
                def priority(item):
                    # Unseen/weak concepts before well-mastered or repeated
                    # questions. Stable tie-breaking makes this auditable.
                    initial_bonus = .45 if item["observations"] == 0 else 0
                    retry_penalty = min(item["attempts"],8) * .15
                    target_difficulty = 1 if item["mastery"] < .4 else (
                        2 if item["mastery"] < .75 else 3)
                    return (2*(1-item["mastery"]) + initial_bonus -
                            retry_penalty - abs(item["difficulty"]-target_difficulty)*.06)
                ranked = sorted(eligible, key=lambda item:
                    (-priority(item), item["last_answered"] or "", item["version_id"]))
                # Pass 1 ensures breadth across weak concepts. Pass 2 fills
                # any remaining slots with other diagnostic questions.
                chosen, seen_points = [], set()
                for item in ranked:
                    if item["knowledge_point_id"] not in seen_points:
                        chosen.append(item)
                        seen_points.add(item["knowledge_point_id"])
                if len(chosen) < payload.count:
                    selected_ids = {x["version_id"] for x in chosen}
                    chosen += [x for x in ranked if x["version_id"] not in selected_ids]
                rows = chosen
            if len(rows) < payload.count:
                raise HTTPException(409, f"Only {len(rows)} eligible {payload.mode} questions available; requested {payload.count}")
            session_id = _uuid()
            db.execute("""INSERT OR IGNORE INTO users (id,username,display_name)
                       VALUES ('local','local','Local learner')""")
            db.execute("INSERT INTO quiz_sessions(id,user_id,course_id,mode) VALUES (?,?,?,?)",
                       (session_id, USER_ID, course_id, payload.mode))
            for position, row in enumerate(rows[:payload.count], start=1):
                db.execute("""INSERT INTO quiz_items(id,session_id,question_version_id,
                           primary_knowledge_point_id,position,selection_reason)
                           VALUES (?,?,?,?,?,?)""",
                           (_uuid(), session_id, row["version_id"], row["knowledge_point_id"],
                            position, (BLIND_REASON if payload.source=="pending"
                                       else "优先复习未掌握或尚未测评知识点" if payload.mode=="adaptive"
                                       else "正常顺序练习")))
            return _quiz_detail(db, session_id)

    @app.get("/api/quiz-sessions/{session_id}")
    def get_quiz(session_id: str):
        with transaction(database) as db:
            return _quiz_detail(db, session_id)

    @app.post("/api/quiz-items/{quiz_item_id}/answer")
    def submit_answer(quiz_item_id: str, payload: SubmitAnswer):
        serialized = json.dumps(payload.response, ensure_ascii=False)
        with transaction(database) as db:
            item = db.execute("""SELECT i.id,i.session_id,i.primary_knowledge_point_id,
                v.question_type,v.question_id AS question_id,v.correct_answer_json,v.review_status,
                i.selection_reason,s.status AS session_status,
                (SELECT COUNT(*) FROM question_knowledge_points x
                 WHERE x.question_version_id=i.question_version_id) AS concept_count,
                s.user_id FROM quiz_items i
                JOIN quiz_sessions s ON s.id=i.session_id
                JOIN question_versions v ON v.id=i.question_version_id
                WHERE i.id=?""", (quiz_item_id,)).fetchone()
            if item is None or item["user_id"] != USER_ID:
                raise HTTPException(404, "Quiz item not found")
            previous = db.execute("""SELECT id,client_request_id,response_json
                FROM answer_records WHERE quiz_item_id=?""", (quiz_item_id,)).fetchone()
            if previous is not None:
                if previous["client_request_id"] == payload.client_request_id and previous["response_json"] == serialized:
                    return _answer_result(db, previous["id"])
                raise HTTPException(409, "Question already answered; no second attempt in this session")
            collision = db.execute("SELECT 1 FROM answer_records WHERE client_request_id=?",
                                   (payload.client_request_id,)).fetchone()
            if collision is not None:
                raise HTTPException(409, "client_request_id is already used")
            if item["session_status"] != "active":
                raise HTTPException(409, "Quiz session is no longer active")
            answer = json.loads(item["correct_answer_json"])
            if item["question_type"] == "single_choice":
                if type(payload.response) is not str or payload.response not in ("A", "B", "C", "D"):
                    raise HTTPException(422, "Single-choice response must be A, B, C or D")
            elif item["question_type"] == "multiple_choice":
                if (type(payload.response) is not list or
                    not 1 <= len(payload.response) <= 4 or
                    any(type(x) is not str or x not in ("A","B","C","D") for x in payload.response) or
                    len(set(payload.response)) != len(payload.response) or
                    payload.response != sorted(payload.response)):
                    raise HTTPException(422, "Multiple choice must be a sorted unique array of A-D")
            elif item["question_type"] == "short_answer":
                if type(payload.response) is not str or not 1 <= len(payload.response.strip()) <= 3000:
                    raise HTTPException(422, "Short answer text must be 1-3000 characters")
            elif item["question_type"] == "true_false":
                if type(payload.response) is not bool:
                    raise HTTPException(422, "True/false response must be JSON boolean")
            else:
                raise HTTPException(422, "Unsupported question type")
            correct = False if item["question_type"] == "short_answer" else payload.response == answer
            record_id = _uuid()
            db.execute("""INSERT INTO answer_records(id,quiz_item_id,client_request_id,
                       response_json,is_correct,elapsed_ms) VALUES (?,?,?,?,?,?)""",
                       (record_id, quiz_item_id, payload.client_request_id, serialized, int(correct), payload.elapsed_ms))
            # Multi-concept questions are not independent BKT observations.
            # Repeating the SAME question (including a revised version) inside
            # 24h also adds no independent evidence. It remains a real practice
            # answer and is graded, but cannot inflate a mastery probability.
            eligible = (item["question_type"] in ("single_choice","true_false")
                        and item["concept_count"] == 1
                        and item["review_status"] == "approved"
                        and item["selection_reason"] != BLIND_REASON)
            if eligible:
                kp_id = item["primary_knowledge_point_id"]
                recent = db.execute("""SELECT 1 FROM mastery_events e
                    JOIN answer_records old_a ON old_a.id=e.answer_record_id
                    JOIN quiz_items old_i ON old_i.id=old_a.quiz_item_id
                    JOIN question_versions old_v ON old_v.id=old_i.question_version_id
                    WHERE e.user_id=? AND e.knowledge_point_id=?
                      AND e.model_key=? AND old_v.question_id=?
                      AND old_a.answered_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-24 hours')
                    LIMIT 1""", (USER_ID,kp_id,MODEL_KEY,item["question_id"])).fetchone()
                eligible = recent is None
            if eligible:
                kp_id = item["primary_knowledge_point_id"]
                state = db.execute("""SELECT p_mastery,observations FROM mastery_states
                    WHERE user_id=? AND knowledge_point_id=? AND model_key=?""",
                    (USER_ID, kp_id, MODEL_KEY)).fetchone()
                prior = state["p_mastery"] if state else BKT.p_initial
                observations = state["observations"] if state else 0
                posterior, after = update_mastery(prior, correct, BKT)
                db.execute("""INSERT INTO mastery_states(user_id,knowledge_point_id,model_key,p_mastery,observations)
                    VALUES (?,?,?,?,?) ON CONFLICT(user_id,knowledge_point_id,model_key)
                    DO UPDATE SET p_mastery=excluded.p_mastery,observations=excluded.observations,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (USER_ID,kp_id,MODEL_KEY,after,observations+1))
                db.execute("""INSERT INTO mastery_events(id,user_id,knowledge_point_id,
                    answer_record_id,model_key,p_before,p_posterior,p_after,parameters_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (_uuid(),USER_ID,kp_id,record_id,MODEL_KEY,prior,posterior,after,
                     json.dumps({"p_initial":BKT.p_initial,"p_learn":BKT.p_learn,
                                 "p_guess":BKT.p_guess,"p_slip":BKT.p_slip})))
            missing = db.execute("""SELECT COUNT(*) FROM quiz_items i
                LEFT JOIN answer_records a ON a.quiz_item_id=i.id
                WHERE i.session_id=? AND a.id IS NULL""", (item["session_id"],)).fetchone()[0]
            if missing == 0:
                db.execute("""UPDATE quiz_sessions SET status='completed',
                    finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                    (item["session_id"],))
            return _answer_result(db, record_id)

    @app.get("/api/courses/{course_id}/learning-report")
    def learning_report(course_id: str):
        with transaction(database) as db:
            if db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'", (course_id,)).fetchone() is None:
                raise HTTPException(404, "Course not found")
            per_kp = _rows(db.execute("""SELECT kp.id AS knowledge_point_id,kp.canonical_name,
                 kp.review_status,m.p_mastery,m.observations,
                 COUNT(a.id) AS answered,COALESCE(SUM(a.is_correct),0) AS correct
                 FROM knowledge_points kp
                 LEFT JOIN mastery_states m ON m.knowledge_point_id=kp.id
                    AND m.user_id=? AND m.model_key=?
                 LEFT JOIN quiz_items i ON i.primary_knowledge_point_id=kp.id
                 LEFT JOIN question_versions v ON v.id=i.question_version_id
                 LEFT JOIN quiz_sessions s ON s.id=i.session_id AND s.user_id=?
                    AND s.course_id=?
                 LEFT JOIN answer_records a ON a.quiz_item_id=i.id AND s.id IS NOT NULL
                    AND (i.selection_reason IS NULL OR i.selection_reason != ?)
                    AND v.question_type != 'short_answer'
                 WHERE kp.course_id=? AND kp.archived_at IS NULL
                 GROUP BY kp.id ORDER BY kp.canonical_name,kp.id""",
                 (USER_ID,MODEL_KEY,USER_ID,course_id,BLIND_REASON,course_id)))
            evidence = evidence_by_knowledge_point(db, USER_ID, course_id, MODEL_KEY)
            for row in per_kp:
                row["accuracy"] = round(row["correct"] / row["answered"], 4) if row["answered"] else None
                found = evidence.get(row["knowledge_point_id"], {})
                row["distinct_questions"] = found.get("distinct_questions", 0)
                row["practice_sessions"] = found.get("practice_sessions", 0)
                row["practice_days"] = found.get("practice_days", 0)
                row["last_practiced_at"] = found.get("last_practiced_at")
                row["evidence_level"] = evidence_level(
                    observations=found.get("observations", 0),
                    distinct_questions=row["distinct_questions"],
                    practice_days=row["practice_days"])
                row["display_mastery"] = (
                    row["p_mastery"] if row["evidence_level"] == "repeated" else None)
            summary = overview_stats(per_kp)
            answered = sum(row["answered"] for row in per_kp)
            correct = sum(row["correct"] for row in per_kp)
            sessions = db.execute("SELECT COUNT(*) FROM quiz_sessions WHERE course_id=? AND user_id=?",
                                  (course_id,USER_ID)).fetchone()[0]
            provisional = db.execute("""SELECT COUNT(*) FROM answer_records a
                JOIN quiz_items i ON i.id=a.quiz_item_id
                JOIN quiz_sessions s ON s.id=i.session_id
                WHERE s.course_id=? AND s.user_id=? AND i.selection_reason=?""",
                (course_id,USER_ID,BLIND_REASON)).fetchone()[0]
            return {"course_id":course_id,"user_id":USER_ID,"sessions":sessions,
                    "answered":answered,"correct":correct,
                    "accuracy":round(correct / answered,4) if answered else None,
                    "provisional_answers":provisional,
                    "evidence_summary":summary,
                    "mastery_model":MODEL_KEY,
                    "bkt_warning":"Uncalibrated baseline BKT: fixed parameters. "
                        "The estimate is hidden until repeated independent observations "
                        "across different days; those observations do not validate the "
                        "probability as a measured ability score.",
                    "knowledge_points":per_kp}
