"""Deliberate direct batch approval of existing source-linked knowledge drafts.

This endpoint does NOT call a second AI. It also never silently approves:
the caller explicitly supplies selected IDs and confirms bypassing AI review.
Each accepted point is re-checked within a transaction and written to the
normal knowledge audit event history. Source-less AI drafts cannot pass.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import transaction


class DirectBulkApprove(BaseModel):
    confirmed: bool
    point_ids: list[str] = Field(min_length=1,max_length=2000)
    # Distinguishes a deliberate explicit bypass from the verified AI path.
    bypass_ai_review: bool


def overview(db, course_id: str) -> list[dict]:
    if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                      (course_id,)).fetchone():
        raise HTTPException(404,"Course not found")
    rows=db.execute("""SELECT p.id,p.canonical_name,p.review_status,p.created_job_id,
            COUNT(DISTINCT CASE WHEN d.course_id=p.course_id AND d.status='ready'
                    THEN s.chunk_id END) AS source_count,
            MAX(CASE WHEN d.course_id=p.course_id AND c.content_kind='figure_description'
                     THEN 1 ELSE 0 END) AS has_visual
          FROM knowledge_points p
          LEFT JOIN knowledge_point_sources s ON s.knowledge_point_id=p.id
          LEFT JOIN document_chunks c ON c.id=s.chunk_id
          LEFT JOIN documents d ON d.id=c.document_id
          WHERE p.course_id=? AND p.archived_at IS NULL
            AND p.review_status='pending'
          GROUP BY p.id,p.canonical_name,p.review_status,p.created_job_id
          ORDER BY p.created_at,p.id""",(course_id,)).fetchall()
    result=[dict(row) for row in rows]
    names=Counter(row["canonical_name"].strip().casefold() for row in result)
    for row in result:
        row["has_visual"]=bool(row["has_visual"])
        row["duplicate_name"]=names[row["canonical_name"].strip().casefold()]>1
        row["ai_generated"]=row["created_job_id"] is not None
        row["eligible"]=row["source_count"]>0
        row.pop("created_job_id")
    return result


def register_direct_bulk_review(app: FastAPI, db_path: Path):
    @app.get("/api/courses/{course_id}/knowledge-points/bulk-approval-preview")
    def preview(course_id: str):
        with transaction(db_path) as db:
            rows=overview(db,course_id)
            return {
                "points": rows,
                "eligible":sum(item["eligible"] for item in rows),
                "ai_generated":sum(item["ai_generated"] for item in rows),
                "visual":sum(item["has_visual"] for item in rows),
                "missing_evidence":sum(not item["eligible"] for item in rows),
            }

    @app.post("/api/courses/{course_id}/knowledge-points/bulk-approve")
    def bulk_approve(course_id: str,body:DirectBulkApprove):
        if not body.confirmed or not body.bypass_ai_review:
            raise HTTPException(422,"Explicit confirmation to bypass AI review is required")
        approved,skipped=[],[]
        with transaction(db_path) as db:
            current={row["id"]:row for row in overview(db,course_id)}
            for point_id in dict.fromkeys(body.point_ids):
                row=current.get(point_id)
                if row is None:
                    skipped.append({"id":point_id,"reason":"Not pending / not in this course"});continue
                if not row["eligible"]:
                    skipped.append({"id":point_id,"reason":"No verified ready-course source link"});continue
                db.execute("""UPDATE knowledge_points SET review_status='approved',
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id=? AND course_id=? AND review_status='pending'
                         AND archived_at IS NULL""",(point_id,course_id))
                db.execute("""INSERT INTO knowledge_point_events
                    (id,knowledge_point_id,action,before_json,after_json)
                    VALUES(?,?,'approved',?,?)""",
                    (str(uuid4()),point_id,
                     json.dumps({"review_status":"pending"},ensure_ascii=False),
                     json.dumps({
                         "review_status":"approved",
                         "confirmation":"direct_bulk_user_approval_without_ai_review",
                         "has_visual_source":row["has_visual"],
                         "duplicate_name_warning":row["duplicate_name"],
                     },ensure_ascii=False)))
                approved.append(point_id)
        return {"applied":approved,"skipped":skipped}
