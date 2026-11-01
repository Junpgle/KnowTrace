"""Explicit bulk approval for eligible *latest* pending question versions.

A quote check is structural provenance, never a correctness proof. Risky
candidates stay pending for manual verification. User must deliberately opt in
to bypass individual review; every approval is audited. No AI calls are made.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .ai import compact
from .db import transaction
from .questions import validate_question_fields

QUOTE_MARKERS = ("Verbatim source text: ", "Verbatim source: ")


class BulkQuestionApproval(BaseModel):
    confirmed: bool
    accept_unverified_answers: bool
    question_version_ids: list[str] = Field(min_length=1, max_length=2000)


def inspect_pending(db, course_id: str) -> list[dict]:
    course = db.execute(
        "SELECT 1 FROM courses WHERE id=? AND status='active'", (course_id,)
    ).fetchone()
    if course is None:
        raise HTTPException(404, "Course not found")

    rows = db.execute("""SELECT v.id,v.question_id,v.stem,v.options_json,
          v.correct_answer_json,v.explanation,v.question_type,v.difficulty,
          v.review_status,v.version_no
          FROM question_versions v JOIN questions q ON q.id=v.question_id
          WHERE q.course_id=? AND q.archived_at IS NULL
            AND v.review_status='pending'
            AND v.version_no=(SELECT MAX(latest.version_no)
                  FROM question_versions latest WHERE latest.question_id=v.question_id)
          ORDER BY v.created_at,v.id""", (course_id,)).fetchall()

    # Detect exact normalized duplicates among current, non-rejected
    # versions, including approved ones. This does not claim semantic
    # deduplication.
    used = Counter(compact(row["stem"]) for row in db.execute("""
          SELECT v.stem FROM question_versions v
          JOIN questions q ON q.id=v.question_id
          WHERE q.course_id=? AND q.archived_at IS NULL
            AND v.review_status IN ('pending','approved')
            AND v.version_no=(SELECT MAX(latest.version_no)
                  FROM question_versions latest WHERE latest.question_id=v.question_id)
    """, (course_id,)))

    result = []
    for row in rows:
        problems = []
        vid = row["id"]
        try:
            options = json.loads(row["options_json"])
            answer = json.loads(row["correct_answer_json"])
            invalid = validate_question_fields(
                row["question_type"],row["stem"],options,answer,
                row["explanation"],row["difficulty"])
        except (ValueError, TypeError, KeyError):
            invalid = "题目字段无法解析"
        if invalid:
            problems.append("题型、答案或题干结构异常")

        kp_rows = db.execute("""SELECT k.knowledge_point_id,k.is_primary,
               p.review_status,p.archived_at,p.course_id,p.chapter_id
               FROM question_knowledge_points k
               JOIN knowledge_points p ON p.id=k.knowledge_point_id
               WHERE k.question_version_id=?""", (vid,)).fetchall()
        primaries = [p for p in kp_rows if p["is_primary"] == 1]
        if len(primaries)!=1:
            problems.append("主考查知识点缺失或重复")
        if not kp_rows or any(
            p["course_id"] != course_id or p["archived_at"] is not None
            or p["review_status"] != "approved" for p in kp_rows
        ):
            problems.append("关联知识点尚未全部通过审核")
        chapter_id = primaries[0]["chapter_id"] if len(primaries)==1 else None

        sources = db.execute("""SELECT s.evidence_note,c.content_kind,c.content_text,
              d.course_id,d.status
              FROM question_sources s JOIN document_chunks c ON c.id=s.chunk_id
              JOIN documents d ON d.id=c.document_id
              WHERE s.question_version_id=?""", (vid,)).fetchall()
        if not sources:
            problems.append("没有课件引用")
        if any(s["status"]!="ready" or s["course_id"]!=course_id for s in sources):
            problems.append("课件来源不属于当前课程或尚未就绪")
        if any(s["content_kind"]=="figure_description" for s in sources):
            problems.append("包含尚未核实的图片识别来源")
        if any("需复核" in (s["evidence_note"] or "") or
               "unverified" in (s["evidence_note"] or "").lower() for s in sources):
            problems.append("AI 生成时存在来源或关联考点复核提示")
        for s in sources:
            if s["content_kind"]=="figure_description":
                continue
            note = s["evidence_note"] or ""
            quote = None
            for marker in QUOTE_MARKERS:
                if marker in note:
                    quote = note.split(marker,1)[1].strip()
                    break
            if (not quote or len(compact(quote))<10
                or compact(quote) not in compact(s["content_text"] or "")):
                problems.append("课件引用缺少可验证的逐字原文")
                break
        if used[compact(row["stem"])]>1:
            problems.append("发现相同题干的待审或已审题")
        result.append({
            "id":vid, "stem":row["stem"], "version_no":row["version_no"],
            "question_type":row["question_type"], "chapter_id":chapter_id,
            "source_count":len(sources), "knowledge_count":len(kp_rows),
            "eligible":not problems, "issues":problems,
        })
    return result


def register_bulk_question_review(app: FastAPI, database: Path) -> None:
    @app.get("/api/courses/{course_id}/questions/bulk-approval-preview")
    def preview(course_id: str):
        with transaction(database) as db:
            candidates=inspect_pending(db,course_id)
        return {
            "total":len(candidates),
            "eligible":sum(row["eligible"] for row in candidates),
            "flagged":sum(not row["eligible"] for row in candidates),
            "questions":candidates,
            "warning":"来源及结构校验不证明模型答案正确。批量通过是用户明确接受跳过逐题人工复核的决定。",
        }

    @app.post("/api/courses/{course_id}/questions/bulk-approve")
    def approve(course_id: str,payload: BulkQuestionApproval):
        if not payload.confirmed or not payload.accept_unverified_answers:
            raise HTTPException(422,"Must explicitly confirm bypassing per-question content review")
        approved,skipped=[],[]
        with transaction(database) as db:
            # Read + validate + update in one serialized SQLite write
            # transaction, so a parallel revision cannot change eligibility.
            db.execute("BEGIN IMMEDIATE")
            candidates = {row["id"]:row for row in inspect_pending(db,course_id)}
            for vid in dict.fromkeys(payload.question_version_ids):
                item=candidates.get(vid)
                if item is None:
                    skipped.append({"id":vid,"reason":"题目并非本课程最新待审核版本"})
                    continue
                if not item["eligible"]:
                    skipped.append({"id":vid,"reason":"；".join(item["issues"])})
                    continue
                changed=db.execute("""UPDATE question_versions SET
                    review_status='approved',
                    approved_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id=? AND review_status='pending'
                      AND version_no=(SELECT MAX(x.version_no)
                        FROM question_versions x
                        WHERE x.question_id=question_versions.question_id)""",(vid,))
                if changed.rowcount != 1:
                    skipped.append({"id":vid,"reason":"题目状态已变更"})
                    continue
                db.execute("""INSERT INTO question_bulk_review_events
                    (id,course_id,question_version_id,action,details_json)
                    VALUES (?,?,?,'approved',?)""",(
                    str(uuid4()),course_id,vid,
                    json.dumps({"method":"direct_batch_user_confirmation",
                                "safety_checks":"source_and_structure_only",
                                "correct_answer_verified":False},
                               ensure_ascii=False),
                ))
                approved.append(vid)
        return {"applied":approved,"skipped":skipped}
