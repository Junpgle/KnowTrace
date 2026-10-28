"""KnowTrace API - M1: courses, chapters, knowledge points and source documents."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .db import connect, initialize, transaction
from .parser import ParseError, SUPPORTED_EXTENSIONS, extract_chunks

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    code: str | None = Field(default=None, max_length=50)


class ChapterCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    ordinal: int = 0
    parent_chapter_id: str | None = None


class KnowledgePointCreate(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=160)
    chapter_id: str | None = None
    summary: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=3)


def _uuid() -> str:
    return str(uuid4())


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def create_app(*, db_path: str | Path | None = None,
               storage_dir: str | Path | None = None) -> FastAPI:
    data_root = Path(os.environ.get("KNOWTRACE_DATA_DIR", "./data")).resolve()
    database = Path(db_path).resolve() if db_path else data_root / "knowtrace.sqlite3"
    storage = Path(storage_dir).resolve() if storage_dir else data_root / "uploads"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize(database)
        storage.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="KnowTrace API", version="0.1.0", lifespan=lifespan)

    def ensure_course(db: sqlite3.Connection, course_id: str) -> None:
        if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'", (course_id,)).fetchone():
            raise HTTPException(404, "Course not found")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "knowtrace-api", "version": "0.1.0"}

    @app.get("/api/courses")
    def list_courses():
        with transaction(database) as db:
            return _rows(db.execute("SELECT id, title, description, code, created_at FROM courses WHERE status='active' ORDER BY created_at DESC"))

    @app.post("/api/courses", status_code=201)
    def add_course(payload: CourseCreate):
        cid = _uuid()
        # A single local user for M1. Schema keeps user IDs for future accounts.
        with transaction(database) as db:
            db.execute("INSERT OR IGNORE INTO users (id,username,display_name) VALUES (?,?,?)",
                       ("local", "local", "Local learner"))
            try:
                db.execute("INSERT INTO courses (id,owner_user_id,code,title,description) VALUES (?,?,?,?,?)",
                           (cid, "local", payload.code, payload.title, payload.description))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Course code already exists") from exc
        return {"id": cid, **payload.model_dump()}

    @app.get("/api/courses/{course_id}")
    def get_course(course_id: str):
        with transaction(database) as db:
            row = db.execute("SELECT id, title, description, code, created_at FROM courses WHERE id=? AND status='active'", (course_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Course not found")
            return dict(row)

    @app.post("/api/courses/{course_id}/chapters", status_code=201)
    def add_chapter(course_id: str, payload: ChapterCreate):
        new_id = _uuid()
        with transaction(database) as db:
            ensure_course(db, course_id)
            if payload.parent_chapter_id:
                parent = db.execute("SELECT course_id FROM chapters WHERE id=?", (payload.parent_chapter_id,)).fetchone()
                if not parent or parent["course_id"] != course_id:
                    raise HTTPException(422, "Parent chapter must belong to the same course")
            db.execute("INSERT INTO chapters (id,course_id,parent_chapter_id,title,ordinal) VALUES (?,?,?,?,?)",
                       (new_id, course_id, payload.parent_chapter_id, payload.title, payload.ordinal))
        return {"id": new_id, "course_id": course_id, **payload.model_dump()}

    @app.get("/api/courses/{course_id}/chapters")
    def list_chapters(course_id: str):
        with transaction(database) as db:
            ensure_course(db, course_id)
            return _rows(db.execute("SELECT id,course_id,parent_chapter_id,title,ordinal FROM chapters WHERE course_id=? ORDER BY ordinal,id", (course_id,)))

    @app.post("/api/courses/{course_id}/knowledge-points", status_code=201)
    def add_knowledge_point(course_id: str, payload: KnowledgePointCreate):
        kid = _uuid()
        with transaction(database) as db:
            ensure_course(db, course_id)
            if payload.chapter_id:
                chapter = db.execute("SELECT course_id FROM chapters WHERE id=?", (payload.chapter_id,)).fetchone()
                if not chapter or chapter["course_id"] != course_id:
                    raise HTTPException(422, "Chapter must belong to the same course")
            db.execute("INSERT INTO knowledge_points (id,course_id,chapter_id,canonical_name,summary,difficulty,review_status) VALUES (?,?,?,?,?,?,'approved')",
                       (kid, course_id, payload.chapter_id, payload.canonical_name, payload.summary, payload.difficulty))
        return {"id": kid, "course_id": course_id, **payload.model_dump(), "review_status": "approved"}

    @app.get("/api/courses/{course_id}/knowledge-points")
    def list_knowledge_points(course_id: str):
        with transaction(database) as db:
            ensure_course(db, course_id)
            return _rows(db.execute("SELECT id,course_id,chapter_id,canonical_name,summary,difficulty,review_status FROM knowledge_points WHERE course_id=? AND archived_at IS NULL ORDER BY created_at,id", (course_id,)))

    @app.post("/api/courses/{course_id}/documents", status_code=201)
    async def upload_document(course_id: str, file: UploadFile = File(...)):
        original = Path(file.filename or "").name
        suffix = Path(original).suffix.lower()
        if not original or suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(415, "Only PDF and PPTX are supported")
        # Read one extra byte and reject oversized uploads without storing them.
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File exceeds 30 MiB")
        try:
            page_count, chunks = extract_chunks(original, raw)
        except ParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        with transaction(database) as db:
            ensure_course(db, course_id)
        did = _uuid()
        stored_name = f"{did}{suffix}"
        target = storage / stored_name
        storage.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        try:
            with transaction(database) as db:
                db.execute("INSERT INTO documents (id,course_id,uploaded_by,original_filename,media_type,storage_key,sha256,total_pages,status) VALUES (?,?,?,?,?,?,?,?,?)",
                           (did, course_id, "local", original, "application/pdf" if suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.presentationml.presentation", stored_name, hashlib.sha256(raw).hexdigest(), page_count, "ready"))
                for index, chunk in enumerate(chunks):
                    db.execute("INSERT INTO document_chunks (id,document_id,chunk_index,page_start,page_end,content_kind,content_text,content_sha256) VALUES (?,?,?,?,?,?,?,?)",
                               (_uuid(), did, index, chunk["page_start"], chunk["page_end"], chunk["content_kind"], chunk["content_text"], hashlib.sha256(chunk["content_text"].encode("utf-8")).hexdigest()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"id": did, "course_id": course_id, "filename": original,
                "total_pages": page_count, "chunks": len(chunks), "status": "ready"}

    @app.get("/api/courses/{course_id}/documents")
    def list_documents(course_id: str):
        with transaction(database) as db:
            ensure_course(db, course_id)
            return _rows(db.execute("SELECT id,original_filename,total_pages,status,created_at FROM documents WHERE course_id=? ORDER BY created_at DESC", (course_id,)))

    @app.get("/api/documents/{document_id}/chunks")
    def list_chunks(document_id: str, limit: int = 50, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422, "Invalid pagination")
        with transaction(database) as db:
            if not db.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
                raise HTTPException(404, "Document not found")
            return _rows(db.execute("SELECT id,chunk_index,page_start,page_end,content_kind,content_text FROM document_chunks WHERE document_id=? ORDER BY chunk_index LIMIT ? OFFSET ?", (document_id, limit, offset)))

    return app


app = create_app()
