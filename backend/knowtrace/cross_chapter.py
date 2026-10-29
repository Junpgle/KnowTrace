"""Bounded cross-chapter bridge discovery without a Cartesian LLM scan.

Candidate retrieval uses an inverted index of course topic terms. These scores
only choose which nodes meet in one model prompt; they NEVER create edges.
Previous completed bridge groups are excluded by *pair*, not by knowledge
point: a concept can connect multiple chapters in subsequent runs.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

from .db import transaction
from .grouped_relations import shingles


def bridge_groups(db_path: Path, course_id: str, examined_pairs: set[frozenset[str]],
                  limit: int, group_size: int) -> list[list[str]]:
    """Plan cross-chapter calls using source and keyword evidence.

    Only real chapter IDs count as chapters. Unassigned material is grouped
    by its actual originating document, not made into a fake chapter.
    """
    with transaction(db_path) as db:
        rows=db.execute("""SELECT p.id,p.chapter_id,p.canonical_name,p.summary,
                MIN(d.id) AS document_id,
                GROUP_CONCAT(DISTINCT s.chunk_id) AS chunks,
                ch.title AS chapter_title
            FROM knowledge_points p
            JOIN knowledge_point_sources s ON s.knowledge_point_id=p.id
            JOIN document_chunks c ON c.id=s.chunk_id
            JOIN documents d ON d.id=c.document_id
            LEFT JOIN chapters ch ON ch.id=p.chapter_id AND ch.course_id=p.course_id
            WHERE p.course_id=? AND p.review_status='approved'
              AND p.archived_at IS NULL AND d.course_id=? AND d.status='ready'
            GROUP BY p.id,p.chapter_id,p.canonical_name,p.summary
            ORDER BY p.created_at,p.id""",(course_id,course_id)).fetchall()

    nodes={}
    inverted: dict[str,list[str]]=defaultdict(list)
    for row in rows:
        point=dict(row)
        point["bucket"]=("chapter",point["chapter_id"]) if point["chapter_id"] else (
            "document",point["document_id"])
        point["terms"]=shingles(point["canonical_name"],point["summary"] or "")
        # Chapter headings are a hint, never a surrogate for concrete sources.
        point["chapter_terms"]=shingles(point["chapter_title"] or "","")
        point["chunks"]=set((point["chunks"] or "").split(","))
        nodes[point["id"]]=point
        for term in point["terms"]:
            inverted[term].append(point["id"])

    n=len(nodes)
    if n<2:return []
    document_frequency={term:len(ids) for term,ids in inverted.items()}
    # Highly generic bigrams do not justify submitting entire unrelated
    # chapters together. Rare 2-gram overlaps work for CJK and ASCII.
    max_frequency=max(8,n//5)
    candidates: dict[str,list[tuple[float,str]]]={}
    for source in nodes.values():
        scores=defaultdict(float)
        for term in source["terms"]:
            frequency=document_frequency[term]
            if frequency>max_frequency:continue
            weight=math.log1p(n/max(1,frequency))
            for target_id in inverted[term]:
                if target_id==source["id"]:continue
                target=nodes[target_id]
                if target["bucket"]==source["bucket"]:continue
                pair=frozenset((source["id"],target_id))
                if pair in examined_pairs:continue
                scores[target_id]+=weight
        ranked=[]
        for target_id,score in scores.items():
            target=nodes[target_id]
            shared_chunks=len(source["chunks"] & target["chunks"])
            # A cross-chapter shared source is stronger than a word overlap.
            score+=shared_chunks*4
            if source["chapter_terms"] & target["terms"]:
                score+=1.2
            if target["chapter_terms"] & source["terms"]:
                score+=1.2
            if score>0:
                ranked.append((score,target_id))
        candidates[source["id"]]=sorted(ranked,reverse=True)[:max(24,group_size*3)]

    groups=[]
    count=0
    used_anchors=set()
    # Only one anchor group per point in a single run. The same point remains
    # eligible as a new bridge anchor in later runs if untested pairs remain.
    for source in nodes.values():
        if count>=limit:break
        if source["id"] in used_anchors:continue
        partners=[]
        for score,target_id in candidates[source["id"]]:
            if target_id in used_anchors:continue
            if frozenset((source["id"],target_id)) in examined_pairs:continue
            partners.append(target_id)
            if len(partners)>=min(group_size-1,limit-count-1):break
        if not partners:continue
        group=[source["id"],*partners]
        groups.append(group)
        used_anchors.update(group)
        count+=len(group)
    return groups


def completed_bridge_pairs(db, course_id: str) -> set[frozenset[str]]:
    """Only completed bridge groups are recorded, including zero-edge groups."""
    import json
    groups=db.execute("""SELECT focus_ids_json,next_index FROM relation_batch_runs
                WHERE course_id=? AND mode='bridge'""",(course_id,)).fetchall()
    compared=set()
    for row in groups:
        for group in json.loads(row["focus_ids_json"])[:row["next_index"]]:
            if not isinstance(group,list):continue
            # Only cross-bucket pairs were considered, but group may include
            # nodes from several chapters. Avoid silently excluding untested
            # pairs from future runs when grouping logic evolves.
            for i,a in enumerate(group):
                for b in group[i+1:]:
                    compared.add(frozenset((a,b)))
    return compared
