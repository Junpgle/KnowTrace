"""Organize existing knowledge points by their cited chapter PDFs.

Only explicit document-to-chapter mappings are applied. This never changes
semantic relationships, source evidence, mastery, or review status.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import transaction


class DocumentChapterMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    chapter_id: str | None = None
    chapter_title: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def exactly_one_destination(self):
        if bool(self.chapter_id) == bool(self.chapter_title and self.chapter_title.strip()):
            raise ValueError("Specify exactly one existing chapter or new chapter title")
        if self.chapter_title is not None:
            self.chapter_title = self.chapter_title.strip()
        return self


class SourceChapterApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False
    mappings: list[DocumentChapterMapping] = Field(min_length=1, max_length=500)


def _normal(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def _read_course(db, course_id: str):
    if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                      (course_id,)).fetchone():
        raise HTTPException(404, "Course not found")
    chapters = [dict(row) for row in db.execute(
        "SELECT id,title,ordinal FROM chapters WHERE course_id=? ORDER BY ordinal,id",
        (course_id,))]
    all_documents = [dict(row) for row in db.execute(
        """SELECT id,original_filename,status FROM documents
           WHERE course_id=? ORDER BY created_at,id""", (course_id,))]
    pdfs = {doc["id"]: doc for doc in all_documents
            if Path(doc["original_filename"]).suffix.lower() == ".pdf"
            and doc["status"] != "archived"}
    points = {row["id"]: dict(row) for row in db.execute(
        """SELECT id,chapter_id,canonical_name,summary,difficulty,review_status
           FROM knowledge_points WHERE course_id=? AND archived_at IS NULL
           AND chapter_id IS NULL ORDER BY id""", (course_id,))}
    sources = defaultdict(set)
    # Include ALL documents cited by each point, even non-PDF/archived documents.
    # A partially mapped or contradictory point must not be silently assigned.
    for row in db.execute(
        """SELECT DISTINCT kp.id AS knowledge_point_id, c.document_id
           FROM knowledge_points kp
           JOIN knowledge_point_sources s ON s.knowledge_point_id=kp.id
           JOIN document_chunks c ON c.id=s.chunk_id
           WHERE kp.course_id=? AND kp.archived_at IS NULL
             AND kp.chapter_id IS NULL""", (course_id,)):
        sources[row["knowledge_point_id"]].add(row["document_id"])
    return chapters, pdfs, points, sources


def register_source_chapter_routes(app: FastAPI, database: Path) -> None:
    @app.get("/api/courses/{course_id}/source-chapters/preview")
    def preview_source_chapters(course_id: str):
        with transaction(database) as db:
            chapters, pdfs, points, sources = _read_course(db, course_id)

        existing = defaultdict(list)
        for chapter in chapters:
            existing[_normal(chapter["title"])].append(chapter["id"])

        count = Counter()
        exclusive = Counter()
        no_sources = 0
        multiple_sources = 0
        cannot_map = 0
        for pid in points:
            ids = sources.get(pid, set())
            if not ids:
                no_sources += 1
            if len(ids) > 1:
                multiple_sources += 1
            if ids and not ids.issubset(pdfs):
                cannot_map += 1
            for docid in ids & pdfs.keys():
                count[docid] += 1
                if len(ids) == 1:
                    exclusive[docid] += 1

        documents = []
        for docid, doc in pdfs.items():
            if not count[docid]:
                continue
            title = Path(doc["original_filename"]).stem.strip()[:160]
            matches = existing[_normal(title)]
            documents.append({
                "document_id": docid,
                "filename": doc["original_filename"],
                "suggested_title": title,
                "matching_chapter_id": matches[0] if len(matches) == 1 else None,
                "linked_unclassified": count[docid],
                "single_source_points": exclusive[docid],
            })
        return {
            "unclassified_points": len(points),
            "pdf_documents": len(pdfs),
            "documents": documents,
            "no_source_points": no_sources,
            "multi_document_points": multiple_sources,
            "points_with_other_documents": cannot_map,
            "single_pdf_points": sum(exclusive.values()),
            "note": "Preview only: no chapter or knowledge point is changed.",
        }

    @app.post("/api/courses/{course_id}/source-chapters/apply")
    def apply_source_chapters(course_id: str, payload: SourceChapterApply):
        if not payload.confirmed:
            raise HTTPException(422, "Explicit confirmation is required")
        with transaction(database) as db:
            chapters, pdfs, points, sources = _read_course(db, course_id)
            existing_ids = {chapter["id"] for chapter in chapters}
            by_title = defaultdict(list)
            for chapter in chapters:
                by_title[_normal(chapter["title"])].append(chapter["id"])

            targets = {}
            titles = {}
            for mapping in payload.mappings:
                did = mapping.document_id
                if did in targets:
                    raise HTTPException(422, "Duplicate document mapping")
                if did not in pdfs:
                    raise HTTPException(422, "Document is not an active PDF from this course")
                if not any(did in ids for ids in sources.values()):
                    raise HTTPException(422, "PDF has no unclassified knowledge points")
                if mapping.chapter_id is not None:
                    if mapping.chapter_id not in existing_ids:
                        raise HTTPException(422, "Target chapter must belong to this course")
                    targets[did] = ("existing", mapping.chapter_id)
                else:
                    title = (mapping.chapter_title or "").strip()
                    if not title or len(title) > 160:
                        raise HTTPException(422, "Invalid chapter name")
                    normalized = _normal(title)
                    matches = by_title[normalized]
                    if len(matches) > 1:
                        raise HTTPException(409, "Ambiguous existing chapter name; select its ID")
                    if matches:
                        targets[did] = ("existing", matches[0])
                    else:
                        targets[did] = ("new", normalized)
                        titles[normalized] = title

            planned = {}
            ambiguous = 0
            incomplete = 0
            for pid, point in points.items():
                ids = sources.get(pid, set())
                if not ids or not ids.issubset(targets):
                    incomplete += 1
                    continue
                chosen = {targets[did] for did in ids}
                if len(chosen) != 1:
                    ambiguous += 1
                    continue
                planned[pid] = next(iter(chosen))

            # Only create chapters that are needed for a point that can be assigned.
            required = {key[1] for key in planned.values() if key[0] == "new"}
            created = []
            created_by_title = {}
            ordinal = max((ch["ordinal"] for ch in chapters), default=0)
            for normalized in sorted(required):
                ordinal += 1
                cid = str(uuid4())
                db.execute(
                    """INSERT INTO chapters(id,course_id,title,ordinal)
                       VALUES (?,?,?,?)""",
                    (cid, course_id, titles[normalized], ordinal))
                created_by_title[normalized] = cid
                created.append({"id": cid, "title": titles[normalized]})

            assigned = 0
            for pid, target in planned.items():
                cid = (target[1] if target[0] == "existing"
                       else created_by_title[target[1]])
                old = points[pid]
                cursor = db.execute(
                    """UPDATE knowledge_points
                       SET chapter_id=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE id=? AND course_id=? AND chapter_id IS NULL
                         AND archived_at IS NULL""",
                    (cid, pid, course_id))
                if cursor.rowcount != 1:
                    raise HTTPException(409, "Knowledge point changed; preview again")
                before = {key: old[key] for key in
                          ("canonical_name", "summary", "difficulty",
                           "chapter_id", "review_status")}
                after = {**before, "chapter_id": cid,
                         "assignment_method": "confirmed_pdf_source",
                         "source_document_ids": sorted(sources[pid])}
                db.execute(
                    """INSERT INTO knowledge_point_events
                       (id,knowledge_point_id,action,before_json,after_json)
                       VALUES (?,?,'edited',?,?)""",
                    (str(uuid4()), pid,
                     json.dumps(before, ensure_ascii=False, sort_keys=True),
                     json.dumps(after, ensure_ascii=False, sort_keys=True)))
                assigned += 1

            return {
                "assigned_points": assigned,
                "created_chapters": created,
                "skipped_ambiguous": ambiguous,
                "skipped_unmapped_or_no_source": incomplete,
                "remaining_unclassified": len(points) - assigned,
                "preserved_review_status": True,
            }
