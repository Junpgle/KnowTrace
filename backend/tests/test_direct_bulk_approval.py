"""Direct, expressly confirmed no-second-AI bulk approval and safeguards."""
from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app


def setup(http,db):
    cid=http.post("/api/courses",json={"title":"Algorithms"}).json()["id"]
    other=http.post("/api/courses",json={"title":"Networking"}).json()["id"]
    with connect(db) as conn:
        conn.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,
             media_type,storage_key,sha256,total_pages,status)
             VALUES('doc',?,'local','lecture.pdf','application/pdf',
                    'doc.pdf','digest',1,'ready')""",(cid,))
        conn.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,
             page_start,page_end,content_kind,content_text)
             VALUES ('text','doc',0,1,1,'text','Sorting algorithms and time complexity')""")
        conn.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,
             page_start,page_end,content_kind,content_text)
             VALUES ('visual','doc',1,1,1,'figure_description','A visual graph of sorting')""")
        conn.execute("""INSERT INTO processing_jobs(id,course_id,document_id,job_type,engine_name,
              prompt_version,status) VALUES('job',?,'doc','knowledge_extract','mimo','kp_v1','succeeded')""",
                     (cid,))
        for kid,title,course,kind,job in [
            ("kp1","Quick sort",cid,"text","job"),
            ("kp2","Heap sort",cid,"visual","job"),
            ("kp3","No source",cid,None,"job"),
            ("kp4","Other course",other,None,None),
        ]:
            conn.execute("""INSERT INTO knowledge_points
                (id,course_id,canonical_name,summary,review_status,created_job_id)
                VALUES(?,?,?,'knowledge','pending',?)""",(kid,course,title,job))
            if kind:
                conn.execute("""INSERT INTO knowledge_point_sources
                    (knowledge_point_id,chunk_id,evidence_note) VALUES(?,?,?)""",
                    (kid,kind,"course source"))
    return cid,other


def test_direct_bulk_approval_without_an_ai_review(tmp_path):
    db=tmp_path/"db.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload")) as http:
        course,other=setup(http,db)
        before=http.get(f"/api/courses/{course}/knowledge-points/bulk-approval-preview")
        assert before.status_code==200,before.text
        data=before.json()
        assert data["eligible"]==2 and data["missing_evidence"]==1
        assert data["ai_generated"]==3 and data["visual"]==1
        assert {row["id"]:row["eligible"] for row in data["points"]}=={
            "kp1":True,"kp2":True,"kp3":False}
        for confirmed,bypass in [(False,True),(True,False)]:
            response=http.post(f"/api/courses/{course}/knowledge-points/bulk-approve",
                json={"confirmed":confirmed,"bypass_ai_review":bypass,
                      "point_ids":["kp1","kp2"]})
            assert response.status_code==422
        result=http.post(f"/api/courses/{course}/knowledge-points/bulk-approve",
            json={"confirmed":True,"bypass_ai_review":True,
                  "point_ids":["kp1","kp1","kp2","kp3","kp4","absent"]})
        assert result.status_code==200,result.text
        body=result.json()
        assert body["applied"]==["kp1","kp2"]
        assert {item["id"] for item in body["skipped"]}=={"kp3","kp4","absent"}
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="approved"
        assert http.get("/api/knowledge-points/kp2").json()["review_status"]=="approved"
        assert http.get("/api/knowledge-points/kp3").json()["review_status"]=="pending"
        assert http.get("/api/knowledge-points/kp4").json()["review_status"]=="pending"
        with connect(db) as conn:
            events=conn.execute("""SELECT knowledge_point_id,after_json FROM
                knowledge_point_events WHERE knowledge_point_id IN ('kp1','kp2')
                ORDER BY knowledge_point_id""").fetchall()
            assert len(events)==2
            for event in events:
                assert "direct_bulk_user_approval_without_ai_review" in event["after_json"]
        second=http.post(f"/api/courses/{course}/knowledge-points/bulk-approve",
            json={"confirmed":True,"bypass_ai_review":True,"point_ids":["kp1"]})
        assert second.json()["applied"]==[]
        assert second.json()["skipped"]
        assert http.get(f"/api/courses/{other}/knowledge-points/bulk-approval-preview").status_code==200


def test_recheck_missing_source_at_commit(tmp_path):
    db=tmp_path/"db.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload")) as http:
        course,_=setup(http,db)
        assert http.get(f"/api/courses/{course}/knowledge-points/bulk-approval-preview").json()["eligible"]==2
        with connect(db) as conn:
            conn.execute("DELETE FROM knowledge_point_sources WHERE knowledge_point_id='kp1'")
        result=http.post(f"/api/courses/{course}/knowledge-points/bulk-approve",
            json={"confirmed":True,"bypass_ai_review":True,"point_ids":["kp1"]})
        assert not result.json()["applied"]
        assert http.get("/api/knowledge-points/kp1").json()["review_status"]=="pending"
