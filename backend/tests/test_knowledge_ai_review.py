"""Source-grounded AI review recommendations, safe acceptance and batch recovery."""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app

QUOTE = "A communication network has a source system, a transmission system and a destination system."
TEXT = QUOTE + " It provides the fundamental structure for digital communication and reliable message delivery."


class ReviewStub:
    text_model = "mock-review"
    vision_model = ""
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def review_knowledge(self, system, context):
        source = json.loads(context)["sources"][0]
        self.calls.append(source["chunk_id"])
        self.started.set()
        assert self.release.wait(6), "Timed out waiting to release mock AI review"
        return json.dumps({
            "recommendation":"approve","confidence":"high",
            "rationale":"知识点名称与摘要在所引用的原始课件中均可找到直接且连续的文字证据。",
            "issues":[],
            "evidence_chunk_id":source["chunk_id"],
            "evidence_quote":QUOTE,
        },ensure_ascii=False)


def seed(http,db, count=3):
    cid=http.post("/api/courses",json={"title":"Networks"}).json()["id"]
    with connect(db) as conn:
        conn.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,
             media_type,storage_key,sha256,total_pages,status)
             VALUES ('doc1',?,'local','lecture.pdf','application/pdf','doc.pdf','hash',3,'ready')""",(cid,))
        for i in range(1,count+1):
            conn.execute("""INSERT INTO document_chunks
                (id,document_id,chunk_index,page_start,page_end,content_kind,content_text)
                VALUES(?,'doc1',?,?,?,'text',?)""",
                (f"chunk{i}",i-1,i,i,TEXT))
            conn.execute("""INSERT INTO knowledge_points
                (id,course_id,canonical_name,summary,difficulty,review_status,created_job_id)
                VALUES (?,?,?, ?,2,'pending',NULL)""",
                (f"kp{i}",cid,f"Knowledge {i}","Source, transmission and destination systems.",))
            conn.execute("""INSERT INTO knowledge_point_sources
                (knowledge_point_id,chunk_id,evidence_note) VALUES(?,?,?)""",
                (f"kp{i}",f"chunk{i}","Exact text quotation; "+QUOTE))
    return cid


def await_status(http,bid,desired,timeout=5):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        item=http.get(f"/api/courses/{bid['course_id']}/ai-review-batches").json()[0]
        if item["status"] in desired:return item
        time.sleep(.03)
    raise AssertionError(f"Batch never reached {desired}: {item}")


def test_one_point_verified_review_and_explicit_approval(tmp_path):
    db=tmp_path/"review.sqlite3"
    stub=ReviewStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed(http,db,2)
        review=http.post("/api/knowledge-points/kp1/ai-review",json={"provider":"default"})
        assert review.status_code==200,review.text
        item=review.json()
        assert item["recommendation"]=="approve"
        assert item["verified_text"] is True
        assert item["stale"] is False
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="pending"
        denied=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                          json={"confirmed":False,"review_ids":[item["id"]]})
        assert denied.status_code==422
        accepted=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                            json={"confirmed":True,"review_ids":[item["id"]]})
        assert accepted.status_code==200,accepted.text
        assert accepted.json()["applied"]==["kp1"]
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="approved"
        history=http.get("/api/knowledge-points/kp1/history").json()
        assert history[-1]["after"]["ai_review_id"]==item["id"]
        again=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                        json={"confirmed":True,"review_ids":[item["id"]]})
        assert again.json()["applied"]==[]
        assert len(http.get("/api/courses/"+cid+"/ai-knowledge-reviews").json())==1


def test_revision_stales_prior_ai_review_and_blocks_approval(tmp_path):
    db=tmp_path/"review.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ReviewStub())) as http:
        cid=seed(http,db,1)
        suggestion=http.post("/api/knowledge-points/kp1/ai-review",
                             json={"provider":"default"}).json()
        changed=http.patch("/api/knowledge-points/kp1",
                           json={"summary":"Rewritten explanation requiring new source evidence."})
        assert changed.status_code==200,changed.text
        review=http.get("/api/courses/"+cid+"/ai-knowledge-reviews").json()[0]
        assert review["stale"] is True
        result=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                         json={"confirmed":True,"review_ids":[suggestion["id"]]})
        assert not result.json()["applied"] and result.json()["skipped"]
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="pending"


def test_visual_only_source_never_bulk_approved(tmp_path):
    db=tmp_path/"review.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ReviewStub())) as http:
        cid=seed(http,db,1)
        with connect(db) as conn:
            conn.execute("""UPDATE document_chunks SET content_kind='figure_description'
                WHERE id='chunk1'""")
        response=http.post("/api/knowledge-points/kp1/ai-review",
                           json={"provider":"default"})
        assert response.status_code==200,response.text
        review=response.json()
        assert review["recommendation"]=="revise"
        assert not review["verified_text"]
        result=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                         json={"confirmed":True,"review_ids":[review["id"]]})
        assert result.json()["applied"]==[]
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="pending"


def test_batch_stop_resume_and_no_double_processing(tmp_path):
    db=tmp_path/"review.sqlite3"
    stub=ReviewStub()
    stub.release.clear()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed(http,db,3)
        resp=http.post(f"/api/courses/{cid}/ai-review-batches",json={"provider":"default","limit":3})
        assert resp.status_code==201,resp.text
        bid=resp.json()
        assert stub.started.wait(3)
        assert http.post(f"/api/courses/{cid}/ai-review-batches",
                         json={"provider":"default"}).status_code==409
        stopped=http.post(f"/api/ai-review-batches/{bid['id']}/stop")
        assert stopped.json()["status"]=="stopping"
        stub.release.set()
        paused=await_status(http,bid,{"stopped"})
        assert paused["completed"]==1
        assert len(stub.calls)==1
        assert http.post(f"/api/ai-review-batches/{bid['id']}/resume").status_code==200
        complete=await_status(http,bid,{"succeeded"})
        assert complete["completed"]==3
        assert len(stub.calls)==3
        assert len(http.get(f"/api/courses/{cid}/ai-knowledge-reviews").json())==3
        assert http.post(f"/api/ai-review-batches/{bid['id']}/resume").status_code==409
        assert http.post(f"/api/courses/{cid}/ai-review-batches",
                         json={"provider":"default"}).status_code==409


def test_no_evidence_is_flagged_without_model_call(tmp_path):
    db=tmp_path/"review.sqlite3"
    stub=ReviewStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed(http,db,1)
        with connect(db) as conn:
            conn.execute("DELETE FROM knowledge_point_sources WHERE knowledge_point_id='kp1'")
        response=http.post("/api/knowledge-points/kp1/ai-review",json={"provider":"default"})
        assert response.status_code==200
        assert response.json()["recommendation"]=="revise"
        assert not stub.calls


def test_changed_knowledge_is_eligible_for_new_batch_and_fenced_json(tmp_path):
    db=tmp_path/"review.sqlite3"
    stub=ReviewStub()
    original=stub.review_knowledge
    stub.review_knowledge=lambda system,context: "```json\n" + original(system,context) + "\n```"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed(http,db,1)
        first=http.post("/api/knowledge-points/kp1/ai-review",json={"provider":"default"})
        assert first.status_code==200,first.text
        assert http.post(f"/api/courses/{cid}/ai-review-batches",
                         json={"provider":"default"}).status_code==409
        changed=http.patch("/api/knowledge-points/kp1",
                           json={"summary":"A revised summary that requires a new review."})
        assert changed.status_code==200
        started=http.post(f"/api/courses/{cid}/ai-review-batches",
                          json={"provider":"default"})
        assert started.status_code==201,started.text
        finished=await_status(http,started.json(),{"succeeded"})
        assert finished["completed"]==1
        updated=http.get(f"/api/courses/{cid}/ai-knowledge-reviews").json()
        assert len(updated)==1
        assert updated[0]["id"]!=first.json()["id"]
        assert updated[0]["stale"] is False


def test_approval_checks_new_duplicates_after_ai_review(tmp_path):
    db=tmp_path/"review.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ReviewStub())) as http:
        cid=seed(http,db,1)
        first=http.post("/api/knowledge-points/kp1/ai-review",json={"provider":"default"})
        assert first.status_code==200,first.text
        with connect(db) as conn:
            conn.execute("""INSERT INTO knowledge_points
              (id,course_id,canonical_name,review_status)
              VALUES('duplicate',?,'Knowledge 1','approved')""",(cid,))
        result=http.post(f"/api/courses/{cid}/ai-knowledge-reviews/apply",
                         json={"confirmed":True,"review_ids":[first.json()["id"]]})
        assert result.status_code==200,result.text
        assert result.json()["applied"]==[]
        assert result.json()["skipped"][0]["reason"]=="New duplicate detected since AI review"
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="pending"
