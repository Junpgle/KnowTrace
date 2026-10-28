"""Local single-user quizzes, immutable answer events, and BKT mastery estimates.

Only approved *latest* question versions may enter quizzes. Learners never
receive answers before submitting an item. This prototype has no auth: the
separate administrative question-version endpoint is still accessible locally.
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

USER_ID = "local"
MODEL_KEY = "bkt_v1"
BKT = BKTParameters()


class QuizStart(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    mode: Literal["normal"] = "normal"  # Adaptive selection is planned for later.


class SubmitAnswer(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    response: StrictStr | StrictBool
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
                i.question_version_id,i.primary_knowledge_point_id,v.question_type,v.stem,
                v.options_json,v.explanation,v.correct_answer_json,
                a.id AS answer_id,a.response_json,a.is_correct,a.answered_at,a.elapsed_ms
                FROM quiz_items i JOIN question_versions v ON v.id=i.question_version_id
                LEFT JOIN answer_records a ON a.quiz_item_id=i.id
                WHERE i.session_id=? ORDER BY i.position""", (session_id,)))
    items = []
    for record in records:
        answered = record["answer_id"] is not None
        obj = {"quiz_item_id": record["quiz_item_id"], "position": record["position"],
               "question_version_id": record["question_version_id"],
               "primary_knowledge_point_id": record["primary_knowledge_point_id"],
               "question_type": record["question_type"], "stem": record["stem"],
               "options": json.loads(record["options_json"]), "answered": answered}
        if answered:
            obj["result"] = {"answer_id": record["answer_id"],
                             "response": json.loads(record["response_json"]),
                             "is_correct": bool(record["is_correct"]),
                             "correct_answer": json.loads(record["correct_answer_json"]),
                             "explanation": record["explanation"],
                             "answered_at": record["answered_at"],
                             "elapsed_ms": record["elapsed_ms"]}
        items.append(obj)
    answered_count = sum(item["answered"] for item in items)
    correct_count = sum(bool(item["result"]["is_correct"]) for item in items if item["answered"])
    return {"id": session["id"], "course_id": session["course_id"],
            "mode": session["mode"], "status": session["status"],
            "started_at": session["started_at"], "finished_at": session["finished_at"],
            "total": len(items), "answered": answered_count, "correct": correct_count,
            "items": items}


def _answer_result(db: sqlite3.Connection, answer_id: str) -> dict:
    row = db.execute("""SELECT a.id AS answer_id,a.quiz_item_id,a.response_json,
           a.is_correct,a.answered_at,v.correct_answer_json,v.explanation,
           e.p_before,e.p_posterior,e.p_after,e.knowledge_point_id
           FROM answer_records a JOIN quiz_items i ON i.id=a.quiz_item_id
           JOIN question_versions v ON v.id=i.question_version_id
           LEFT JOIN mastery_events e ON e.answer_record_id=a.id AND e.model_key=?
           WHERE a.id=?""", (MODEL_KEY, answer_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Answer not found")
    return {"answer_id": row["answer_id"], "quiz_item_id": row["quiz_item_id"],
            "response": json.loads(row["response_json"]),
            "is_correct": bool(row["is_correct"]),
            "correct_answer": json.loads(row["correct_answer_json"]),
            "explanation": row["explanation"], "knowledge_point_id": row["knowledge_point_id"],
            "mastery": {"model": MODEL_KEY, "before": row["p_before"],
                        "posterior": row["p_posterior"], "after": row["p_after"]},
            "answered_at": row["answered_at"]}


def register_quiz_routes(app: FastAPI, database: Path) -> None:
    @app.post("/api/courses/{course_id}/quiz-sessions", status_code=201)
    def start_quiz(course_id: str, payload: QuizStart):
        with transaction(database) as db:
            if db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                          (course_id,)).fetchone() is None:
                raise HTTPException(404, "Course not found")
            rows = _rows(db.execute("""SELECT v.id AS version_id,k.knowledge_point_id
                FROM question_versions v
                JOIN questions q ON q.id=v.question_id
                JOIN question_knowledge_points k ON k.question_version_id=v.id AND k.is_primary=1
                JOIN knowledge_points kp ON kp.id=k.knowledge_point_id
                WHERE q.course_id=? AND q.archived_at IS NULL AND v.review_status='approved'
                AND kp.review_status='approved' AND kp.archived_at IS NULL
                AND v.version_no=(SELECT MAX(v2.version_no)
                                   FROM question_versions v2 WHERE v2.question_id=q.id)
                ORDER BY v.created_at,v.id""", (course_id,)))
            if len(rows) < payload.count:
                raise HTTPException(409, f"Only {len(rows)} approved latest questions available; requested {payload.count}")
            session_id = _uuid()
            db.execute("""INSERT OR IGNORE INTO users (id,username,display_name)
                       VALUES ('local','local','Local learner')""")
            db.execute("INSERT INTO quiz_sessions(id,user_id,course_id,mode) VALUES (?,?,?,'normal')",
                       (session_id, USER_ID, course_id))
            for position, row in enumerate(rows[:payload.count], start=1):
                db.execute("""INSERT INTO quiz_items(id,session_id,question_version_id,
                           primary_knowledge_point_id,position) VALUES (?,?,?,?,?)""",
                           (_uuid(), session_id, row["version_id"], row["knowledge_point_id"], position))
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
                v.question_type,v.correct_answer_json,s.status AS session_status,
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
            elif item["question_type"] == "true_false":
                if type(payload.response) is not bool:
                    raise HTTPException(422, "True/false response must be JSON boolean")
            else:
                raise HTTPException(422, "Unsupported question type")
            correct = payload.response == answer
            record_id = _uuid()
            db.execute("""INSERT INTO answer_records(id,quiz_item_id,client_request_id,
                       response_json,is_correct,elapsed_ms) VALUES (?,?,?,?,?,?)""",
                       (record_id, quiz_item_id, payload.client_request_id, serialized, int(correct), payload.elapsed_ms))
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
                 m.p_mastery,m.observations,
                 COUNT(a.id) AS answered,COALESCE(SUM(a.is_correct),0) AS correct
                 FROM knowledge_points kp
                 LEFT JOIN mastery_states m ON m.knowledge_point_id=kp.id
                    AND m.user_id=? AND m.model_key=?
                 LEFT JOIN quiz_items i ON i.primary_knowledge_point_id=kp.id
                 LEFT JOIN quiz_sessions s ON s.id=i.session_id AND s.user_id=?
                    AND s.course_id=?
                 LEFT JOIN answer_records a ON a.quiz_item_id=i.id AND s.id IS NOT NULL
                 WHERE kp.course_id=? AND kp.archived_at IS NULL
                 GROUP BY kp.id ORDER BY kp.canonical_name,kp.id""",
                 (USER_ID,MODEL_KEY,USER_ID,course_id,course_id)))
            for row in per_kp:
                row["accuracy"] = round(row["correct"] / row["answered"], 4) if row["answered"] else None
            answered = sum(row["answered"] for row in per_kp)
            correct = sum(row["correct"] for row in per_kp)
            sessions = db.execute("SELECT COUNT(*) FROM quiz_sessions WHERE course_id=? AND user_id=?",
                                  (course_id,USER_ID)).fetchone()[0]
            return {"course_id":course_id,"user_id":USER_ID,"sessions":sessions,
                    "answered":answered,"correct":correct,
                    "accuracy":round(correct / answered,4) if answered else None,
                    "mastery_model":MODEL_KEY,"bkt_warning":"Model estimate, not a validated ability score.",
                    "knowledge_points":per_kp}
