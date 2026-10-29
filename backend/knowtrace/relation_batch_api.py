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

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .db import transaction
from .relations_api import creates_required_cycle
from .grouped_relations import plan_groups, generate_group_relations
from .cross_chapter import bridge_groups, completed_bridge_pairs


class StartRelations(BaseModel):
    provider: Literal["default","ollama","mimo"] = "default"
    max_focus_points: int = Field(default=30, ge=1, le=2000)
    per_focus: int = Field(default=2, ge=1, le=5)
    mode: Literal["single","grouped","bridge"] = "single"
    group_size: int = Field(default=12, ge=2, le=16)
    confirm_large_run: bool = False


class ApproveRelations(BaseModel):
    confirmed: bool
    include_visual_sources: bool = False
    types: list[Literal["prerequisite_required","prerequisite_recommended",
                        "related","uses","compares"]] = Field(min_length=1,max_length=5)


def register_relation_batch_routes(
    app: FastAPI, db_path: Path,
    generate_for_focus: Callable[[str,str,str,int],dict],
    resolve_provider,
):
    lock=threading.RLock()
    workers: dict[str,threading.Thread]={}
    live_lock=threading.RLock()
    live_text: dict[str,dict]={}

    def begin_live(batch_id: str, index: int, total: int, group_size: int):
        with live_lock:
            if len(live_text)>80:
                for old in list(live_text)[:25]:
                    live_text.pop(old,None)
            live_text[batch_id]={
                "group_index":index+1,"total_groups":total,
                "group_size":group_size,"phase":"awaiting_first_token",
                "text":"","received_chars":0,"truncated":False,
            }

    def append_live(batch_id: str, delta: str):
        # Display only visible response text; never expose model reasoning.
        with live_lock:
            data=live_text.get(batch_id)
            if not data:return
            data["received_chars"]+=len(delta)
            data["text"]=(data["text"]+delta)[-16000:]
            data["truncated"]=data["received_chars"]>16000
            data["phase"]="streaming"

    def finish_live(batch_id: str, phase: str):
        with live_lock:
            if batch_id in live_text:
                live_text[batch_id]["phase"]=phase


    def get(db, id: str):
        row=db.execute("SELECT * FROM relation_batch_runs WHERE id=?",(id,)).fetchone()
        if row is None:raise HTTPException(404,"Relation batch not found")
        return dict(row)

    def public(row: dict) -> dict:
        result={k:v for k,v in row.items() if k!="focus_ids_json"}
        if row["bridge_strategy"]:
            result["mode"]="bridge"
        result["total_points"]=row["total_points"] or row["total"]
        result["completed_points"]=(
            row["completed_points"] if row["mode"]=="grouped" else row["completed"]
        )
        result["progress"]=round(
            result["completed_points"]*100/max(1,result["total_points"]),1)
        result["remaining_requests"]=max(0,row["total"]-row["completed"])
        result["estimated_request_count"]=row["total"]
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
                    focus=focus_ids[i]
                    focus_id=focus[0] if isinstance(focus,list) else focus
                    begin_live(batch_id,i,job["total"],
                               len(focus) if isinstance(focus,list) else 1)
                    db.execute("""UPDATE relation_batch_runs
                                  SET current_focus=?,updated_at=CURRENT_TIMESTAMP
                                  WHERE id=?""",(focus_id,batch_id))
                try:
                    if job["mode"]=="grouped":
                        result=generate_group_relations(
                            db_path,job["course_id"],focus,job["provider"],
                            job["per_focus"],resolve_provider,
                            cross_chapter_only=bool(job["bridge_strategy"]),
                            on_delta=lambda delta: append_live(batch_id,delta))
                    else:
                        result=generate_for_focus(job["course_id"],focus_id,
                                                  job["provider"],job["per_focus"])
                    finish_live(batch_id,"validated")
                    created=int(result.get("created",0))
                    warning_count=len(result.get("warnings",[]))
                    skipped=0
                except HTTPException as error:
                    finish_live(batch_id,"failed")
                    # Stale knowledge, no evidence, or too few remaining points
                    # are skips, not fatal model/provider errors.
                    if error.status_code in (404,409) or (
                        error.status_code==422 and
                        "No linked ready course sources" in str(error.detail)):
                        created,warning_count,skipped=0,1,1
                    else:
                        with transaction(db_path) as db:
                            db.execute("""UPDATE relation_batch_runs SET status='stopped',
                                         last_error=?,current_focus=NULL,
                                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                                       (f"Failed at item {i+1}: {str(error.detail)[:220]}",batch_id))
                        return
                except Exception as error:
                    finish_live(batch_id,"failed")
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
                    completed_points = (current["completed_points"] or 0) + (
                        len(focus) if isinstance(focus,list) else 1)
                    db.execute("""UPDATE relation_batch_runs
                                 SET next_index=?,completed=?,completed_points=?,
                                   skipped=skipped+?,created_relations=created_relations+?,
                                   warning_count=warning_count+?,
                                   status=?,last_error=NULL,current_focus=NULL,
                                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                               (i+1,i+1,completed_points,skipped,created,
                                warning_count,final,batch_id))
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

    @app.get("/api/relation-batches/{id}/live")
    def read_visible_stream(id: str):
        # Ephemeral: source text and model output are not written to SQLite.
        # Jobs themselves remain persisted for normal stop/resume.
        with transaction(db_path) as db:
            row=get(db,id)
        with live_lock:
            display=dict(live_text.get(id) or {})
        return {
            "job_id":id,
            "status":row["status"],
            "phase":display.get("phase","not_streaming"),
            "group_index":display.get("group_index",row["completed"]),
            "total_groups":display.get("total_groups",row["total"]),
            "group_size":display.get("group_size",0),
            "text":display.get("text",""),
            "received_chars":display.get("received_chars",0),
            "truncated":display.get("truncated",False),
            "stream_supported":row["mode"]=="grouped",
        }

    @app.get("/api/courses/{course_id}/relation-batches/bridge-preview")
    def bridge_preview(course_id: str,
                       limit: int = Query(default=30,ge=2,le=2000),
                       group_size: int = Query(default=12,ge=2,le=16)):
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                              (course_id,)).fetchone():
                raise HTTPException(404,"Course not found")
            counts=db.execute("""SELECT
               COUNT(DISTINCT p.id) AS eligible,
               COUNT(DISTINCT p.chapter_id) AS chapter_count,
               COUNT(DISTINCT CASE WHEN p.chapter_id IS NULL THEN p.id END)
                   AS unassigned_points,
               COUNT(DISTINCT d.id) AS source_documents
               FROM knowledge_points p
               JOIN knowledge_point_sources s ON s.knowledge_point_id=p.id
               JOIN document_chunks c ON c.id=s.chunk_id
               JOIN documents d ON d.id=c.document_id
               WHERE p.course_id=? AND p.review_status='approved'
                 AND p.archived_at IS NULL AND d.course_id=p.course_id
                 AND d.status='ready'""",(course_id,)).fetchone()
            previous=completed_bridge_pairs(db,course_id)
        candidates=bridge_groups(db_path,course_id,previous,limit,group_size)
        return {
            "eligible":counts["eligible"],
            "actual_chapters":counts["chapter_count"],
            "unassigned_points":counts["unassigned_points"],
            "source_documents":counts["source_documents"],
            "already_compared_pairs":len(previous),
            "candidate_groups":len(candidates),
            "participating_points":sum(map(len,candidates)),
            "note":("没有足够的跨章节或跨原始文件候选。请先补全知识点的真实章节归属；"
                    "如果所有未分章知识点来自同一份资料，系统不会虚构章节。"
                    if not candidates else
                    "这里只计算有文本或共同来源线索的候选，不代表 AI 会为每组生成关系。"),
        }

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
            if payload.mode=="bridge":
                # Cross-chapter comparison is independent of already completed
                # intra-chapter runs. A point may participate again in a NEW
                # chapter pair, but a previously completed bridge group is not
                # repeated or billed a second time.
                compared=completed_bridge_pairs(db,course_id)
                plan=bridge_groups(db_path,course_id,compared,
                                   payload.max_focus_points,payload.group_size)
                point_count=sum(map(len,plan))
            else:
                done=set()
                for row in db.execute("""SELECT focus_ids_json,next_index
                    FROM relation_batch_runs WHERE course_id=? AND bridge_strategy=0""",
                    (course_id,)).fetchall():
                    for checkpoint in json.loads(row["focus_ids_json"])[:row["next_index"]]:
                        if isinstance(checkpoint,list):done.update(checkpoint)
                        else:done.add(checkpoint)
                ids=[]
                for row in db.execute("""SELECT kp.id FROM knowledge_points kp
                    WHERE kp.course_id=? AND kp.review_status='approved'
                      AND kp.archived_at IS NULL
                      AND EXISTS (SELECT 1 FROM knowledge_point_sources s
                        JOIN document_chunks c ON c.id=s.chunk_id
                        JOIN documents d ON d.id=c.document_id
                        WHERE s.knowledge_point_id=kp.id
                          AND d.course_id=kp.course_id AND d.status='ready')
                    ORDER BY kp.created_at,kp.id""",(course_id,)).fetchall():
                    if row["id"] not in done:
                        ids.append(row["id"])
                    if len(ids)>=payload.max_focus_points:break
                if payload.mode=="grouped":
                    plan=plan_groups(db_path,course_id,done,payload.max_focus_points,
                                     payload.group_size)
                    point_count=sum(map(len,plan))
                else:
                    plan=ids
                    point_count=len(ids)
            if not plan:
                raise HTTPException(409,
                    "No unexamined cross-chapter candidates with source/term overlap"
                    if payload.mode=="bridge" else
                    "No remaining approved source-linked knowledge points")
            id=str(uuid4())
            db.execute("""INSERT INTO relation_batch_runs
                (id,course_id,provider,focus_ids_json,per_focus,total,
                 mode,bridge_strategy,group_size,total_points,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,'running')""",
                (id,course_id,payload.provider,json.dumps(plan),payload.per_focus,len(plan),
                 "grouped" if payload.mode=="bridge" else payload.mode,
                 int(payload.mode=="bridge"),
                 payload.group_size if payload.mode!="single" else 1,point_count))
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
                    # Allow a deliberate, separate user opt-in for image-based
                    # model descriptions; never label it as verified original.
                    counts["visual"]+=1
                    if not body.include_visual_sources:
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
