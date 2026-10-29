"""Regressions for 1300+ node graphs and course-scale evidence-backed relations."""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app

QUOTE = "数据通信系统由源系统传输系统和目的系统三个主要部分构成。"
TEXT = QUOTE + "发送设备负责把信号变换成适合传输的形式。"


class RelationStub:
    text_model = "fake-relations"
    vision_model = ""

    def __init__(self):
        self.calls: list[str] = []
        self.entered=threading.Event()
        self.release=threading.Event()
        self.release.set()

    def generate_relations(self, system: str, context: str) -> str:
        data=json.loads(context)
        focus=data["focus_knowledge_point_id"]
        self.calls.append(focus)
        self.entered.set()
        assert self.release.wait(8), "Timed out waiting for mock model response"
        other=next(x["id"] for x in data["knowledge_points"] if x["id"]!=focus)
        source=data["sources"][0]
        return json.dumps({"items":[{
            "source_kp_id":focus,"target_kp_id":other,
            "relation_type":"related",
            "rationale":"这两个知识点同属给定课程来源中明确描述的通信概念。",
            "source_chunk_id":source["chunk_id"],"quote":QUOTE,
        }]},ensure_ascii=False)


def seed(http, db, count=5):
    course=http.post("/api/courses",json={"title":"Communication Networks"}).json()["id"]
    with connect(db) as conn:
        conn.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,
          media_type,storage_key,sha256,total_pages,status)
          VALUES('doc',?,'local','lecture.pdf','application/pdf',
                  'lecture.pdf','sha123',1,'ready')""",(course,))
        conn.execute("""INSERT INTO document_chunks
            (id,document_id,chunk_index,page_start,page_end,content_kind,content_text)
            VALUES('chunk','doc',0,1,1,'text',?)""",(TEXT,))
        for i in range(count):
            id=f"kp{i:04d}"
            conn.execute("""INSERT INTO knowledge_points
                (id,course_id,canonical_name,summary,review_status)
                VALUES(?,?,?,?, 'approved')""",(id,course,f"通信概念{i:04d}",TEXT))
            conn.execute("""INSERT INTO knowledge_point_sources
                (knowledge_point_id,chunk_id) VALUES(?,'chunk')""",(id,))
    return course


def wait(http,course,desired,timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        response=http.get(f"/api/courses/{course}/relation-batches")
        assert response.status_code==200,response.text
        state=response.json()[0]
        if state["status"] in desired:return state
        time.sleep(.03)
    raise AssertionError(f"Status did not reach {desired}, got {state}")


def test_relation_batch_stop_resume_no_duplicate_charges_and_bulk_approval(tmp_path):
    db=tmp_path/"large.sqlite3"
    stub=RelationStub()
    stub.release.clear()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=stub)) as http:
        course=seed(http,db)
        large=http.post(f"/api/courses/{course}/relation-batches",
                        json={"max_focus_points":31,"provider":"default"})
        assert large.status_code==422
        first=http.post(f"/api/courses/{course}/relation-batches",
                        json={"max_focus_points":3,"per_focus":2,"provider":"default"})
        assert first.status_code==201,first.text
        assert first.json()["total"]==3
        assert stub.entered.wait(3)
        conflict=http.post(f"/api/courses/{course}/relation-batches",
                           json={"max_focus_points":1,"provider":"default"})
        assert conflict.status_code==409
        stopped=http.post(f"/api/relation-batches/{first.json()['id']}/stop")
        assert stopped.status_code==200
        stub.release.set()
        paused=wait(http,course,{"stopped"})
        assert paused["completed"]==1
        assert paused["created_relations"]==1
        assert len(stub.calls)==1
        resumed=http.post(f"/api/relation-batches/{first.json()['id']}/resume")
        assert resumed.status_code==200,resumed.text
        complete=wait(http,course,{"succeeded"})
        assert complete["completed"]==3
        assert complete["created_relations"]==3
        assert len(stub.calls)==3
        preview=http.get(f"/api/courses/{course}/relations/bulk-review-preview").json()
        assert preview["total"]==3
        assert preview["types"][0]["relation_type"]=="related"
        assert preview["types"][0]["total"]==3
        denied=http.post(f"/api/courses/{course}/relations/bulk-approve",json={
            "confirmed":False,"types":["related"]})
        assert denied.status_code==422
        accept=http.post(f"/api/courses/{course}/relations/bulk-approve",json={
            "confirmed":True,"types":["related"]})
        assert accept.status_code==200,accept.text
        assert accept.json()["approved"]==3
        graph=http.get(f"/api/courses/{course}/knowledge-graph?limit=2000").json()
        assert graph["stats"]["approved_semantic_edges"]==3
        with connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM knowledge_relation_events").fetchone()[0]==3
        # New run processes remaining two only; no repeated model calls for
        # previously checkpointed focus points (regardless of relation result).
        nxt=http.post(f"/api/courses/{course}/relation-batches",json={
            "provider":"default","max_focus_points":5})
        assert nxt.status_code==201,nxt.text
        assert nxt.json()["total"]==2
        later=wait(http,course,{"succeeded"})
        assert later["total"]==2
        assert len(stub.calls)==5
        assert len(set(stub.calls))==5
        assert http.post(f"/api/courses/{course}/relation-batches",
                         json={"provider":"default","max_focus_points":5}).status_code==409


def test_all_1307_nodes_are_returned_when_requested(tmp_path):
    db=tmp_path/"graph.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        course=seed(http,db,count=1307)
        result=http.get(f"/api/courses/{course}/knowledge-graph?limit=2000")
        assert result.status_code==200,result.text[:1500]
        graph=result.json()
        assert graph["stats"]["total_knowledge_points"]==1307
        assert graph["stats"]["shown_knowledge_points"]==1307
        assert graph["stats"]["truncated"] is False
        assert len([n for n in graph["nodes"] if n["kind"]=="knowledge"])==1307
        assert len([e for e in graph["edges"] if e["structural"]])>=1307
        assert graph["stats"]["approved_semantic_edges"]==0


def test_batch_restart_preserves_checkpoint(tmp_path):
    db=tmp_path/"run.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        course=seed(http,db,count=4)
        with connect(db) as conn:
            conn.execute("""INSERT INTO relation_batch_runs
                (id,course_id,provider,focus_ids_json,per_focus,total,next_index,completed,status)
                VALUES('orphan',?,'default',?,2,4,2,2,'running')""",
                (course,json.dumps([f"kp{i:04d}" for i in range(4)])))
    stub=RelationStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=stub)) as http:
        job=http.get(f"/api/courses/{course}/relation-batches").json()[0]
        assert job["status"]=="interrupted"
        assert job["next_index"]==2
        assert http.post("/api/relation-batches/orphan/resume").status_code==200
        completed=wait(http,course,{"succeeded"})
        assert completed["completed"]==4
        assert stub.calls==["kp0002","kp0003"]


def test_required_relation_cycles_are_skipped_in_bulk_approval(tmp_path):
    db=tmp_path/"cycles.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        course=seed(http,db,count=3)
        with connect(db) as conn:
            for i,(a,b) in enumerate([("kp0000","kp0001"),("kp0001","kp0002"),("kp0002","kp0000")]):
                rid=f"relation{i}"
                conn.execute("""INSERT INTO knowledge_relations
                    (id,source_kp_id,target_kp_id,relation_type,rationale,review_status)
                    VALUES(?,?,?,'prerequisite_required','Verified premise','pending')""",
                    (rid,a,b))
                conn.execute("""INSERT INTO knowledge_relation_sources
                    (relation_id,chunk_id,evidence_note) VALUES(?,'chunk','real source')""",(rid,))
        result=http.post(f"/api/courses/{course}/relations/bulk-approve",
                         json={"confirmed":True,"types":["prerequisite_required"]})
        assert result.status_code==200,result.text
        assert result.json()["approved"]==2
        assert result.json()["cycles"]==1
        assert result.json()["skipped"]==1
        assert http.get(f"/api/courses/{course}/study-path").status_code==200


def test_image_described_relation_requires_explicit_opt_in(tmp_path):
    db=tmp_path/"vision.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        course=seed(http,db,count=2)
        with connect(db) as conn:
            conn.execute("UPDATE document_chunks SET content_kind='figure_description' WHERE id='chunk'")
            conn.execute("""INSERT INTO knowledge_relations
                (id,source_kp_id,target_kp_id,relation_type,rationale,review_status)
                VALUES('visual_relation','kp0000','kp0001','related','Chart based','pending')""")
            conn.execute("""INSERT INTO knowledge_relation_sources
                (relation_id,chunk_id,evidence_note)
                VALUES('visual_relation','chunk','AI vision description')""")
        first=http.post(f"/api/courses/{course}/relations/bulk-approve",json={
            "confirmed":True,"types":["related"]})
        assert first.status_code==200
        assert first.json()["approved"]==0 and first.json()["visual"]==1
        manual=http.post(f"/api/courses/{course}/relations/bulk-approve",json={
            "confirmed":True,"types":["related"],"include_visual_sources":True})
        assert manual.status_code==200,manual.text
        assert manual.json()["approved"]==1
        assert http.get(f"/api/courses/{course}/knowledge-graph").json()["stats"]["approved_semantic_edges"]==1
