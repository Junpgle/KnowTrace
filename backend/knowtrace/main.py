"""KnowTrace API - M1: courses, chapters, knowledge points and source documents."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from typing import Literal

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import connect, initialize, transaction
from .ai import AIConfigError, AIServiceError, OllamaProvider, compact, grounded_items
from .mimo import MimoProvider
from .parser import ParseError, SUPPORTED_EXTENSIONS, extract_chunks
from .questions import validate_generated_questions, validate_question_fields
from .quiz_api import register_quiz_routes

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
LOW_TEXT_THRESHOLD = 80  # heuristic: flag for review, not proof of missing content


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    code: str | None = Field(default=None, max_length=50)


class ChapterCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    ordinal: int = 0
    parent_chapter_id: str | None = None


class ExtractKnowledgeRequest(BaseModel):
    page_start: int = Field(default=1, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    include_visual: bool = False
    provider: Literal['default', 'ollama', 'mimo'] = 'default'


class ReviewKnowledgeRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


class KnowledgePointUpdate(BaseModel):
    """Partial edit. An explicit null clears optional fields; omitted means unchanged."""
    model_config = ConfigDict(extra="forbid")

    canonical_name: str | None = Field(default=None, max_length=160)
    summary: str | None = Field(default=None, max_length=2000)
    difficulty: int | None = Field(default=None, ge=1, le=3)
    chapter_id: str | None = None

    @field_validator("canonical_name", "summary")
    @classmethod
    def validate_text(cls, value: str | None, info):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError(f"{info.field_name} cannot be blank; use null to clear optional fields")
        return value


class GenerateQuestionsRequest(BaseModel):
    provider: Literal['default', 'ollama', 'mimo'] = 'default'
    count: int = Field(default=2, ge=1, le=4)
    question_types: list[Literal["single_choice", "true_false"]] = Field(
        default_factory=lambda: ["single_choice", "true_false"], min_length=1, max_length=2)


class QuestionRevisionRequest(BaseModel):
    """Revision makes a new version; no published question is overwritten."""
    model_config = ConfigDict(extra="forbid")
    stem: str | None = None
    options: dict[str, str] | None = None
    correct_answer: str | bool | None = None
    explanation: str | None = None
    difficulty: int | None = None


class ReviewQuestionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


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
               storage_dir: str | Path | None = None,
               ai_provider: object | None = None) -> FastAPI:
    data_root = Path(os.environ.get("KNOWTRACE_DATA_DIR", "./data")).resolve()
    database = Path(db_path).resolve() if db_path else data_root / "knowtrace.sqlite3"
    storage = Path(storage_dir).resolve() if storage_dir else data_root / "uploads"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize(database)
        storage.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="KnowTrace API", version="0.6.0", lifespan=lifespan)
    configured_provider = os.getenv("KNOWTRACE_AI_PROVIDER", "ollama").strip().lower()
    if configured_provider not in ("ollama", "mimo"):
        raise AIConfigError("KNOWTRACE_AI_PROVIDER must be ollama or mimo")
    app.state.ai_provider = ai_provider if ai_provider is not None else (
        MimoProvider.from_env() if configured_provider == "mimo" else OllamaProvider.from_env())

    def resolve_provider(selected: str):
        if selected == "default":
            return app.state.ai_provider
        if selected == "mimo":
            return MimoProvider.from_env()
        if selected == "ollama":
            return OllamaProvider.from_env()
        raise HTTPException(422, "Unsupported AI provider")

    @app.get("/api/ai/providers")
    def ai_provider_status():
        local = OllamaProvider.from_env()
        mimo = MimoProvider.from_env()
        return {"default": configured_provider,
                "providers": [
                  {"id": "ollama", "label": "本地 Ollama", "model": local.text_model,
                   "available": bool(local.text_model), "vision_model": local.vision_model,
                   "function_calling": False},
                  {"id": "mimo", "label": "小米 MiMo API", "model": mimo.text_model,
                   "available": bool(mimo.api_key), "vision_model": local.vision_model,
                   "function_calling": True}
                ],
                "security": "API keys are configured on the backend only and never returned."}

    def ensure_course(db: sqlite3.Connection, course_id: str) -> None:
        if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'", (course_id,)).fetchone():
            raise HTTPException(404, "Course not found")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "knowtrace-api", "version": "0.6.0"}

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

    def knowledge_detail(db: sqlite3.Connection, knowledge_point_id: str) -> dict:
        point = db.execute("""SELECT id,course_id,chapter_id,canonical_name,summary,difficulty,
                             review_status,created_job_id,created_at,updated_at
                             FROM knowledge_points WHERE id=? AND archived_at IS NULL""",
                           (knowledge_point_id,)).fetchone()
        if point is None:
            raise HTTPException(404, "Knowledge point not found")
        sources = _rows(db.execute("""SELECT c.id AS chunk_id,c.document_id,d.original_filename,
                         c.page_start,c.page_end,c.content_kind,s.evidence_note
                         FROM knowledge_point_sources s
                         JOIN document_chunks c ON c.id=s.chunk_id
                         JOIN documents d ON d.id=c.document_id
                         WHERE s.knowledge_point_id=? ORDER BY d.created_at,c.page_start,c.chunk_index""",
                         (knowledge_point_id,)))
        return {**dict(point), "sources": sources}

    def audit_knowledge(db: sqlite3.Connection, knowledge_point_id: str, action: str,
                        before: dict, after: dict) -> None:
        db.execute("""INSERT INTO knowledge_point_events
                      (id,knowledge_point_id,action,before_json,after_json)
                      VALUES (?,?,?,?,?)""",
                   (_uuid(), knowledge_point_id, action,
                    json.dumps(before, ensure_ascii=False, sort_keys=True),
                    json.dumps(after, ensure_ascii=False, sort_keys=True)))

    @app.get("/api/knowledge-points/{knowledge_point_id}")
    def get_knowledge_point(knowledge_point_id: str):
        with transaction(database) as db:
            return knowledge_detail(db, knowledge_point_id)

    @app.get("/api/knowledge-points/{knowledge_point_id}/history")
    def knowledge_history(knowledge_point_id: str):
        with transaction(database) as db:
            knowledge_detail(db, knowledge_point_id)
            events = _rows(db.execute("""SELECT id,action,before_json,after_json,created_at
                        FROM knowledge_point_events WHERE knowledge_point_id=?
                        ORDER BY rowid""", (knowledge_point_id,)))
            for event in events:
                event["before"] = json.loads(event.pop("before_json"))
                event["after"] = json.loads(event.pop("after_json"))
            return events

    @app.patch("/api/knowledge-points/{knowledge_point_id}")
    def update_knowledge_point(knowledge_point_id: str, payload: KnowledgePointUpdate):
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, "Specify at least one field to update")
        if "canonical_name" in changes and changes["canonical_name"] is None:
            raise HTTPException(422, "canonical_name cannot be null")
        with transaction(database) as db:
            previous = knowledge_detail(db, knowledge_point_id)
            if "chapter_id" in changes and changes["chapter_id"] is not None:
                if not changes["chapter_id"].strip():
                    raise HTTPException(422, "chapter_id must not be blank")
                chapter = db.execute("SELECT course_id FROM chapters WHERE id=?",
                                     (changes["chapter_id"],)).fetchone()
                if chapter is None or chapter["course_id"] != previous["course_id"]:
                    raise HTTPException(422, "Chapter must belong to the same course")
            if "canonical_name" in changes:
                possible_duplicates = db.execute("""SELECT canonical_name FROM knowledge_points
                     WHERE course_id=? AND id<>? AND archived_at IS NULL""",
                     (previous["course_id"], knowledge_point_id)).fetchall()
                if any(compact(row["canonical_name"]) == compact(changes["canonical_name"])
                       for row in possible_duplicates):
                    raise HTTPException(409, "Knowledge point name already exists in this course")
            fields = ("canonical_name", "summary", "difficulty", "chapter_id")
            updated = {key: changes.get(key, previous[key]) for key in fields}
            if all(updated[key] == previous[key] for key in fields):
                return previous
            before = {key: previous[key] for key in (*fields, "review_status")}
            # Any material edit (including a previously approved point) must
            # be reviewed again. The original source evidence is immutable.
            db.execute("""UPDATE knowledge_points SET canonical_name=?,summary=?,difficulty=?,
                          chapter_id=?,review_status='pending',
                          updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                       (updated["canonical_name"],updated["summary"],updated["difficulty"],
                        updated["chapter_id"], knowledge_point_id))
            audit_knowledge(db, knowledge_point_id, "edited", before,
                            {**updated, "review_status": "pending"})
            return knowledge_detail(db, knowledge_point_id)

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

    @app.get("/api/documents/{document_id}/quality")
    def document_quality(document_id: str):
        """Flag low-text pages for review without pretending to read diagrams.

        Includes pages with no extracted chunks. This is a diagnostic, NOT an
        OCR confidence score; title pages and sparse slides can be false positives.
        """
        with transaction(database) as db:
            doc = db.execute("SELECT total_pages, original_filename FROM documents WHERE id=?", (document_id,)).fetchone()
            if doc is None:
                raise HTTPException(404, "Document not found")
            parts = db.execute("SELECT page_start,content_text FROM document_chunks WHERE document_id=? AND content_kind='text' ORDER BY chunk_index", (document_id,)).fetchall()

        # Chunks can overlap; for a low-text warning a modest overcount is safe.
        # We do not claim these character counts are exact after chunking.
        counts = [0] * int(doc["total_pages"])
        for part in parts:
            page = part["page_start"]
            if 1 <= page <= len(counts):
                counts[page - 1] += len(re.sub(r"\s+", "", part["content_text"]))
        pages = []
        for number, count in enumerate(counts, start=1):
            reason = "no_text" if count == 0 else "low_text" if count < LOW_TEXT_THRESHOLD else None
            pages.append({"page": number, "extracted_characters_approx": count,
                          "needs_review": reason is not None, "reason": reason})
        return {"document_id": document_id, "filename": doc["original_filename"],
                "total_pages": len(pages),
                "pages_needing_review": sum(p["needs_review"] for p in pages),
                "warning": "Heuristic only: low text does not necessarily mean missing image content.",
                "pages": pages}

    @app.get("/api/documents/{document_id}/pages/{page_number}/preview")
    def preview_pdf_page(document_id: str, page_number: int):
        """Render original PDF page so learners can inspect figures and equations.

        This endpoint previews a page; it does NOT extract the image's meaning.
        """
        with transaction(database) as db:
            doc = db.execute("SELECT storage_key,media_type,total_pages FROM documents WHERE id=?", (document_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "Document not found")
        if doc["media_type"] != "application/pdf":
            raise HTTPException(415, "Preview currently supports PDF only")
        if page_number < 1 or page_number > doc["total_pages"]:
            raise HTTPException(404, "Page not found")
        # The key was generated by the server, but still prevent path traversal.
        source = (storage / doc["storage_key"]).resolve()
        if source.parent != storage or not source.is_file():
            raise HTTPException(404, "Original file not found")
        try:
            with fitz.open(source) as pdf:
                page = pdf[page_number - 1]
                scale = min(1.5, 1600 / max(1.0, page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                return Response(pix.tobytes("png"), media_type="image/png",
                                headers={"Cache-Control": "private, max-age=300"})
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise HTTPException(422, "Unable to render PDF page") from exc

    @app.post("/api/documents/{document_id}/extract-knowledge")
    def extract_document_knowledge(document_id: str, payload: ExtractKnowledgeRequest):
        """Extract source-grounded drafts from up to four pages per request.

        Opt-in vision is limited to two sparse PDF pages per call. All generated
        knowledge remains pending review; no AI answer is silently approved.
        """
        provider = resolve_provider(payload.provider)
        page_end = payload.page_end or payload.page_start
        if page_end < payload.page_start or page_end - payload.page_start >= 4:
            raise HTTPException(422, "Select 1 to 4 consecutive pages")
        with transaction(database) as db:
            doc = db.execute("SELECT id,course_id,total_pages,media_type,storage_key FROM documents WHERE id=? AND status='ready'", (document_id,)).fetchone()
            if doc is None:
                raise HTTPException(404, "Ready document not found")
            if page_end > doc["total_pages"]:
                raise HTTPException(422, "Requested page exceeds document length")
            raw_chunks = _rows(db.execute("SELECT id,page_start,content_text,content_kind FROM document_chunks WHERE document_id=? AND content_kind='text' AND page_start BETWEEN ? AND ? ORDER BY chunk_index",
                                          (document_id, payload.page_start, page_end)))
        sources: dict[int, list[dict]] = {p: [] for p in range(payload.page_start, page_end + 1)}
        for part in raw_chunks:
            sources[part["page_start"]].append(part)
        # Model input and displayed provenance use one page at a time. Long
        # chunks may overlap, so no character count is claimed to be exact.
        page_texts: dict[int, str] = {}
        for page, chunks in sources.items():
            text = "\n".join(c["content_text"] for c in chunks)
            if len(compact(text)) >= LOW_TEXT_THRESHOLD:
                page_texts[page] = text[:6000]
        vision_pages: list[int] = []
        if payload.include_visual:
            if doc["media_type"] != "application/pdf":
                raise HTTPException(415, "Visual extraction currently supports PDF only")
            if not getattr(provider, "vision_model", None):
                raise HTTPException(503, "Configure KNOWTRACE_VISION_MODEL to enable visual extraction")
            sparse = [p for p in sources if p not in page_texts]
            if len(sparse) > 2:
                raise HTTPException(422, "At most two sparse pages per visual extraction request")
            filepath = (storage / doc["storage_key"]).resolve()
            if filepath.parent != storage or not filepath.is_file():
                raise HTTPException(404, "Original document file not found")
            try:
                with fitz.open(filepath) as pdf:
                    for page in sparse:
                        image = pdf[page - 1].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).tobytes("png")
                        desc = provider.describe_visual(base64.b64encode(image).decode("ascii"))
                        if desc and desc != "无法辨识":
                            page_texts[page] = desc
                            vision_pages.append(page)
            except (AIConfigError, AIServiceError) as exc:
                raise HTTPException(503, str(exc)) from exc
            except (fitz.FileDataError, RuntimeError, ValueError) as exc:
                raise HTTPException(422, "Unable to render document for visual extraction") from exc
        if not page_texts:
            raise HTTPException(422, "No sufficiently detailed text found; enable vision or select different pages")

        job_id = _uuid()
        model_name = getattr(provider, "text_model", "test-model")
        with transaction(database) as db:
            db.execute("INSERT INTO processing_jobs(id,course_id,document_id,job_type,engine_name,prompt_version,status) VALUES (?,?,?,'knowledge_extract',?,'kp_v1','running')",
                       (job_id, doc["course_id"], document_id, model_name))
        try:
            response = provider.extract(page_texts)
            extracted, warnings = grounded_items(response, page_texts)
            with transaction(database) as db:
                # Persist model-derived figure descriptions as separate evidence,
                # unmistakably marked as NOT verified text from the PDF.
                for page in vision_pages:
                    next_index = db.execute("SELECT COALESCE(MAX(chunk_index)+1,0) FROM document_chunks WHERE document_id=?", (document_id,)).fetchone()[0]
                    figure = {"id": _uuid(), "page_start": page, "content_text": page_texts[page], "content_kind": "figure_description"}
                    db.execute("INSERT INTO document_chunks(id,document_id,chunk_index,page_start,page_end,content_kind,content_text,content_sha256) VALUES (?,?,?,?,?,?,?,?)",
                               (figure["id"], document_id, next_index, page, page, figure["content_kind"], figure["content_text"], hashlib.sha256(figure["content_text"].encode()).hexdigest()))
                    sources[page].append(figure)
                existing = {compact(row["canonical_name"]): row["id"] for row in db.execute("SELECT id,canonical_name FROM knowledge_points WHERE course_id=? AND archived_at IS NULL", (doc["course_id"],))}
                created, reused = [], []
                for point in extracted:
                    # Never create an orphan knowledge point: verify the quoted
                    # evidence maps to one concrete stored chunk first.
                    matching = next((c for c in sources[point["page"]] if compact(point["quote"]) in compact(c["content_text"])), None)
                    if matching is None:
                        warnings.append(f"Evidence chunk missing for {point['name']}")
                        continue
                    key = compact(point["name"])
                    if key in existing:
                        kp_id = existing[key]
                        reused.append(kp_id)
                    else:
                        kp_id = _uuid()
                        db.execute("INSERT INTO knowledge_points(id,course_id,canonical_name,summary,difficulty,created_job_id,review_status) VALUES (?,?,?,?,?,?,'pending')",
                                   (kp_id, doc["course_id"], point["name"], point["summary"], point["difficulty"], job_id))
                        existing[key] = kp_id
                        created.append(kp_id)
                    # A single page may have many text chunks. Link the source
                    # that contains the exact quotation, not merely its page.
                    note = ("AI-derived visual description (unverified); " if matching["content_kind"] == "figure_description" else "Exact text quotation; ") + point["quote"]
                    db.execute("INSERT OR IGNORE INTO knowledge_point_sources(knowledge_point_id,chunk_id,evidence_note) VALUES (?,?,?)", (kp_id, matching["id"], note))
                db.execute("UPDATE processing_jobs SET status='succeeded',finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (job_id,))
            return {"job_id": job_id, "created": len(created), "reused": len(reused),
                    "created_ids": created, "review_status": "pending",
                    "vision_pages": vision_pages, "skipped_sparse_pages": [p for p in sources if p not in page_texts],
                    "warnings": warnings}
        except (AIConfigError, AIServiceError) as exc:
            with transaction(database) as db:
                db.execute("UPDATE processing_jobs SET status='failed',error_message=?,finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (str(exc)[:1000], job_id))
            raise HTTPException(503 if isinstance(exc, AIConfigError) else 502, str(exc)) from exc

    @app.get("/api/documents/{document_id}/knowledge-points")
    def list_document_knowledge(document_id: str):
        with transaction(database) as db:
            if not db.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
                raise HTTPException(404, "Document not found")
            return _rows(db.execute("""SELECT DISTINCT kp.id,kp.canonical_name,kp.summary,kp.difficulty,
                kp.chapter_id,kp.review_status,c.page_start AS page,c.content_kind AS source_kind,s.evidence_note
                FROM knowledge_points kp JOIN knowledge_point_sources s ON s.knowledge_point_id=kp.id
                JOIN document_chunks c ON c.id=s.chunk_id WHERE c.document_id=? ORDER BY c.page_start,kp.canonical_name""", (document_id,)))

    @app.post("/api/knowledge-points/{knowledge_point_id}/review")
    def review_knowledge(knowledge_point_id: str, payload: ReviewKnowledgeRequest):
        with transaction(database) as db:
            previous = knowledge_detail(db, knowledge_point_id)
            if previous["review_status"] == payload.decision:
                return {"id": knowledge_point_id, "review_status": payload.decision}
            if payload.decision == "approved" and previous["created_job_id"] and not previous["sources"]:
                raise HTTPException(422, "AI knowledge point requires a source before approval")
            db.execute("""UPDATE knowledge_points SET review_status=?,
                          updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                       (payload.decision, knowledge_point_id))
            audit_knowledge(db, knowledge_point_id, payload.decision,
                            {"review_status": previous["review_status"]},
                            {"review_status": payload.decision})
        return {"id": knowledge_point_id, "review_status": payload.decision}

    def question_version_detail(db: sqlite3.Connection, version_id: str) -> dict:
        row = db.execute("""SELECT v.*,q.course_id,q.origin FROM question_versions v
                             JOIN questions q ON q.id=v.question_id
                             WHERE v.id=? AND q.archived_at IS NULL""", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Question version not found")
        item = dict(row)
        item["options"] = json.loads(item.pop("options_json"))
        item["correct_answer"] = json.loads(item.pop("correct_answer_json"))
        item["knowledge_points"] = _rows(db.execute("""SELECT knowledge_point_id,is_primary
                                  FROM question_knowledge_points WHERE question_version_id=?
                                  ORDER BY is_primary DESC,knowledge_point_id""", (version_id,)))
        item["sources"] = _rows(db.execute("""SELECT c.id AS chunk_id,c.document_id,
                                  d.original_filename,c.page_start,c.content_kind,s.evidence_note
                                  FROM question_sources s
                                  JOIN document_chunks c ON c.id=s.chunk_id
                                  JOIN documents d ON d.id=c.document_id
                                  WHERE s.question_version_id=?
                                  ORDER BY c.page_start""", (version_id,)))
        return item

    @app.post("/api/knowledge-points/{knowledge_point_id}/generate-questions")
    def generate_questions(knowledge_point_id: str, payload: GenerateQuestionsRequest):
        """Generate pending questions only from approved concepts + their sources."""
        if len(set(payload.question_types)) != len(payload.question_types):
            raise HTTPException(422, "question_types must not contain duplicates")
        provider = resolve_provider(payload.provider)
        with transaction(database) as db:
            kp = db.execute("""SELECT id,course_id,canonical_name,summary,review_status
                    FROM knowledge_points WHERE id=? AND archived_at IS NULL""",
                    (knowledge_point_id,)).fetchone()
            if kp is None:
                raise HTTPException(404, "Knowledge point not found")
            if kp["review_status"] != "approved":
                raise HTTPException(409, "Approve the knowledge point before generating questions")
            source_cols = ("c.id,c.page_start,c.content_kind,d.original_filename"
                           if isinstance(provider, MimoProvider)
                           else "c.id,c.page_start,c.content_kind,c.content_text,d.original_filename")
            source_rows = _rows(db.execute("""SELECT """ + source_cols + """
                        FROM knowledge_point_sources s
                        JOIN document_chunks c ON c.id=s.chunk_id
                        JOIN documents d ON d.id=c.document_id
                        WHERE s.knowledge_point_id=? AND d.status='ready'
                        ORDER BY c.page_start,c.chunk_index LIMIT 4""", (knowledge_point_id,)))
            if not source_rows:
                raise HTTPException(422, "No source material linked to this knowledge point")
            existing_stems = {compact(row["stem"]) for row in db.execute("""SELECT v.stem FROM question_versions v
                                JOIN questions q ON q.id=v.question_id
                                WHERE q.course_id=? AND q.archived_at IS NULL""", (kp["course_id"],))}
        # MiMo initially receives only a tiny source catalog. It uses tool calls
        # to retrieve exact chunks as needed. Ollama keeps the legacy bounded
        # excerpts workflow. Only retrieved bytes are eligible evidence.
        sources = {row["id"]: {**row, "content_text": row["content_text"][:2200]}
                   for row in source_rows} if not isinstance(provider, MimoProvider) else {}
        job_id = _uuid()
        with transaction(database) as db:
            db.execute("""INSERT INTO processing_jobs
                       (id,course_id,document_id,job_type,engine_name,prompt_version,status)
                       VALUES (?,?,?,'question_generate',?,'questions_v1','running')""",
                       (job_id, kp["course_id"], None, getattr(provider,"text_model","local")))
        try:
            metadata = {"tool_calls": 0, "retrieved_chunks": 0, "usage": {}}
            if isinstance(provider, MimoProvider):
                result = provider.generate_questions_lazy(
                    db_path=database, knowledge_point_id=knowledge_point_id,
                    knowledge_name=kp["canonical_name"], knowledge_summary=kp["summary"] or "",
                    count=payload.count, question_types=payload.question_types)
                raw, sources = result["raw"], result["sources"]
                metadata = {key: result[key] for key in metadata}
            else:
                raw = provider.generate_questions(knowledge_name=kp["canonical_name"],
                    knowledge_summary=kp["summary"] or "", sources=list(sources.values()),
                    count=payload.count, question_types=payload.question_types)
            valid, warnings = validate_generated_questions(raw, sources,
                permitted_types=set(payload.question_types),count=payload.count,
                existing_stems=existing_stems)
            new_versions = []
            with transaction(database) as db:
                # Recheck the approval state after inference (which can be slow).
                current = db.execute("SELECT review_status FROM knowledge_points WHERE id=?", (knowledge_point_id,)).fetchone()
                if current is None or current["review_status"] != "approved":
                    raise HTTPException(409, "Knowledge point approval changed during generation; retry")
                for candidate in valid:
                    question_id, version_id = _uuid(), _uuid()
                    db.execute("INSERT INTO questions(id,course_id,origin) VALUES (?,?,'ai')",
                               (question_id, kp["course_id"]))
                    db.execute("""INSERT INTO question_versions(id,question_id,version_no,
                               question_type,stem,options_json,correct_answer_json,explanation,
                               difficulty,review_status,created_job_id)
                               VALUES (?,?,1,?,?,?,?,?,?,'pending',?)""",
                               (version_id,question_id,candidate["question_type"],candidate["stem"],
                                json.dumps(candidate["options"],ensure_ascii=False),
                                json.dumps(candidate["correct_answer"],ensure_ascii=False),
                                candidate["explanation"],candidate["difficulty"],job_id))
                    db.execute("""INSERT INTO question_knowledge_points
                               (question_version_id,knowledge_point_id,is_primary) VALUES (?,?,1)""",
                               (version_id,knowledge_point_id))
                    source_kind = sources[candidate["source_chunk_id"]]["content_kind"]
                    evidence = ("AI visual description (unverified): " if source_kind == "figure_description"
                                else "Verbatim source text: ") + candidate["quote"]
                    db.execute("""INSERT INTO question_sources
                               (question_version_id,chunk_id,evidence_note) VALUES (?,?,?)""",
                               (version_id,candidate["source_chunk_id"],evidence))
                    new_versions.append(version_id)
                db.execute("""UPDATE processing_jobs SET status='succeeded',
                           finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",(job_id,))
            return {"job_id":job_id,"created":len(new_versions),
                    "question_version_ids":new_versions,"review_status":"pending",
                    "provider": "mimo" if isinstance(provider, MimoProvider) else "ollama",
                    "model": getattr(provider, "text_model", "test-model"),
                    "tool_calls": metadata["tool_calls"],
                    "retrieved_chunks": metadata["retrieved_chunks"], "usage": metadata["usage"],
                    "warnings":warnings}
        except (AIConfigError, AIServiceError) as exc:
            with transaction(database) as db:
                db.execute("""UPDATE processing_jobs SET status='failed',error_message=?,
                           finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                           (str(exc)[:1000],job_id))
            raise HTTPException(503 if isinstance(exc,AIConfigError) else 502,str(exc)) from exc

    @app.get("/api/courses/{course_id}/questions")
    def list_course_questions(course_id: str, status: Literal["pending", "approved", "rejected", "draft"] | None = None):
        with transaction(database) as db:
            ensure_course(db, course_id)
            rows = _rows(db.execute("""SELECT v.id AS version_id,v.question_id,v.version_no,
                      v.question_type,v.stem,v.difficulty,v.review_status,
                      (SELECT k.knowledge_point_id FROM question_knowledge_points k
                       WHERE k.question_version_id=v.id AND k.is_primary=1) AS primary_knowledge_point_id
                      FROM question_versions v JOIN questions q ON q.id=v.question_id
                      WHERE q.course_id=? AND q.archived_at IS NULL
                      AND v.version_no=(SELECT MAX(x.version_no) FROM question_versions x WHERE x.question_id=q.id)
                      ORDER BY v.created_at,v.id""", (course_id,)))
            return [r for r in rows if status is None or r["review_status"] == status]

    @app.get("/api/question-versions/{version_id}")
    def get_question_version(version_id: str):
        with transaction(database) as db:
            return question_version_detail(db,version_id)

    @app.get("/api/questions/{question_id}/versions")
    def question_history(question_id: str):
        with transaction(database) as db:
            exists = db.execute("SELECT 1 FROM questions WHERE id=? AND archived_at IS NULL",(question_id,)).fetchone()
            if not exists:
                raise HTTPException(404,"Question not found")
            vids = db.execute("""SELECT id FROM question_versions WHERE question_id=?
                                  ORDER BY version_no""",(question_id,)).fetchall()
            return [question_version_detail(db,row["id"]) for row in vids]

    @app.post("/api/question-versions/{version_id}/revise",status_code=201)
    def revise_question(version_id: str,payload: QuestionRevisionRequest):
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(422,"Specify non-null fields to revise")
        with transaction(database) as db:
            old = question_version_detail(db,version_id)
            latest = db.execute("SELECT MAX(version_no) AS n FROM question_versions WHERE question_id=?",
                                (old["question_id"],)).fetchone()["n"]
            if old["version_no"] != latest:
                raise HTTPException(409,"Only the latest question version can be revised")
            fields = {key:changes.get(key,old[key]) for key in
                      ("stem","options","correct_answer","explanation","difficulty")}
            err = validate_question_fields(old["question_type"],fields["stem"],fields["options"],
                                           fields["correct_answer"],fields["explanation"],fields["difficulty"])
            if err:
                raise HTTPException(422,err)
            if all(fields[key] == old[key] for key in fields):
                raise HTTPException(422,"No content change specified")
            new_id = _uuid()
            db.execute("""INSERT INTO question_versions(id,question_id,version_no,question_type,
                       stem,options_json,correct_answer_json,explanation,difficulty,review_status)
                       VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
                       (new_id,old["question_id"],latest+1,old["question_type"],fields["stem"],
                        json.dumps(fields["options"],ensure_ascii=False),
                        json.dumps(fields["correct_answer"],ensure_ascii=False),
                        fields["explanation"],fields["difficulty"]))
            for link in old["knowledge_points"]:
                db.execute("""INSERT INTO question_knowledge_points
                           (question_version_id,knowledge_point_id,is_primary) VALUES (?,?,?)""",
                           (new_id,link["knowledge_point_id"],link["is_primary"]))
            for origin in old["sources"]:
                db.execute("INSERT INTO question_sources(question_version_id,chunk_id,evidence_note) VALUES (?,?,?)",
                           (new_id,origin["chunk_id"],origin["evidence_note"]))
            return question_version_detail(db,new_id)

    @app.post("/api/question-versions/{version_id}/review")
    def review_question(version_id: str,payload: ReviewQuestionRequest):
        with transaction(database) as db:
            version = question_version_detail(db,version_id)
            if version["review_status"] == payload.decision:
                return {"id":version_id,"review_status":payload.decision}
            if version["review_status"] not in ("pending","draft"):
                raise HTTPException(409,"Create a new version to change an already reviewed question")
            if payload.decision == "approved":
                latest = db.execute("SELECT MAX(version_no) FROM question_versions WHERE question_id=?", (version["question_id"],)).fetchone()[0]
                if version["version_no"] != latest:
                    raise HTTPException(409, "Only latest question version can be approved")
                if not version["sources"]:
                    raise HTTPException(422,"Question needs source evidence")
                primaries = [p for p in version["knowledge_points"] if p["is_primary"] == 1]
                if len(primaries) != 1:
                    raise HTTPException(422,"Exactly one primary knowledge point is required")
                current = db.execute("""SELECT review_status FROM knowledge_points
                                       WHERE id=? AND archived_at IS NULL""",
                                     (primaries[0]["knowledge_point_id"],)).fetchone()
                if current is None or current["review_status"] != "approved":
                    raise HTTPException(409,"Primary knowledge point must be approved")
                err = validate_question_fields(version["question_type"],version["stem"],
                                               version["options"],version["correct_answer"],
                                               version["explanation"],version["difficulty"])
                if err:
                    raise HTTPException(422,err)
            db.execute("""UPDATE question_versions SET review_status=?,
                        approved_at=CASE WHEN ?='approved'
                        THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END WHERE id=?""",
                       (payload.decision,payload.decision,version_id))
            return {"id":version_id,"review_status":payload.decision}

    register_quiz_routes(app, database)
    return app


app = create_app()
