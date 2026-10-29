"""Course-scale AI relation discovery and explicit bulk relation approval.

Workers call the existing grounded single-focus generator and checkpoint each
completed focus. No all-pairs (O(N²)) LLM calls, no fabricated edges.
Only previously approved knowledge points qualify as relation endpoints.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import transaction
from .relations_api import creates_required_cycle


class StartRelations(BaseModel):
    provider: Literal["default","ollama","mimo"] = "default"
    max_focus_points: int = Field(default=30, ge=1, le=2000)
    per_focus: int = Field(default=2, ge=1, le=5)
    confirm_large_run: bool = False


class ApproveRelations(BaseModel):
    confirmed: bool
    types: list[Literal["prerequisite_required","prerequisite_recommended",
                        "related","uses","compares"]] = Field(min_length=1,max_length=5)


def register_relation_batch_routes(
    app: FastAPI, db_path: Path,
    generate_for_focus: Callable[[str,str,str,int],dict],
):
    lock=threading.RLock()
    workers: dict[str,threading.Thread]={}

    def get(db, id: str):
        row=db.execute("SELECT * FROM relation_batch_runs WHERE id=?",(id,)).fetchone()
        if row is None:raise HTTPException(404,"Relation batch not found")
        return dict(row)

    def public(row: dict) -> dict:
        result={k:v for k,v in row.items() if k!="focus_ids_json"}
        result["progress"]=round(row["completed"]*100/max(1,row["total"]),1)
        return result

    def run(batch_id: str):
        try:
            while True:
                with transaction(db_path) as db:
                    job=get(db,batch_id)
                    if job["status"]=="stopping":
                        db.execute("""UPDATE relation_batch_runs SET status='stopped',
                                     current_focus=NULL,updated_at=CURRENT_TIMESTAMP
                                     WHERE id=?""",(batch_id,))
                        return
                    if job["status"]!="running":return
                    focus_ids=json.loads(job["focus_ids_json"])
                    i=job["next_index"]
                    if i >= len(focus_ids):
                        db.execute("""UPDATE relation_batch_runs SET status='succeeded',
                                      current_focus=NULL,updated_at=CURRENT_TIMESTAMP
                                      WHERE id=?""",(batch_id,))
                        return
                    focus_id=focus_ids[i]
                    db.execute("""UPDATE relation_batch_runs
                                  SET current_focus=?,updated_at=CURRENT_TIMESTAMP
                                  WHERE id=?""",(focus_id,batch_id))
                try:
                    result=generate_for_focus(job["course_id"],focus_id,
                                              job["provider"],job["per_focus"])
                    created=int(result.get("created",0))
                    warning_count=len(result.get("warnings",[]))
                    skipped=0
                except HTTPException as error:
                    # Stale knowledge, no evidence, or too few remaining points
                    # are skips, not fatal model/provider errors.
                    if error.status_code in (404,409,422):
                        created,warning_count,skipped=0,1,1
                    else:
                        with transaction(db_path) as db:
                            db.execute("""UPDATE relation_batch_runs SET status='stopped',
                                         last_error=?,current_focus=NULL,
                                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                                       (f"Failed at item {i+1}: {str(error.detail)[:220]}",batch_id))
                        return
                except Exception as error:
                    # Do not leak raw model responses, keys or course material.
                    with transaction(db_path) as db:
                        db.execute("""UPDATE relation_batch_runs SET status='stopped',
                                      last_error=?,current_focus=NULL,
                                      updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                                   (f"Failed at item {i+1}: {type(error).__name__}",batch_id))
                    return
                with transaction(db_path) as db:
                    current=get(db,batch_id)
                    if current["next_index"]!=i:return
                    final=("stopped" if current["status"]=="stopping"
                           else "succeeded" if i+1>=len(focus_ids) else "running")
                    db.execute("""UPDATE relation_batch_runs
                                 SET next_index=?,completed=?,skipped=skipped+?,
                                   created_relations=created_relations+?,
                                   warning_count=warning_count+?,
                                   status=?,last_error=NULL,current_focus=NULL,
                                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                               (i+1,i+1,skipped,created,warning_count,final,batch_id))
                    if final!="running":return
        finally:
            with lock:
                workers.pop(batch_id,None)

    def spawn(id: str):
        with lock:
            if id in workers:return
            worker=threading.Thread(target=run,args=(id,),daemon=True,
                                    name="knowtrace-relations-"+id[:8])
            workers[id]=worker
            worker.start()

    @app.post("/api/courses/{course_id}/relation-batches",status_code=201)
    def start(course_id: str, payload: StartRelations):
        if payload.max_focus_points > 30 and not payload.confirm_large_run:
            raise HTTPException(422,"Confirm large run cost before >30 model calls")
        with lock,transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                              (course_id,)).fetchone():
                raise HTTPException(404,"Course not found")
            exists=db.execute("""SELECT 1 FROM relation_batch_runs
                WHERE course_id=? AND status IN ('running','stopping')""",(course_id,)).fetchone()
            if exists:raise HTTPException(409,"This course already has a running relation discovery job")
            # Include approved points with at least one *ready* source only.
            # This avoids thousands of pointless requests on source-less items.
            ids=[r["id"] for r in db.execute("""SELECT kp.id FROM knowledge_points kp
                WHERE kp.course_id=? AND kp.review_status='approved'
                  AND kp.archived_at IS NULL
                  AND EXISTS (SELECT 1 FROM knowledge_point_sources s
                    JOIN document_chunks c ON c.id=s.chunk_id
                    JOIN documents d ON d.id=c.document_id
                    WHERE s.knowledge_point_id=kp.id
                      AND d.course_id=kp.course_id AND d.status='ready')
                ORDER BY kp.created_at,kp.id LIMIT ?""",
                (course_id,payload.max_focus_points)).fetchall()]
            if len(ids)<2:
                raise HTTPException(409,"Need at least two approved source-linked knowledge points")
            id=str(uuid4())
            db.execute("""INSERT INTO relation_batch_runs
                (id,course_id,provider,focus_ids_json,per_focus,total,status)
                VALUES(?,?,?,?,?,?,'running')""",
                (id,course_id,payload.provider,json.dumps(ids),payload.per_focus,len(ids)))
            result=public(get(db,id))
        spawn(id)
        return result

    @app.get("/api/courses/{course_id}/relation-batches")
    def list_batches(course_id: str):
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM courses WHERE id=?",(course_id,)).fetchone():
                raise HTTPException(404,"Course not found")
            rows=db.execute("""SELECT * FROM relation_batch_runs WHERE course_id=?
                  ORDER BY created_at DESC,rowid DESC LIMIT 20""",(course_id,)).fetchall()
            return [public(dict(row)) for row in rows]

    @app.post("/api/relation-batches/{id}/stop")
    def stop(id: str):
        with transaction(db_path) as db:
            row=get(db,id)
            if row["status"]=="running":
                db.execute("UPDATE relation_batch_runs SET status='stopping' WHERE id=?",(id,))
            return public(get(db,id))

    @app.post("/api/relation-batches/{id}/resume")
    def resume(id: str):
        with lock,transaction(db_path) as db:
            row=get(db,id)
            if row["status"] not in ("stopped","interrupted"):
                raise HTTPException(409,"Only stopped and interrupted runs can resume")
            active=db.execute("""SELECT 1 FROM relation_batch_runs WHERE course_id=?
                 AND id<>? AND status IN ('running','stopping')""",
                 (row["course_id"],id)).fetchone()
            if active:raise HTTPException(409,"Another relation job is active for this course")
            db.execute("""UPDATE relation_batch_runs SET status='running',
                         last_error=NULL,current_focus=NULL,
                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",(id,))
            item=public(get(db,id))
        spawn(id)
        return item

    @app.get("/api/courses/{course_id}/relations/bulk-review-preview")
    def preview(course_id: str):
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                              (course_id,)).fetchone():
                raise HTTPException(404,"Course not found")
            rows=db.execute("""SELECT r.relation_type,COUNT(*) AS total,
                   SUM(CASE WHEN s.chunk_id IS NULL THEN 1 ELSE 0 END) AS missing_evidence,
                   SUM(CASE WHEN c.content_kind='figure_description' THEN 1 ELSE 0 END) AS visual
                FROM knowledge_relations r
                JOIN knowledge_points a ON a.id=r.source_kp_id
                JOIN knowledge_points b ON b.id=r.target_kp_id
                LEFT JOIN knowledge_relation_sources s ON s.relation_id=r.id
                LEFT JOIN document_chunks c ON c.id=s.chunk_id
                WHERE a.course_id=? AND b.course_id=?
                  AND a.review_status='approved' AND b.review_status='approved'
                  AND a.archived_at IS NULL AND b.archived_at IS NULL
                  AND r.review_status='pending'
                GROUP BY r.relation_type ORDER BY r.relation_type""",(course_id,course_id)).fetchall()
            return {"types":[dict(r) for r in rows],
                    "total":sum(r["total"] for r in rows)}

    @app.post("/api/courses/{course_id}/relations/bulk-approve")
    def approve(course_id: str, body: ApproveRelations):
        if not body.confirmed:raise HTTPException(422,"Explicit user confirmation required")
        counts={"approved":0,"skipped":0,"cycles":0,"visual":0}
        approved_ids=[]
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                              (course_id,)).fetchone():
                raise HTTPException(404,"Course not found")
            placeholders=",".join("?" for _ in body.types)
            rows=db.execute("""SELECT r.id,r.source_kp_id,r.target_kp_id,r.relation_type
                FROM knowledge_relations r
                JOIN knowledge_points a ON a.id=r.source_kp_id
                JOIN knowledge_points b ON b.id=r.target_kp_id
                WHERE a.course_id=? AND b.course_id=?
                AND r.relation_type IN ("""+placeholders+""")
                AND r.review_status='pending'
                AND a.review_status='approved' AND b.review_status='approved'
                AND a.archived_at IS NULL AND b.archived_at IS NULL
                ORDER BY r.created_at,r.id""",
                (course_id,course_id,*body.types)).fetchall()
            for row in rows:
                src=db.execute("""SELECT c.content_kind
                    FROM knowledge_relation_sources s
                    JOIN document_chunks c ON c.id=s.chunk_id
                    JOIN documents d ON d.id=c.document_id
                    WHERE s.relation_id=? AND d.course_id=?
                      AND d.status='ready' LIMIT 1""",(row["id"],course_id)).fetchone()
                if not src:
                    counts["skipped"]+=1;continue
                if src["content_kind"]=="figure_description":
                    # An AI description is not the same as verified original PDF.
                    counts["visual"]+=1
                    counts["skipped"]+=1
                    continue
                if row["relation_type"]=="prerequisite_required" and creates_required_cycle(
                    db,row["source_kp_id"],row["target_kp_id"]):
                    counts["cycles"]+=1;counts["skipped"]+=1;continue
                db.execute("UPDATE knowledge_relations SET review_status='approved' WHERE id=?",(row["id"],))
                db.execute("""INSERT INTO knowledge_relation_events
                    (id,relation_id,action,before_status,after_status)
                    VALUES(? , ?,'approved','pending','approved')""",(str(uuid4()),row["id"]))
                counts["approved"]+=1
                approved_ids.append(row["id"])
        return {**counts,"approved_ids":approved_ids[:25]}

    def recover():
        with transaction(db_path) as db:
            db.execute("""UPDATE relation_batch_runs
                         SET status='interrupted',current_focus=NULL
                         WHERE status IN ('running','stopping')""")
    return recover
