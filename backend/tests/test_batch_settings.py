"""Regressions for local AI configuration and resumable whole-document extraction."""
from __future__ import annotations

import json
import os
import stat
import threading
import time

from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app


class SlowStub:
    text_model = "test-model"
    vision_model = ""
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
    def extract(self, pages):
        self.calls.append(list(pages))
        if len(self.calls) == 1:
            self.entered.set()
            assert self.release.wait(5), "Test timed out waiting to release extraction"
        page = min(pages)
        source = pages[page]
        quote = source[:55]
        return json.dumps({"items": [{
            "name": "第" + str(page) + "页的知识点",
            "summary": "这个知识点来自给定原始页面的课程内容",
            "page": page, "quote": quote, "difficulty": 2,
        }]},ensure_ascii=False)


def prepare(http, db_path, n=5):
    cid = http.post("/api/courses",json={"title":"Networks"}).json()["id"]
    with connect(db_path) as db:
        db.execute("""INSERT INTO documents
            (id,course_id,uploaded_by,original_filename,media_type,
             storage_key,sha256,total_pages,status)
            VALUES('doc1',?,'local','lecture.pdf','application/pdf',
                   'doc1.pdf','sha256text',?,'ready')""",(cid,n))
        for page in range(1,n+1):
            text = "Transmission protocols and network layers are documented in this lecture, "
            text += f"page {page}. " + "Data transfer relies on network communication. " * 3
            db.execute("""INSERT INTO document_chunks
                (id,document_id,chunk_index,page_start,page_end,content_kind,content_text)
                VALUES(?,'doc1',?,?,?,'text',?)""",
                (f"chunk{page}",page-1,page,page,text))
    return cid


def poll(http,job_id,statuses,timeout=5):
    end=time.monotonic()+timeout
    last=None
    while time.monotonic()<end:
        resp=http.get("/api/batch-extractions/"+job_id)
        assert resp.status_code==200,resp.text
        last=resp.json()
        if last["status"] in statuses:
            return last
        time.sleep(.025)
    raise AssertionError("Batch stayed in "+str(last))


def test_stop_after_inflight_chunk_then_resume_without_repeating_pages(tmp_path):
    db=tmp_path/"db.sqlite3"
    stub=SlowStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=stub)) as http:
        cid=prepare(http,db)
        start=http.post("/api/documents/doc1/batch-extractions",
                        json={"provider":"default","pages_per_batch":2,"include_visual":False})
        assert start.status_code==201,start.text
        job_id=start.json()["id"]
        assert stub.entered.wait(3)
        conflict=http.post("/api/documents/doc1/batch-extractions",json={"provider":"default"})
        assert conflict.status_code==409
        requested=http.post("/api/batch-extractions/"+job_id+"/stop")
        assert requested.status_code==200
        assert requested.json()["status"]=="stopping"
        stub.release.set()
        paused=poll(http,job_id,{"stopped"})
        assert paused["completed_pages"]==2
        assert paused["next_page"]==3
        assert paused["created_points"]==1
        assert [list(p) for p in stub.calls]==[[1,2]]
        resumed=http.post("/api/batch-extractions/"+job_id+"/resume")
        assert resumed.status_code==200
        complete=poll(http,job_id,{"succeeded"})
        assert complete["completed_pages"]==5
        assert complete["progress"]==100.0
        assert complete["created_points"]==3
        assert [list(p) for p in stub.calls]==[[1,2],[3,4],[5]]
        assert http.get(f"/api/courses/{cid}/knowledge-points").status_code==200
        assert len(http.get(f"/api/courses/{cid}/knowledge-points").json())==3
        assert http.post("/api/batch-extractions/"+job_id+"/resume").status_code==409


def test_restart_marks_orphan_running_job_interrupted_and_can_resume(tmp_path):
    db=tmp_path/"db.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        cid=prepare(http,db,n=1)
        with connect(db) as conn:
            conn.execute("""INSERT INTO batch_extractions
               (id,course_id,document_id,provider,pages_per_batch,total_pages,
               status) VALUES ('orphan',?,'doc1','default',1,1,'running')""",(cid,))
    ai=SlowStub();ai.release.set()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=ai)) as http:
        interrupted=http.get("/api/batch-extractions/orphan").json()
        assert interrupted["status"]=="interrupted"
        assert interrupted["next_page"]==1
        assert http.post("/api/batch-extractions/orphan/resume").status_code==200
        finished=poll(http,"orphan",{"succeeded"})
        assert finished["completed_pages"]==1


def test_settings_save_without_exposing_key_and_reload(tmp_path,monkeypatch):
    db=tmp_path/"db.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads"),
                    client=("127.0.0.1",50743)) as http:
        start=http.get("/api/settings/ai")
        assert start.status_code==200,start.text
        saved=http.patch("/api/settings/ai",json={
            "default_provider":"mimo",
            "mimo_model":"mimo-v2.6-pro",
            "mimo_api_key":"sk-secret-test-only-1234",
            "ollama_text_model":"qwen3.5:4b",
            "ollama_vision_model":"gemma4:12b",
            "ollama_url":"http://127.0.0.1:11434"})
        assert saved.status_code==200,saved.text
        assert saved.json()["mimo_key_configured"]
        assert "sk-secret" not in saved.text
        assert "sk-secret" not in http.get("/api/ai/providers").text
        assert http.get("/api/settings/ai").json()["default"]=="mimo"
        file=db.with_name("ai_settings.json")
        assert file.exists()
        if os.name=="posix":
            assert stat.S_IMODE(file.stat().st_mode)==0o600
        bad=http.patch("/api/settings/ai",json={"mimo_api_key":"tp-not-allowed"})
        assert bad.status_code==422
        bad_url=http.patch("/api/settings/ai",json={"ollama_url":"http://example.com:11434"})
        assert bad_url.status_code==422
        assert http.get("/api/settings/ai").json()["mimo_key_configured"] is True
        cleared=http.patch("/api/settings/ai",json={"clear_mimo_key":True})
        assert cleared.status_code==200
        assert not cleared.json()["mimo_key_configured"]
        assert "mimo_api_key" not in json.loads(file.read_text())


def test_batch_skips_image_only_pages_and_keeps_extracted_points(tmp_path):
    db=tmp_path/"db.sqlite3"
    stub=SlowStub()
    stub.release.set()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=stub)) as http:
        prepare(http,db,n=4)
        with connect(db) as connection:
            connection.execute("DELETE FROM document_chunks WHERE id='chunk2'")
        started=http.post("/api/documents/doc1/batch-extractions",
                          json={"provider":"default","pages_per_batch":1})
        assert started.status_code==201,started.text
        done=poll(http,started.json()["id"],{"succeeded"})
        assert done["completed_pages"]==4
        assert done["created_points"]==3
        assert done["skipped_pages"]==1
        assert [list(page) for page in stub.calls]==[[1],[3],[4]]


def test_settings_are_local_only(tmp_path):
    db=tmp_path/"db.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        # Starlette TestClient defaults to a synthetic remote hostname.
        assert http.patch("/api/settings/ai",json={"mimo_api_key":"sk-do-not-save-this"}).status_code==403
        assert not db.with_name("ai_settings.json").exists()
