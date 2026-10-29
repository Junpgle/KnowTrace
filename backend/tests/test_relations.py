"""Relation candidate validation, human review, cycle prevention, and study paths."""
from __future__ import annotations

import json
from fastapi.testclient import TestClient
import pytest

from knowtrace.db import initialize, connect
from knowtrace.main import create_app
from knowtrace.mimo import MimoProvider

QUOTE = "数据通信系统由源系统传输系统和目的系统三个主要部分构成。"
DESCRIPTION = QUOTE + "发送设备负责把信号变换成适合传输的形式。"


class LocalStub:
    text_model = "mock-local"
    vision_model = ""

    def generate_relations(self, system, context):
        data = json.loads(context)
        items = data["knowledge_points"]
        sources = data["sources"]
        assert len(items) >= 2
        assert sources[0]["text"].startswith(QUOTE)
        return json.dumps({"items": [{
            "source_kp_id": items[0]["id"], "target_kp_id": items[1]["id"],
            "relation_type": "prerequisite_recommended",
            "rationale": "先了解通信模型，有助于学习信号变换过程。",
            "source_chunk_id": sources[0]["chunk_id"], "quote": QUOTE,
        }]}, ensure_ascii=False)


def seed(path):
    initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO users(id,username) VALUES ('local','local')")
        db.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course1','local','计算机网络')")
        db.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('other','local','其他课程')")
        for kid, name, course, state in [
            ("a","数据通信系统","course1","approved"),
            ("b","信号变换","course1","approved"),
            ("c","数据传输","course1","approved"),
            ("pending","待审核知识","course1","pending"),
            ("foreign","外部知识","other","approved"),
        ]:
            db.execute("""INSERT INTO knowledge_points
                 (id,course_id,canonical_name,review_status) VALUES (?,?,?,?)""",
                 (kid,course,name,state))
        db.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,media_type,
                   storage_key,sha256,total_pages,status)
                   VALUES('doc1','course1','local','lecture.pdf',
                   'application/pdf','doc1.pdf','hash1',1,'ready')""")
        db.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,page_start,
                   page_end,content_kind,content_text)
                   VALUES('chunk1','doc1',0,1,1,'text',?)""",(DESCRIPTION,))
        for kid in ("a","b","c"):
            db.execute("INSERT INTO knowledge_point_sources(knowledge_point_id,chunk_id) VALUES (?, 'chunk1')", (kid,))
        db.execute("""INSERT INTO mastery_states(user_id,knowledge_point_id,model_key,p_mastery,observations)
                   VALUES('local','a','bkt_v1',0.95,5)""")
        db.execute("""INSERT INTO mastery_states(user_id,knowledge_point_id,model_key,p_mastery,observations)
                   VALUES('local','b','bkt_v1',0.35,3)""")


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "relation.sqlite3"
    seed(path)
    with TestClient(create_app(db_path=path,storage_dir=tmp_path/"upload",
                               ai_provider=LocalStub())) as http:
        yield http, path


def candidate(source="a", target="b", relation_type="prerequisite_required",
              chunk="chunk1", quote=QUOTE):
    return {"items": [{"source_kp_id":source,"target_kp_id":target,
             "relation_type":relation_type, "rationale":"理解数据通信模型后才能分析发送设备的作用和信号变化。",
             "source_chunk_id":chunk, "quote":quote}]}


def import_relations(client, value, focus="a"):
    return client.post("/api/courses/course1/relations/import",json={
        "focus_id":focus,"count":3,"response_text":json.dumps(value,ensure_ascii=False)
    })


def test_manual_ai_handoff_review_and_graph_update(client):
    http,_=client
    prompt=http.post("/api/courses/course1/relations/prompt",json={"focus_id":"a","count":3})
    assert prompt.status_code==200,prompt.text
    assert QUOTE in prompt.json()["prompt"]
    assert "chunk1" in prompt.json()["prompt"]
    reply="提取的关系：\n"+chr(96)*3+"json\n"+json.dumps(candidate(),ensure_ascii=False)+"\n"+chr(96)*3
    imported=http.post("/api/courses/course1/relations/import",json={
        "focus_id":"a","count":3,"response_text":reply})
    assert imported.status_code==200,imported.text
    assert imported.json()["created"]==1
    rid=imported.json()["relation_ids"][0]
    pending=http.get("/api/courses/course1/knowledge-relations").json()
    assert pending[0]["review_status"]=="pending"
    assert pending[0]["sources"][0]["page_start"]==1
    assert "exact quotation" in pending[0]["sources"][0]["evidence_note"]
    assert http.get("/api/courses/course1/knowledge-graph").json()["stats"]["approved_semantic_edges"]==0
    approved=http.post("/api/knowledge-relations/"+rid+"/review",json={"decision":"approved"})
    assert approved.status_code==200,approved.text
    graph=http.get("/api/courses/course1/knowledge-graph").json()
    edge=next(e for e in graph["edges"] if e["id"]==rid)
    assert edge["type"]=="prerequisite_required" and edge["verified"] is True
    path=http.get("/api/courses/course1/study-path").json()
    names=[p["id"] for p in path["items"]]
    assert names.index("a") < names.index("b")
    assert path["required_edges"]==1
    again=import_relations(http,candidate())
    assert again.status_code==200 and again.json()["created"]==0
    assert len(http.get("/api/courses/course1/knowledge-relations").json())==1


def test_invalid_cross_course_fake_quotes_and_pending_endpoints(client):
    http,_=client
    for item in [
        candidate(target="foreign"),candidate(target="pending"),
        candidate(chunk="not-a-chunk"),candidate(quote="这是一段纯粹编造且不存在的课件内容证据。"),
        candidate(source="a",target="a"),
        candidate(source="b",target="c"),
        candidate(relation_type=["related"]),
    ]:
        result=import_relations(http,item)
        assert result.status_code==200,result.text
        assert result.json()["created"]==0
        assert result.json()["warnings"]
    assert http.post("/api/courses/course1/relations/prompt",
                     json={"focus_id":"pending"}).status_code==409
    assert http.post("/api/courses/other/relations/prompt",
                     json={"focus_id":"a"}).status_code==409
    assert http.post("/api/courses/course1/relations/prompt",
                     json={"focus_id":"a","target_id":"foreign"}).status_code==422
    assert http.get("/api/courses/other/knowledge-relations").json()==[]


def test_cycle_is_rejected_and_manual_requires_real_quote(client):
    http,path=client
    for source,target in [("a","b"),("b","c")]:
        output=import_relations(http,candidate(source,target),source)
        assert output.json()["created"]==1
        assert http.post("/api/knowledge-relations/"+output.json()["relation_ids"][0]+"/review",
                         json={"decision":"approved"}).status_code==200
    inverse=import_relations(http,candidate("c","a"),"c")
    rid=inverse.json()["relation_ids"][0]
    rejected=http.post("/api/knowledge-relations/"+rid+"/review",json={"decision":"approved"})
    assert rejected.status_code==409 and "cycle" in rejected.text
    assert http.post("/api/knowledge-relations/"+rid+"/review",json={"decision":"rejected"}).status_code==200
    manual={"source_kp_id":"a","target_kp_id":"c","relation_type":"related",
            "rationale":"这两个知识点涉及完整的通信链路过程。","source_chunk_id":"chunk1",
            "quote":"非常虚假的证据句子，课件中肯定不存在"}
    assert http.post("/api/courses/course1/relations/manual",json=manual).status_code==422
    manual["quote"]=QUOTE
    accepted=http.post("/api/courses/course1/relations/manual",json=manual)
    assert accepted.status_code==200,accepted.text
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM knowledge_relation_events").fetchone()[0]==3
        assert db.execute("""SELECT COUNT(*) FROM knowledge_relations r
                             JOIN processing_jobs j ON j.id=r.created_job_id
                             WHERE j.job_type='relation_extract'""").fetchone()[0]==4


def test_local_model_generation_and_path_fallback(client):
    http,_=client
    result=http.post("/api/courses/course1/relations/generate",
                     json={"focus_id":"a","count":2,"provider":"default"})
    assert result.status_code==200,result.text
    assert result.json()["created"]==1
    assert result.json()["provider"]=="ollama"
    before=http.get("/api/courses/course1/study-path").json()
    assert before["required_edges"]==0
    assert "严格的先修路径" in before["notice"]
    assert before["items"][0]["id"]=="c"  # untested first; stable ID tie-break
    assert before["items"][1]["id"]=="b"  # lower mastery than a
    assert http.get("/api/courses/course1/study-path?limit=0").status_code==422


def test_mimo_lazy_retrieval_and_candidate_validation(client,monkeypatch):
    http,_=client
    def fake_from_env():
        m=MimoProvider(api_key="sk-test")
        requests=[]
        def fake_post(messages,tools=None):
            requests.append(json.dumps(messages,ensure_ascii=False))
            if len(requests)==1:
                assert QUOTE not in requests[0]
                assert tools and tools[0]["function"]["name"]=="read_source_chunk"
                return {"choices":[{"message":{"content":None,"tool_calls":[
                    {"id":"call001","type":"function","function":{
                       "name":"read_source_chunk","arguments":json.dumps({"chunk_id":"chunk1"})}}
                ]}}],"usage":{"total_tokens":70}}
            assert QUOTE in requests[-1]
            return {"choices":[{"message":{"content":json.dumps(
                candidate(relation_type="prerequisite_recommended"),ensure_ascii=False)}}],
                "usage":{"total_tokens":80}}
        m._post=fake_post
        return m
    monkeypatch.setattr(MimoProvider,"from_env",staticmethod(fake_from_env))
    output=http.post("/api/courses/course1/relations/generate",
                     json={"focus_id":"a","count":1,"provider":"mimo"})
    assert output.status_code==200,output.text
    body=output.json()
    assert body["created"]==1
    assert body["tool_calls"]==1 and body["retrieved_chunks"]==1
    assert body["usage"]["total_tokens"]==150
