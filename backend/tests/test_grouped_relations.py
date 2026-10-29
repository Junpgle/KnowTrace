"""Chapter-first and semantic-similarity grouped relation API regressions."""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app
from knowtrace.grouped_relations import plan_groups, shingles

QUOTE_A = "缓存一致性协议维护多处理器之间数据副本的一致性和更新传播。"
QUOTE_B = "进程同步通过互斥和信号量协调多个并发执行主体的共享资源。"


class GroupStub:
    text_model = "fake-group"
    vision_model = ""
    def __init__(self):
        self.calls: list[dict] = []
        self.started=threading.Event()
        self.release=threading.Event()
        self.release.set()

    def generate_relation_group(self, system, context):
        data=json.loads(context)
        self.calls.append(data)
        self.started.set()
        assert self.release.wait(10), "Timed out waiting for test model"
        ids=[p["id"] for p in data["knowledge_points"]]
        if len(ids)<2:
            return '{"items":[]}'
        src=data["sources"][0]
        quote = QUOTE_A if QUOTE_A in src["text"] else QUOTE_B
        return json.dumps({"items":[{
            "source_kp_id":ids[0],"target_kp_id":ids[1],
            "relation_type":"related",
            "rationale":"两项知识点均有本组材料所支持的明确概念联系。",
            "source_chunk_id":src["chunk_id"],"quote":quote,
        }]},ensure_ascii=False)


def create_data(http,db, n_per_chapter=15):
    cid=http.post("/api/courses",json={"title":"Computer Systems"}).json()["id"]
    with connect(db) as con:
        for chapter,quote in [("chapter-a",QUOTE_A),("chapter-b",QUOTE_B)]:
            con.execute("INSERT INTO chapters (id,course_id,title) VALUES(?,?,?)",
                        (chapter,cid,chapter))
            doc=chapter+"-doc"
            con.execute("""INSERT INTO documents
              (id,course_id,uploaded_by,original_filename,media_type,
               storage_key,sha256,total_pages,status)
              VALUES (?,?,'local',?,'application/pdf',?, ?,1,'ready')""",
              (doc,cid,doc+".pdf",doc+".pdf",doc))
            chunk=chapter+"-chunk"
            con.execute("""INSERT INTO document_chunks
               (id,document_id,chunk_index,page_start,page_end,content_kind,content_text)
               VALUES (?,?,0,1,1,'text',?)""",(chunk,doc,quote))
            for i in range(n_per_chapter):
                kid=chapter+"-"+str(i)
                name=("缓存一致性协议 " if chapter=="chapter-a" else "进程互斥同步 ") + str(i)
                con.execute("""INSERT INTO knowledge_points
                 (id,course_id,chapter_id,canonical_name,summary,review_status)
                 VALUES (?,?,?,?,?,'approved')""",
                 (kid,cid,chapter,name,quote))
                con.execute("""INSERT INTO knowledge_point_sources
                 (knowledge_point_id,chunk_id) VALUES (?,?)""",(kid,chunk))
    return cid


def until(http,cid,desired,deadline=10):
    expiration=time.monotonic()+deadline
    while time.monotonic()<expiration:
        result=http.get(f"/api/courses/{cid}/relation-batches")
        assert result.status_code==200,result.text
        item=result.json()[0]
        if item["status"] in desired:return item
        time.sleep(0.025)
    raise AssertionError(str(item))


def test_thirty_points_become_four_calls_and_never_mix_chapters(tmp_path):
    db=tmp_path/"groups.sqlite3"
    ai=GroupStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ai)) as http:
        cid=create_data(http,db,15)
        groups=plan_groups(db,cid,set(),30,12)
        assert len(groups)==4
        assert sorted(map(len,groups))==[3,3,12,12]
        assert sum(map(len,groups))==30
        assert all(len({id.split("-")[1] for id in group})==1 for group in groups)
        started=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"grouped","group_size":12,"max_focus_points":30,
            "provider":"default","per_focus":2})
        assert started.status_code==201,started.text
        assert started.json()["total"]==4
        assert started.json()["total_points"]==30
        done=until(http,cid,{"succeeded"})
        assert done["completed"]==4 and done["completed_points"]==30
        assert done["progress"]==100
        assert done["created_relations"]==4
        assert len(ai.calls)==4
        assert all(len(c["knowledge_points"])<=12 for c in ai.calls)
        assert len(http.get(f"/api/courses/{cid}/knowledge-relations").json())==4
        # Previously processed point IDs must not trigger paid calls again.
        second=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"grouped","group_size":12,"max_focus_points":30,
            "provider":"default","per_focus":2})
        assert second.status_code==409


def test_grouped_stop_resume_checks_one_whole_group(tmp_path):
    db=tmp_path/"stop.sqlite3"
    ai=GroupStub()
    ai.release.clear()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ai)) as http:
        cid=create_data(http,db,15)
        start=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"grouped","group_size":8,"max_focus_points":24,
            "provider":"default","per_focus":1})
        assert start.status_code==201,start.text
        assert ai.started.wait(3)
        stop=http.post(f"/api/relation-batches/{start.json()['id']}/stop")
        assert stop.status_code==200
        ai.release.set()
        paused=until(http,cid,{"stopped"})
        assert paused["completed"]==1
        assert paused["completed_points"]==8
        assert len(ai.calls)==1
        resume=http.post(f"/api/relation-batches/{start.json()['id']}/resume")
        assert resume.status_code==200,resume.text
        final=until(http,cid,{"succeeded"})
        assert final["completed_points"]==24
        assert final["completed"]==4  # one chapter remainder is a singleton
        assert len(ai.calls)==3  # singleton is skipped without an API request


def test_similar_knowledge_is_prioritized_even_when_out_of_order(tmp_path):
    db=tmp_path/"semantic.sqlite3"
    ai=GroupStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ai)) as http:
        cid=create_data(http,db,0)
        with connect(db) as con:
            nodes=[
                ("first","缓存一致性 MESI 状态"),
                ("unrelated","磁盘调度算法 SSTF"),
                ("similar","缓存一致性 MESI 协议"),
                ("other","死锁检测算法"),
            ]
            for kid,name in nodes:
                con.execute("""INSERT INTO knowledge_points
                  (id,course_id,chapter_id,canonical_name,summary,review_status)
                  VALUES (?,?, 'chapter-a', ?,?,'approved')""",(kid,cid,name,name))
                con.execute("""INSERT INTO knowledge_point_sources
                  (knowledge_point_id,chunk_id) VALUES (?, 'chapter-a-chunk')""",(kid,))
        groups=plan_groups(db,cid,set(),4,2)
        assert groups[0][0]=="first"
        assert groups[0][1]=="similar"
        assert len(groups)==2
        assert "c:缓存" in shingles("缓存一致性协议","")


def test_unclassified_nodes_group_by_real_document_not_invented_chapter(tmp_path):
    db=tmp_path/"unclassified.sqlite3"
    ai=GroupStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=ai)) as http:
        cid=create_data(http,db,2)
        with connect(db) as con:
            con.execute("""UPDATE knowledge_points SET chapter_id=NULL
                WHERE course_id=?""",(cid,))
        groups=plan_groups(db,cid,set(),4,3)
        assert len(groups)==2
        assert all(len(g)==2 for g in groups)


def test_1308_points_are_batched_into_about_110_model_requests(tmp_path):
    db=tmp_path/"large_groups.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload")) as http:
        cid=create_data(http,db,654)
        groups=plan_groups(db,cid,set(),1308,12)
        assert len(groups)==110
        assert sum(map(len,groups))==1308
        assert max(map(len,groups))<=12
        all_ids=[item for group in groups for item in group]
        assert len(all_ids)==len(set(all_ids))
        assert all(len({id.split("-")[1] for id in group})==1 for group in groups)


def test_live_visible_group_stream_while_model_is_still_responding(tmp_path):
    class IncrementalAI(GroupStub):
        def __init__(self):
            super().__init__()
            self.release.clear()
            self.first_tokens=threading.Event()
        def generate_relation_group_stream(self,system,context,on_delta):
            data=json.loads(context)
            self.calls.append(data)
            on_delta('{"items":[')
            self.first_tokens.set()
            assert self.release.wait(8)
            reply=json.dumps({"items":[]})
            on_delta(reply[len('{"items":['):])
            return reply

    db=tmp_path/"live.sqlite3"
    provider=IncrementalAI()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"upload",ai_provider=provider)) as http:
        cid=create_data(http,db,2)
        start=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"grouped","group_size":4,"max_focus_points":4,
            "provider":"default","per_focus":2})
        assert start.status_code==201,start.text
        job=start.json()["id"]
        assert provider.first_tokens.wait(4)
        live=http.get(f"/api/relation-batches/{job}/live")
        assert live.status_code==200,live.text
        info=live.json()
        assert info["phase"]=="streaming"
        assert info["stream_supported"] is True
        assert info["text"]=='{"items":['
        assert info["received_chars"]==len('{"items":[')
        assert info["group_size"]==2
        assert http.get(f"/api/courses/{cid}/knowledge-relations").json()==[]
        provider.release.set()
        done=until(http,cid,{"succeeded"})
        assert done["completed"]==2
        final=http.get(f"/api/relation-batches/{job}/live").json()
        assert final["phase"]=="validated"
        assert final["received_chars"]>info["received_chars"]
        assert final["text"]=='{"items":[]}', repr(final)
        assert http.get("/api/relation-batches/not-a-real-task/live").status_code==404
