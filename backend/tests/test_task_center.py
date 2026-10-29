"""Concurrent extraction dashboard, truthful checkpoints and in-app notices."""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from knowtrace.ai import AIServiceError
from knowtrace.db import connect, initialize
from knowtrace.main import create_app

TEXT = "Learning communication networking and reliability from real source material. " * 3


class ParallelStub:
    text_model = "fake-parallel"
    vision_model = ""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.two_started = threading.Event()
        self.release = threading.Event()
        self.release.clear()
        self.fail_enabled = True

    def extract(self, pages):
        source = next(iter(pages.values()))
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active >= 2:
                self.two_started.set()
        try:
            if not self.release.wait(8):
                raise AssertionError("Parallel test extraction timed out")
            if "FILE_FAIL" in source and self.fail_enabled:
                raise AIServiceError("Simulated model overload")
            page = min(pages)
            return json.dumps({"items": [{
                "name": "Network knowledge " + source.split("FILE_")[-1][:10],
                "summary": "Knowledge based on the original document text.",
                "quote": TEXT.strip()[:55],
                "page": page,
                "difficulty": 2,
            }]})
        finally:
            with self.lock:
                self.active -= 1


def seed_documents(http,db):
    course = http.post("/api/courses",json={"title":"Concurrent Network Tasks"}).json()["id"]
    with connect(db) as conn:
        for i,label in enumerate(["ONE","TWO","FAIL"],start=1):
            doc_id=f"doc{i}"
            conn.execute("""INSERT INTO documents
                 (id,course_id,uploaded_by,original_filename,media_type,storage_key,
                  sha256,total_pages,status) VALUES (?,?,'local',?,'application/pdf',
                  ?, ?,3,'ready')""",(doc_id,course,f"{label}.pdf",f"{doc_id}.pdf",doc_id))
            for page in range(1,4):
                conn.execute("""INSERT INTO document_chunks
                   (id,document_id,chunk_index,page_start,page_end,content_kind,content_text)
                   VALUES(?,?,?,?,?,'text',?)""",
                   (f"{doc_id}-page{page}",doc_id,page-1,page,page,TEXT+f"FILE_{label}"))
    return course


def wait(http,job_id,expected,seconds=8):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        res=http.get(f"/api/batch-extractions/{job_id}")
        assert res.status_code==200,res.text
        data=res.json()
        if data["status"] in expected:return data
        time.sleep(.03)
    raise AssertionError(f"Expected {expected}, last={data}")


def test_three_files_two_slots_success_and_failure_generate_distinct_notices(tmp_path):
    db=tmp_path/"batch.sqlite3"
    stub=ParallelStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed_documents(http,db)
        jobs=[]
        for doc in ["doc1","doc2","doc3"]:
            r=http.post(f"/api/documents/{doc}/batch-extractions",
                        json={"provider":"default","pages_per_batch":3})
            assert r.status_code==201,r.text
            jobs.append(r.json()["id"])
        assert stub.two_started.wait(4)
        center=http.get("/api/task-center").json()
        assert center["concurrency_limit"]==2
        assert len(center["tasks"])==3
        assert sum(j["work_stage"]=="extracting" for j in center["tasks"])==2
        assert sum(j["work_stage"]=="queued" for j in center["tasks"])==1
        assert all(j["progress"]==0 for j in center["tasks"])
        assert all(j["completed_pages"]==0 for j in center["tasks"])
        # The in-flight batch is not counted as completed.
        assert len([j for j in center["tasks"] if j["in_flight_start"] is not None])==2
        stub.release.set()
        by_doc={}
        for index,job_id in enumerate(jobs,1):
            info=wait(http,job_id,{"succeeded","failed"})
            by_doc[f"doc{index}"]=info
        assert stub.peak==2
        assert by_doc["doc1"]["status"]=="succeeded"
        assert by_doc["doc2"]["status"]=="succeeded"
        assert by_doc["doc3"]["status"]=="failed"
        assert by_doc["doc3"]["completed_pages"]==0
        assert by_doc["doc3"]["next_page"]==1
        center=http.get("/api/task-center").json()
        assert len(center["notifications"])==3
        assert center["unread_count"]==3
        assert {x["task_id"] for x in center["notifications"]}==set(jobs)
        assert {x["filename"] for x in center["notifications"]}=={"ONE.pdf","TWO.pdf","FAIL.pdf"}
        assert sorted(x["level"] for x in center["notifications"])==["error","success","success"]
        assert "Simulated model overload" in by_doc["doc3"]["last_error"]
        assert http.post("/api/task-center/notifications/read").json()["ok"] is True
        assert http.get("/api/task-center").json()["unread_count"]==0

        # A failed job resumes from the failed page and generates another
        # independent completion notice without erasing the failure record.
        stub.fail_enabled=False
        assert http.post(f"/api/batch-extractions/{jobs[2]}/resume").status_code==200
        recovered=wait(http,jobs[2],{"succeeded"})
        assert recovered["completed_pages"]==3
        updated=http.get("/api/task-center").json()
        assert len(updated["notifications"])==4
        assert updated["unread_count"]==1
        assert [x["level"] for x in updated["notifications"] if x["task_id"]==jobs[2]] == [
            "success","error"
        ]


def test_selected_pages_are_async_tasks_with_true_page_progress(tmp_path):
    db=tmp_path/"batch.sqlite3"
    stub=ParallelStub()
    stub.release.set()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=stub)) as http:
        cid=seed_documents(http,db)
        r=http.post("/api/documents/doc1/batch-extractions",json={
            "provider":"default","page_start":2,"page_end":3,"pages_per_batch":2})
        assert r.status_code==201,r.text
        initial=r.json()
        assert initial["mode"]=="range"
        assert initial["page_count"]==2
        assert initial["range_start"]==2 and initial["range_end"]==3
        done=wait(http,initial["id"],{"succeeded"})
        assert done["progress"]==100 and done["completed_pages"]==2
        assert done["next_page"]==4
        assert http.post("/api/documents/doc1/batch-extractions",
                         json={"page_start":1,"page_end":5}).status_code==422
        assert http.post("/api/documents/doc1/batch-extractions",
                         json={"page_start":3,"page_end":2}).status_code==422


def test_additive_migration_keeps_old_batch_rows(tmp_path):
    import sqlite3
    db=tmp_path/"old.sqlite3"
    # Minimal version of the previous batch table, with no progress columns.
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE batch_extractions (
          id TEXT PRIMARY KEY, course_id TEXT,document_id TEXT,
          provider TEXT,pages_per_batch INTEGER, include_visual INTEGER,
          next_page INTEGER,total_pages INTEGER,completed_pages INTEGER,
          created_points INTEGER,reused_points INTEGER,skipped_pages INTEGER,
          status TEXT,last_error TEXT,created_at TEXT,updated_at TEXT)""")
        con.execute("""INSERT INTO batch_extractions VALUES
           ('old','c','d','default',2,0,3,5,2,1,0,0,'stopped',NULL,'2026','2026')""")
    initialize(db)
    with connect(db) as con:
        row=con.execute("SELECT * FROM batch_extractions WHERE id='old'").fetchone()
        assert row["completed_pages"]==2 and row["next_page"]==3
        assert row["range_start"]==1 and row["range_end"] is None
        assert row["work_stage"]=="queued"
        assert con.execute("SELECT count(*) FROM task_notifications").fetchone()[0]==0
