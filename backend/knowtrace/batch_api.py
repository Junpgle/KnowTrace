"""Durable whole-document knowledge extraction with cooperative stop and resume.

One worker per application process, one chunk (1–4 pages) per checkpoint. The
current model request cannot be forcibly terminated: stop takes effect *after*
the in-flight chunk, preserving committed evidence and avoiding half batches.
"""
from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import transaction


class StartBatch(BaseModel):
    provider: Literal["default", "ollama", "mimo"] = "default"
    pages_per_batch: int = Field(default=2, ge=1, le=4)
    include_visual: bool = False


def register_batch_routes(app: FastAPI, db_path: Path, extract_page_range: Callable):
    lock = threading.RLock()
    threads: dict[str, threading.Thread] = {}

    def row_or_404(db, job_id: str):
        row = db.execute("SELECT * FROM batch_extractions WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Batch job not found")
        return dict(row)

    def public(row: dict):
        # Explicitly expose one completed chunk boundary; no fake % during requests.
        return {**row, "progress": round(row["completed_pages"] * 100 / row["total_pages"], 1),
                "stop_note": "Stop applies after the current 1–4 page model request completes."}

    def run(job_id: str):
        try:
            while True:
                with transaction(db_path) as db:
                    row = row_or_404(db, job_id)
                    if row["status"] == "stopping":
                        db.execute("""UPDATE batch_extractions SET status='stopped',
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""", (job_id,))
                        return
                    if row["status"] != "running":
                        return
                    if row["next_page"] > row["total_pages"]:
                        db.execute("""UPDATE batch_extractions SET status='succeeded',
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""", (job_id,))
                        return
                start = row["next_page"]
                end = min(row["total_pages"], start + row["pages_per_batch"] - 1)
                try:
                    result = extract_page_range(row["document_id"], start, end,
                                                bool(row["include_visual"]), row["provider"])
                except HTTPException as exc:
                    # A completely image-only chunk without vision is a *skip*,
                    # not an unrecoverable failure for whole-document extraction.
                    if exc.status_code == 422 and exc.detail == "No sufficiently detailed text found; enable vision or select different pages":
                        result = {"created":0,"reused":0,
                                  "skipped_sparse_pages":list(range(start,end+1))}
                    else:
                        with transaction(db_path) as db:
                            db.execute("""UPDATE batch_extractions SET status='stopped',
                               last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                               (f"Pages {start}-{end}: {str(exc.detail)[:350]}", job_id))
                        return
                except Exception as exc:
                    # Model outage is recoverable from last *committed* batch.
                    # Do not log exception repr: hosted AI errors may contain
                    # private data from transport layers.
                    label = exc.detail if isinstance(exc, HTTPException) else type(exc).__name__
                    message = str(label)[:350]
                    with transaction(db_path) as db:
                        db.execute("""UPDATE batch_extractions SET status='stopped',
                           last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                           (f"Pages {start}-{end}: {message}", job_id))
                    return

                with transaction(db_path) as db:
                    current = row_or_404(db, job_id)
                    if current["next_page"] != start:
                        return
                    new_status = ("stopped" if current["status"] == "stopping"
                                  else "succeeded" if end >= row["total_pages"] else "running")
                    db.execute("""UPDATE batch_extractions SET next_page=?,
                       completed_pages=?, created_points=created_points+?,
                       reused_points=reused_points+?, skipped_pages=skipped_pages+?,
                       status=?,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                       (end + 1, end, result["created"], result["reused"],
                        len(result.get("skipped_sparse_pages", [])),
                        new_status, job_id))
                    if new_status != "running":
                        return
        finally:
            with lock:
                threads.pop(job_id, None)

    def spawn(job_id: str):
        # A single FastAPI worker in development. Never run uvicorn --workers > 1
        # with this in-process MVP; a worker queue is required for production.
        with lock:
            if job_id in threads:
                return
            thread = threading.Thread(target=run, args=(job_id,), daemon=True,
                                      name="knowtrace-batch-" + job_id[:8])
            threads[job_id] = thread
            thread.start()

    @app.post("/api/documents/{document_id}/batch-extractions", status_code=201)
    def start_batch(document_id: str, body: StartBatch):
        with lock, transaction(db_path) as db:
            doc = db.execute("""SELECT id,course_id,total_pages,media_type
                    FROM documents WHERE id=? AND status='ready'""",(document_id,)).fetchone()
            if not doc or not doc["total_pages"]:
                raise HTTPException(404, "Ready document not found")
            if body.include_visual and doc["media_type"] != "application/pdf":
                raise HTTPException(422, "Visual extraction supports PDF only")
            if body.include_visual and body.provider == "mimo":
                raise HTTPException(422, "Visual extraction requires local Ollama")
            existing = db.execute("""SELECT id FROM batch_extractions WHERE document_id=?
                    AND status IN ('running','stopping') LIMIT 1""",(document_id,)).fetchone()
            if existing:
                raise HTTPException(409, "Document already has an active extraction")
            job_id = str(uuid4())
            db.execute("""INSERT INTO batch_extractions
                (id,course_id,document_id,provider,pages_per_batch,include_visual,total_pages)
                VALUES(?,?,?,?,?,?,?)""",
                (job_id,doc["course_id"],document_id,body.provider,body.pages_per_batch,
                 int(body.include_visual),doc["total_pages"]))
            item = row_or_404(db,job_id)
        spawn(job_id)
        return public(item)

    @app.get("/api/documents/{document_id}/batch-extractions")
    def list_batches(document_id: str):
        with transaction(db_path) as db:
            if not db.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
                raise HTTPException(404, "Document not found")
            rows = db.execute("""SELECT * FROM batch_extractions
                WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT 20""",
                (document_id,)).fetchall()
            return [public(dict(r)) for r in rows]

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
        with lock, transaction(db_path) as db:
            row = row_or_404(db,job_id)
            if row["status"] not in ("stopped","interrupted"):
                raise HTTPException(409,"Only stopped or interrupted jobs may resume")
            running = db.execute("""SELECT 1 FROM batch_extractions WHERE document_id=?
                AND id<>? AND status IN ('running','stopping')""",
                (row["document_id"],job_id)).fetchone()
            if running:
                raise HTTPException(409,"Another extraction is already running")
            db.execute("""UPDATE batch_extractions SET status='running',
                        last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",(job_id,))
            item = row_or_404(db,job_id)
        spawn(job_id)
        return public(item)

    def recover_after_restart():
        with transaction(db_path) as db:
            db.execute("""UPDATE batch_extractions SET status='interrupted',
                updated_at=CURRENT_TIMESTAMP WHERE status IN ('running','stopping')""")

    return recover_after_restart
