"""Course-wide, review-aware question coverage and a paginated Q-matrix.

This is an audit of saved question-to-concept mappings, not a claim that
models have proven each concept is correctly examined.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from .db import transaction


def register_question_quality_routes(app: FastAPI, database: Path):
    def records(db, course_id: str):
        if not db.execute("SELECT 1 FROM courses WHERE id=? AND status='active'",
                          (course_id,)).fetchone():
            raise HTTPException(404, "Course not found")
        chapters = [dict(x) for x in db.execute(
            "SELECT id,title,ordinal FROM chapters WHERE course_id=? ORDER BY ordinal,id",
            (course_id,))]
        points = [dict(x) for x in db.execute("""SELECT id,chapter_id,canonical_name,
             review_status FROM knowledge_points WHERE course_id=? AND archived_at IS NULL
             ORDER BY canonical_name,id""",(course_id,))]
        questions = [dict(x) for x in db.execute("""SELECT v.id AS version_id,q.id AS question_id,
           v.review_status,v.question_type,v.difficulty,v.stem,
           (SELECT COUNT(*) FROM question_sources src WHERE src.question_version_id=v.id)
             AS source_count,
           (SELECT COUNT(*) FROM question_sources src JOIN document_chunks c
             ON c.id=src.chunk_id WHERE src.question_version_id=v.id
             AND c.content_kind='figure_description') AS visual_count
           FROM questions q JOIN question_versions v ON v.question_id=q.id
           WHERE q.course_id=? AND q.archived_at IS NULL
           AND v.version_no=(SELECT MAX(x.version_no) FROM question_versions x
                            WHERE x.question_id=q.id)
           ORDER BY v.created_at,v.id""",(course_id,))]
        mappings = defaultdict(list)
        for row in db.execute("""SELECT m.question_version_id,m.knowledge_point_id,
               m.is_primary FROM question_knowledge_points m
               JOIN question_versions v ON v.id=m.question_version_id
               JOIN questions q ON q.id=v.question_id WHERE q.course_id=?
               AND q.archived_at IS NULL
               AND v.version_no=(SELECT MAX(x.version_no) FROM question_versions x
                                WHERE x.question_id=q.id)
               ORDER BY m.is_primary DESC,m.knowledge_point_id""",(course_id,)):
            mappings[row["question_version_id"]].append({
                "knowledge_point_id":row["knowledge_point_id"],
                "is_primary":bool(row["is_primary"]),
            })
        for q in questions:
            q["knowledge_points"] = mappings[q["version_id"]]
            q["concept_count"] = len(q["knowledge_points"])
        return chapters,points,questions

    @app.get("/api/courses/{course_id}/question-quality")
    def question_quality(course_id: str, chapter_id: str | None = None):
        with transaction(database) as db:
            chapters,points,questions=records(db,course_id)
        if chapter_id and chapter_id not in {x["id"] for x in chapters}:
            raise HTTPException(422,"Chapter does not belong to course")
        scoped = [p for p in points if not chapter_id or p["chapter_id"]==chapter_id]
        point_ids = {p["id"] for p in scoped}
        mapping = defaultdict(lambda: {"approved":0,"pending":0,"primary":0,
                                       "diagnostic":0,"composite":0})
        counters = Counter()
        duplicates = Counter()
        for q in questions:
            if not any(x["knowledge_point_id"] in point_ids for x in q["knowledge_points"]):
                continue
            counters[q["review_status"]] += 1
            if q["concept_count"]>1: counters["composite"]+=1
            if q["concept_count"]==1 and q["question_type"] in ("single_choice","true_false"):
                counters["diagnostic"]+=1
                if q["review_status"]=="approved":
                    counters["approved_diagnostic"]+=1
            if q["source_count"]==0: counters["without_source"]+=1
            if q["visual_count"]: counters["visual_evidence"]+=1
            # A normalized duplicate is an editorial signal, not an automatic rejection.
            stem="".join(q["stem"].casefold().split())
            duplicates[stem]+=1
            for link in q["knowledge_points"]:
                id=link["knowledge_point_id"]
                if id not in point_ids:continue
                stats=mapping[id]
                status=q["review_status"]
                if status in ("approved","pending"): stats[status]+=1
                if status=="approved":
                    if link["is_primary"]:stats["primary"]+=1
                    if q["concept_count"]>1:
                        stats["composite"]+=1
                    elif q["question_type"] in ("single_choice","true_false"):
                        stats["diagnostic"]+=1
        approved=[p for p in scoped if p["review_status"]=="approved"]
        covered=sum(mapping[p["id"]]["approved"]>0 for p in approved)
        pending_only=sum(mapping[p["id"]]["approved"]==0 and mapping[p["id"]]["pending"]>0 for p in approved)
        untested=[{
            "id":p["id"],"name":p["canonical_name"],"chapter_id":p["chapter_id"],
            "pending":mapping[p["id"]]["pending"],
            "approved":mapping[p["id"]]["approved"],
            "diagnostic":mapping[p["id"]]["diagnostic"],
        } for p in approved if mapping[p["id"]]["approved"]==0]
        chapter_rows=[]
        for ch in [*chapters,{"id":None,"title":"未归类知识点"}]:
            subset=[p for p in approved if p["chapter_id"]==ch["id"]]
            if not subset:continue
            c=sum(mapping[p["id"]]["approved"]>0 for p in subset)
            d=sum(mapping[p["id"]]["diagnostic"]>0 for p in subset)
            chapter_rows.append({"chapter_id":ch["id"],"chapter":ch["title"],
                "approved_points":len(subset),"covered_points":c,"diagnosable_points":d,
                "coverage":round(c/len(subset),4)})
        return {"course_id":course_id,"chapter_id":chapter_id,
            "approved_points":len(approved),"covered_points":covered,
            "diagnosable_points":sum(mapping[p["id"]]["diagnostic"]>0 for p in approved),
            "pending_only_points":pending_only,"uncovered_points":len(approved)-covered,
            "coverage":round(covered/len(approved),4) if approved else 0,
            "latest_questions":{"total":sum(counters[k] for k in ("approved","pending","draft","rejected")),
                "approved":counters["approved"],"pending":counters["pending"],
                "draft":counters["draft"],"rejected":counters["rejected"],
                "diagnostic":counters["diagnostic"],
                "approved_diagnostic":counters["approved_diagnostic"],
                "composite":counters["composite"],
                "without_source":counters["without_source"],
                "visual_evidence":counters["visual_evidence"],
                "duplicate_stems":sum(v-1 for v in duplicates.values() if v>1)},
            "chapters":chapter_rows,
            "uncovered_sample":untested[:60],
            "disclaimer":"Coverage means a mapped latest approved question, not verified mastery or validated question quality."}

    @app.get("/api/courses/{course_id}/q-matrix")
    def q_matrix(course_id: str, chapter_id: str | None = None,
                 offset: int = Query(0,ge=0),
                 limit: int = Query(100,ge=1,le=300)):
        with transaction(database) as db:
            chapters, points, questions=records(db,course_id)
        if chapter_id and chapter_id not in {x["id"] for x in chapters}:
            raise HTTPException(422,"Chapter does not belong to course")
        ids={p["id"] for p in points if p["review_status"]=="approved"
             and (not chapter_id or p["chapter_id"]==chapter_id)}
        result=[{
            "question_version_id":q["version_id"],"question_type":q["question_type"],
            "difficulty":q["difficulty"],"concept_count":q["concept_count"],
            "knowledge_points":[link for link in q["knowledge_points"]
                                if link["knowledge_point_id"] in ids],
        } for q in questions if q["review_status"]=="approved" and
           any(x["knowledge_point_id"] in ids for x in q["knowledge_points"])]
        return {"total":len(result),"offset":offset,"limit":limit,
            "items":result[offset:offset+limit],
            "note":"Human-reviewed Q-matrix metadata. Mapping does not itself validate diagnostic accuracy."}
