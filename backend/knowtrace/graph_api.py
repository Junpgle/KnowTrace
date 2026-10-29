"""Read-only course knowledge graph for the 3D galaxy UI.

Three trustworthy edge classes:
* course / chapter membership derived from relational foreign keys;
* approved knowledge_relations, shown ONLY when both endpoints are included;
* a labeled virtual "unclassified" group if a course has unassigned points.

Never infer prerequisite edges merely from similar names, common sources, or
their proximity on the screen. BKT values are model estimates, not grades.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from .db import transaction


def register_graph_routes(app: FastAPI, database: Path) -> None:
    @app.get("/api/courses/{course_id}/knowledge-graph")
    def course_knowledge_graph(
        course_id: str,
        limit: int = Query(default=300, ge=1, le=600),
        include_pending: bool = True,
    ):
        with transaction(database) as db:
            course = db.execute(
                "SELECT id,title FROM courses WHERE id=? AND status='active'",
                (course_id,),
            ).fetchone()
            if not course:
                raise HTTPException(404, "Course not found")

            chapters = db.execute(
                """SELECT id,parent_chapter_id,title,ordinal
                   FROM chapters WHERE course_id=?
                   ORDER BY ordinal,id""",
                (course_id,),
            ).fetchall()
            chapter_ids = {row["id"] for row in chapters}

            filters = "" if include_pending else "AND kp.review_status='approved'"
            total = db.execute(
                """SELECT COUNT(*) FROM knowledge_points kp
                   WHERE kp.course_id=? AND kp.archived_at IS NULL """ + filters,
                (course_id,),
            ).fetchone()[0]
            rows = db.execute(
                """SELECT kp.id,kp.chapter_id,kp.canonical_name,kp.summary,kp.difficulty,
                          kp.review_status,ms.p_mastery,ms.observations,
                          (SELECT COUNT(*) FROM knowledge_point_sources s
                           WHERE s.knowledge_point_id=kp.id) AS source_count,
                          (SELECT COUNT(*) FROM question_knowledge_points qkp
                           JOIN question_versions v ON v.id=qkp.question_version_id
                           JOIN questions q ON q.id=v.question_id
                           WHERE qkp.knowledge_point_id=kp.id AND qkp.is_primary=1
                             AND q.archived_at IS NULL AND q.course_id=kp.course_id
                             AND v.review_status='approved'
                             AND v.version_no=(SELECT MAX(v2.version_no)
                                               FROM question_versions v2
                                               WHERE v2.question_id=q.id)
                          ) AS question_count
                   FROM knowledge_points kp
                   LEFT JOIN mastery_states ms
                     ON ms.knowledge_point_id=kp.id
                     AND ms.user_id='local' AND ms.model_key='bkt_v1'
                   WHERE kp.course_id=? AND kp.archived_at IS NULL """ + filters +
                """ ORDER BY kp.canonical_name,kp.id LIMIT ?""",
                (course_id, limit),
            ).fetchall()
            actual = {row["id"] for row in rows}

            # Read relation table only for the current course and approved edges.
            # Relations pointing to filtered-out or truncated points are omitted.
            relations = db.execute(
                """SELECT r.id,r.source_kp_id,r.target_kp_id,r.relation_type,r.rationale
                   FROM knowledge_relations r
                   JOIN knowledge_points src ON src.id=r.source_kp_id
                   JOIN knowledge_points dst ON dst.id=r.target_kp_id
                   WHERE src.course_id=? AND dst.course_id=?
                     AND src.archived_at IS NULL AND dst.archived_at IS NULL
                     AND src.review_status='approved' AND dst.review_status='approved'
                     AND r.review_status='approved'
                   ORDER BY r.created_at,r.id""",
                (course_id, course_id),
            ).fetchall()

        root_id = "course:" + course_id
        nodes = [{
            "id": root_id, "kind": "course", "name": course["title"],
            "review_status": "approved", "chapter_id": None, "mastery": None,
            "observations": 0, "source_count": 0, "question_count": 0,
            "summary": None, "difficulty": None, "virtual": False,
        }]
        edges = []
        for ch in chapters:
            node_id = "chapter:" + ch["id"]
            nodes.append({
                "id": node_id, "kind": "chapter", "name": ch["title"],
                "chapter_id": ch["id"], "review_status": "approved",
                "mastery": None, "observations": 0,
                "source_count": 0, "question_count": 0,
                "summary": None, "difficulty": None, "virtual": False,
            })
            parent_id = ("chapter:" + ch["parent_chapter_id"]
                         if ch["parent_chapter_id"] in chapter_ids else root_id)
            edges.append({
                "id": "membership:" + parent_id + ":" + node_id,
                "source": parent_id, "target": node_id,
                "type": "contains", "verified": True,
                "structural": True, "rationale": None,
            })

        orphan_id = "unclassified:" + course_id
        if any(row["chapter_id"] not in chapter_ids for row in rows):
            nodes.append({
                "id": orphan_id, "kind": "chapter", "name": "未归类知识点",
                "chapter_id": None, "review_status": "approved", "virtual": True,
                "mastery": None, "observations": 0,
                "source_count": 0, "question_count": 0,
                "summary": None, "difficulty": None,
            })
            edges.append({
                "id": "membership:" + root_id + ":" + orphan_id,
                "source": root_id, "target": orphan_id,
                "type": "contains", "verified": True,
                "structural": True, "rationale": None,
            })

        for row in rows:
            point = dict(row)
            point["kind"] = "knowledge"
            point["name"] = point.pop("canonical_name")
            point["mastery"] = (point.pop("p_mastery") if point["observations"] else None)
            point["observations"] = point["observations"] or 0
            point["virtual"] = False
            nodes.append(point)
            target = ("chapter:" + row["chapter_id"]
                      if row["chapter_id"] in chapter_ids else orphan_id)
            edges.append({
                "id": "membership:" + target + ":" + row["id"],
                "source": target, "target": row["id"],
                "type": "contains", "verified": True,
                "structural": True, "rationale": None,
            })

        semantic_edges = 0
        for relation in relations:
            if relation["source_kp_id"] not in actual or relation["target_kp_id"] not in actual:
                continue
            semantic_edges += 1
            edges.append({
                "id": relation["id"],
                "source": relation["source_kp_id"],
                "target": relation["target_kp_id"],
                "type": relation["relation_type"],
                "verified": True, "structural": False,
                "rationale": relation["rationale"],
            })

        return {
            "course_id": course_id,
            "course_name": course["title"],
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_knowledge_points": total,
                "shown_knowledge_points": len(rows),
                "approved_points": sum(p["review_status"] == "approved" for p in rows),
                "pending_points": sum(p["review_status"] == "pending" for p in rows),
                "mastery_estimated_points": sum(bool(p["observations"]) for p in rows),
                "approved_semantic_edges": semantic_edges,
                "truncated": len(rows) < total,
                "limit": limit,
            },
            "notice": "Only approved semantic relations are shown; missing relations are not inferred.",
        }
