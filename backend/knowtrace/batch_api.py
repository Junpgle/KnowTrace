"""Durable extraction tasks and truthful progress for concurrent course documents.

The UI polls committed page checkpoints and an explicit in-flight page range,
not an invented fractional completion percentage. At most two model calls run
in parallel in this single-worker local MVP. Tasks are keyed by document and
never reuse another document's result or notification.
"""
from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .db import transaction


class StartBatch(BaseModel):
    provider: Literal["default", "ollama", "mimo"] = "default"
    pages_per_batch: int = Field(default=2, ge=1, le=4)
    include_visual: bool = False
    page_start: int = Field(default=1, ge=1)
    page_end: int | None = Field(default=None, ge=1)


def register_batch_routes(app: FastAPI, db_path: Path, extract_page_range: Callable):
    lock = threading.RLock()
    slots = threading.BoundedSemaphore(2)
    threads: dict[str, threading.Thread] = {}

    def row_or_404(db, job_id: str):
        row = db.execute("SELECT * FROM batch_extractions WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Extraction task not found")
        return dict(row)

    def public(row: dict):
        end = row["range_end"] or row["total_pages"]
        start = row["range_start"] or 1
        count = end - start + 1
        return {
            **row,
            "page_count": count,
            "progress": round(min(100, row["completed_pages"] * 100 / count), 1),
            "mode": "all" if start == 1 and end == row["total_pages"] else "range",
            "stop_note": "Stop applies after the current model request finishes.",
        }

    def record_notice(db, row: dict, level: str, title: str, message: str):
        db.execute("""INSERT INTO task_notifications
            (id,task_id,course_id,document_id,level,title,message)
            VALUES(?,?,?,?,?,?,?)""",
            (str(uuid4()), row["id"], row["course_id"], row["document_id"],
             level, title, message[:400]))

    def done(db, row, status: str, *, error: str | None = None):
        db.execute("""UPDATE batch_extractions
            SET status=?,work_stage='idle',in_flight_start=NULL,
                in_flight_end=NULL,in_flight_started_at=NULL,
                last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                   (status,error,row["id"]))
        name = db.execute("SELECT original_filename FROM documents WHERE id=?",
                          (row["document_id"],)).fetchone()["original_filename"]
        if status == "succeeded":
            count = row["created_points"]
            reused = row["reused_points"]
            skipped = row["skipped_pages"]
            if count or reused:
                level,heading = "success","知识点提取成功"
            else:
                level,heading = "info","资料处理完成，未生成知识点"
            record_notice(db,row,level,heading,
                          f"{name}：新增 {count} 项，复用 {reused} 项，跳过 {skipped} 页。")
        elif status == "failed":
            record_notice(db,row,"error","知识点提取失败",
                          f"{name}：{error or '模型处理失败'}。已完成的页面不会丢失，可从失败页重试。")
        elif status == "interrupted":
            record_notice(db,row,"info","提取任务意外中断",
                          f"{name}：后端重启，已保存进度，可手动继续。")
        elif status == "stopped":
            record_notice(db,row,"info","提取任务已停止",
                          f"{name}：已完成 {row['completed_pages']} 页，可随时继续。")

    def run(job_id: str):
        try:
            while True:
                # Stop while waiting for a free model slot, without submitting
                # another paid request.
                while not slots.acquire(timeout=0.25):
                    with transaction(db_path) as db:
                        wait = row_or_404(db,job_id)
                        if wait["status"] == "stopping":
                            done(db,wait,"stopped")
                            return
                        if wait["status"] != "running":
                            return
                try:
                    with transaction(db_path) as db:
                        row = row_or_404(db,job_id)
                        if row["status"] == "stopping":
                            done(db,row,"stopped")
                            return
                        if row["status"] != "running":
                            return
                        end_limit = row["range_end"] or row["total_pages"]
                        if row["next_page"] > end_limit:
                            done(db,row,"succeeded")
                            return
                        start = row["next_page"]
                        end = min(end_limit,start+row["pages_per_batch"]-1)
                        db.execute("""UPDATE batch_extractions
                            SET work_stage='extracting',in_flight_start=?,in_flight_end=?,
                                in_flight_started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                                updated_at=CURRENT_TIMESTAMP WHERE id=?""",(start,end,job_id))
                    try:
                        result = extract_page_range(row["document_id"],start,end,
                                                    bool(row["include_visual"]),row["provider"])
                    except HTTPException as exc:
                        # Entirely unreadable chunks can still be completed with
                        # a truthful 'skipped' count when vision is disabled.
                        if exc.status_code == 422 and exc.detail == "No sufficiently detailed text found; enable vision or select different pages":
                            result = {"created":0,"reused":0,
                                      "skipped_sparse_pages":list(range(start,end+1))}
                        else:
                            with transaction(db_path) as db:
                                current = row_or_404(db,job_id)
                                done(db,current,"failed",error=f"第 {start}–{end} 页：{str(exc.detail)[:240]}")
                            return
                    except Exception as exc:
                        # Do not display arbitrary HTTP response bodies: they
                        # could contain remote credentials or source text.
                        with transaction(db_path) as db:
                            current = row_or_404(db,job_id)
                            done(db,current,"failed",error=f"第 {start}–{end} 页：{type(exc).__name__}")
                        return
                    with transaction(db_path) as db:
                        current = row_or_404(db,job_id)
                        if current["next_page"] != start:
                            return
                        completed = end - (current["range_start"] or 1) + 1
                        end_limit = current["range_end"] or current["total_pages"]
                        next_status = ("stopped" if current["status"] == "stopping"
                                       else "succeeded" if end >= end_limit else "running")
                        db.execute("""UPDATE batch_extractions SET next_page=?,
                          completed_pages=?,created_points=created_points+?,
                          reused_points=reused_points+?,skipped_pages=skipped_pages+?,
                          status=?,last_error=NULL,work_stage='queued',
                          in_flight_start=NULL,in_flight_end=NULL,
                          in_flight_started_at=NULL,updated_at=CURRENT_TIMESTAMP
                          WHERE id=?""",
                          (end+1,completed,result["created"],result["reused"],
                           len(result.get("skipped_sparse_pages",[])),next_status,job_id))
                        if next_status != "running":
                            done(db,row_or_404(db,job_id),next_status)
                            return
                finally:
                    slots.release()
        finally:
            with lock:
                threads.pop(job_id,None)

    def spawn(job_id: str):
        # One server worker only. Multiple uvicorn workers would each have
        # their own semaphore; use a persistent queue for production.
        with lock:
            if job_id in threads:
                return
            thread = threading.Thread(target=run,args=(job_id,),daemon=True,
                                      name="knowtrace-extract-"+job_id[:8])
            threads[job_id] = thread
            thread.start()

    @app.post("/api/documents/{document_id}/batch-extractions",status_code=201)
    def start_batch(document_id: str,body:StartBatch):
        with lock,transaction(db_path) as db:
            doc = db.execute("""SELECT id,course_id,total_pages,media_type
                       FROM documents WHERE id=? AND status='ready'""",
                             (document_id,)).fetchone()
            if not doc or not doc["total_pages"]:
                raise HTTPException(404,"Ready document not found")
            end = body.page_end or doc["total_pages"]
            if body.page_start > end or end > doc["total_pages"]:
                raise HTTPException(422,"Invalid document page range")
            if body.page_end is not None and end-body.page_start >= 4:
                raise HTTPException(422,"Manual extraction selects at most 4 pages")
            if body.include_visual and doc["media_type"] != "application/pdf":
                raise HTTPException(422,"Visual extraction supports PDF only")
            if db.execute("""SELECT 1 FROM batch_extractions WHERE document_id=?
                    AND status IN ('running','stopping')""",(document_id,)).fetchone():
                raise HTTPException(409,"This document is already being extracted; view it in the task center")
            job_id = str(uuid4())
            db.execute("""INSERT INTO batch_extractions
                   (id,course_id,document_id,provider,pages_per_batch,include_visual,
                    next_page,total_pages,range_start,range_end)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (job_id,doc["course_id"],document_id,body.provider,
                    body.pages_per_batch,int(body.include_visual),
                    body.page_start,doc["total_pages"],body.page_start,end))
            item = public(row_or_404(db,job_id))
        spawn(job_id)
        return item

    @app.get("/api/documents/{document_id}/batch-extractions")
    def list_batches(document_id: str):
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM documents WHERE id=?",(document_id,)).fetchone():
                raise HTTPException(404,"Document not found")
            return [public(dict(row)) for row in db.execute("""SELECT * FROM batch_extractions
                WHERE document_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20""",
                (document_id,)).fetchall()]

    @app.get("/api/batch-extractions/{job_id}")
    def batch_status(job_id: str):
        with transaction(db_path) as db:
            return public(row_or_404(db,job_id))

    @app.post("/api/batch-extractions/{job_id}/stop")
    def stop_batch(job_id: str):
        with transaction(db_path) as db:
            row = row_or_404(db,job_id)
            if row["status"] == "running":
                db.execute("""UPDATE batch_extractions SET status='stopping',
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",(job_id,))
            return public(row_or_404(db,job_id))

    @app.post("/api/batch-extractions/{job_id}/resume")
    def resume_batch(job_id: str):
        with lock,transaction(db_path) as db:
            row = row_or_404(db,job_id)
            if row["status"] not in ("stopped","interrupted","failed"):
                raise HTTPException(409,"Only stopped, interrupted or failed jobs may resume")
            if db.execute("""SELECT 1 FROM batch_extractions WHERE document_id=? AND id<>?
                AND status IN ('running','stopping')""",
                (row["document_id"],job_id)).fetchone():
                raise HTTPException(409,"Another extraction is already running for this document")
            db.execute("""UPDATE batch_extractions SET status='running',work_stage='queued',
                last_error=NULL,in_flight_start=NULL,in_flight_end=NULL,
                in_flight_started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (job_id,))
            item = public(row_or_404(db,job_id))
        spawn(job_id)
        return item

    @app.get("/api/task-center")
    def task_center(limit: int = Query(default=80,ge=1,le=200)):
        with transaction(db_path) as db:
            rows=db.execute("""SELECT b.*,d.original_filename AS filename,c.title AS course_name
               FROM batch_extractions b
               JOIN documents d ON d.id=b.document_id
               JOIN courses c ON c.id=b.course_id
               ORDER BY CASE WHEN b.status IN ('running','stopping') THEN 0 ELSE 1 END,
                        b.updated_at DESC,b.rowid DESC LIMIT ?""",(limit,)).fetchall()
            notices=[dict(row) for row in db.execute("""SELECT n.*,d.original_filename AS filename
                  FROM task_notifications n JOIN documents d ON d.id=n.document_id
                  ORDER BY n.created_at DESC,n.rowid DESC LIMIT 80""").fetchall()]
            return {"tasks":[public(dict(row)) for row in rows],
                    "notifications":notices,
                    "unread_count":db.execute("""SELECT count(*) FROM task_notifications
                                        WHERE is_read=0""").fetchone()[0],
                    "concurrency_limit":2}

    @app.post("/api/task-center/notifications/read")
    def read_notifications():
        with transaction(db_path) as db:
            db.execute("UPDATE task_notifications SET is_read=1 WHERE is_read=0")
        return {"ok":True}

    def recover_after_restart():
        with transaction(db_path) as db:
            pending=[dict(row) for row in db.execute("""SELECT * FROM batch_extractions
                            WHERE status IN ('running','stopping')""").fetchall()]
            for row in pending:
                done(db,row,"interrupted")

    return recover_after_restart
