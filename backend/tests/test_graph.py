"""3D knowledge galaxy backend: real DB hierarchy, vetted edges and BKT."""
from __future__ import annotations

from fastapi.testclient import TestClient

from knowtrace.db import connect, initialize
from knowtrace.main import create_app


def seed(db):
    initialize(db)
    with connect(db) as c:
        c.execute("INSERT INTO users(id,username) VALUES('local','local')")
        c.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course1','local','计算机网络')")
        c.execute("INSERT INTO chapters(id,course_id,title,ordinal) VALUES('ch1','course1','数据通信',1)")
        c.execute("""INSERT INTO chapters(id,course_id,parent_chapter_id,title,ordinal)
                     VALUES('ch2','course1','ch1','调制技术',2)""")
        c.execute("""INSERT INTO knowledge_points
            (id,course_id,chapter_id,canonical_name,review_status,difficulty,summary)
            VALUES('kp1','course1','ch1','数据通信模型','approved',1,'源系统、传输系统、目的系统')""")
        c.execute("""INSERT INTO knowledge_points
            (id,course_id,chapter_id,canonical_name,review_status,difficulty,summary)
            VALUES('kp2','course1','ch2','调制解调器','approved',2,'数字与模拟信号转换')""")
        c.execute("""INSERT INTO knowledge_points
            (id,course_id,canonical_name,review_status)
            VALUES('kp3','course1','未经审核知识点','pending')""")
        c.execute("""INSERT INTO knowledge_relations
            (id,source_kp_id,target_kp_id,relation_type,review_status,rationale)
            VALUES('rel1','kp1','kp2','prerequisite_required','approved','先理解系统组成')""")
        c.execute("""INSERT INTO knowledge_relations
            (id,source_kp_id,target_kp_id,relation_type,review_status)
            VALUES('rel2','kp2','kp3','related','pending')""")
        c.execute("""INSERT INTO mastery_states
            (user_id,knowledge_point_id,model_key,p_mastery,observations)
            VALUES('local','kp1','bkt_v1',0.73,5)""")


def test_graph_from_real_courses_and_approved_edges(tmp_path):
    db=tmp_path/"graph.sqlite3"
    seed(db)
    with TestClient(create_app(db_path=db, storage_dir=tmp_path/"files")) as http:
        response=http.get("/api/courses/course1/knowledge-graph")
        assert response.status_code==200,response.text
        graph=response.json()
        by_id={n["id"]:n for n in graph["nodes"]}
        assert by_id["course:course1"]["name"]=="计算机网络"
        assert by_id["chapter:ch1"]["kind"]=="chapter"
        assert by_id["kp1"]["mastery"]==0.73
        assert by_id["kp1"]["observations"]==5
        assert by_id["kp2"]["mastery"] is None  # untested ≠ 0% mastered
        assert by_id["kp3"]["review_status"]=="pending"
        assert by_id["unclassified:course1"]["virtual"] is True
        edges={(e["source"],e["target"],e["type"]) for e in graph["edges"]}
        assert ("course:course1","chapter:ch1","contains") in edges
        assert ("chapter:ch1","chapter:ch2","contains") in edges
        assert ("chapter:ch2","kp2","contains") in edges
        assert ("unclassified:course1","kp3","contains") in edges
        assert ("kp1","kp2","prerequisite_required") in edges
        assert not any(e["id"]=="rel2" for e in graph["edges"])
        relation=next(e for e in graph["edges"] if e["id"]=="rel1")
        assert relation["verified"] is True and relation["structural"] is False
        assert graph["stats"]["approved_semantic_edges"]==1


def test_graph_respects_review_filter_limit_and_course_isolation(tmp_path):
    db=tmp_path/"graph.sqlite3"
    seed(db)
    with TestClient(create_app(db_path=db, storage_dir=tmp_path/"files")) as http:
        only=http.get("/api/courses/course1/knowledge-graph?include_pending=false")
        assert only.status_code==200
        body=only.json()
        assert body["stats"]["total_knowledge_points"]==2
        assert {n["id"] for n in body["nodes"] if n["kind"]=="knowledge"} == {"kp1","kp2"}
        assert not any(n["id"]=="unclassified:course1" for n in body["nodes"])
        limited=http.get("/api/courses/course1/knowledge-graph?limit=1").json()
        assert limited["stats"]["total_knowledge_points"]==3
        assert limited["stats"]["shown_knowledge_points"]==1
        assert limited["stats"]["truncated"] is True
        assert limited["stats"]["approved_semantic_edges"]==0
        assert http.get("/api/courses/nope/knowledge-graph").status_code==404
        assert http.get("/api/courses/course1/knowledge-graph?limit=0").status_code==422
        assert http.get("/api/courses/course1/knowledge-graph?limit=10000").status_code==422


def test_chapter_organizing_requires_reapproval_before_relations_display(tmp_path):
    db=tmp_path/"graph.sqlite3"
    seed(db)
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"files")) as http:
        result=http.post("/api/courses/course1/chapters",json={"title":"复习专题","ordinal":8})
        assert result.status_code==201,result.text
        chapter_id=result.json()["id"]
        moved=http.patch("/api/knowledge-points/kp1",json={"chapter_id":chapter_id})
        assert moved.status_code==200,moved.text
        assert moved.json()["review_status"]=="pending"
        graph=http.get("/api/courses/course1/knowledge-graph").json()
        assert next(n for n in graph["nodes"] if n["id"]=="kp1")["chapter_id"]==chapter_id
        assert graph["stats"]["approved_semantic_edges"]==0
        restored=http.post("/api/knowledge-points/kp1/review",json={"decision":"approved"})
        assert restored.status_code==200,restored.text
        again=http.get("/api/courses/course1/knowledge-graph").json()
        assert again["stats"]["approved_semantic_edges"]==1


def test_graph_suggests_unclassified_chapter_only_with_multiple_approved_neighbors(tmp_path):
    db = tmp_path / "suggestions.sqlite3"
    seed(db)
    with connect(db) as c:
        c.execute("UPDATE knowledge_points SET review_status='approved' WHERE id='kp3'")
        c.execute("INSERT INTO knowledge_points (id,course_id,chapter_id,canonical_name,review_status) VALUES ('kp4','course1','ch2','模拟信号','approved')")
        c.execute("UPDATE knowledge_relations SET review_status='approved' WHERE id='rel2'")
        c.execute("INSERT INTO knowledge_relations (id,source_kp_id,target_kp_id,relation_type,review_status) VALUES ('rel3','kp4','kp3','related','approved')")
    with TestClient(create_app(db_path=db, storage_dir=tmp_path / "uploads")) as http:
        graph = http.get("/api/courses/course1/knowledge-graph").json()
        nodes = {node["id"]: node for node in graph["nodes"]}
        assert nodes["kp3"]["chapter_id"] is None
        assert nodes["kp3"]["suggested_chapter_id"] == "ch2"
        assert nodes["kp3"]["suggestion_support"] == 2
        assert graph["stats"]["unclassified_points"] == 1
        assert graph["stats"]["suggested_chapter_points"] == 1
        # Suggestions do not perform any writes, nor infer semantic edges.
        with connect(db) as c:
            assert c.execute("SELECT chapter_id FROM knowledge_points WHERE id='kp3'").fetchone()[0] is None
        limited = http.get("/api/courses/course1/knowledge-graph?limit=1").json()
        assert limited["stats"]["suggested_chapter_points"] == 0
