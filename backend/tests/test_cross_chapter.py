"""Cross-chapter bridge discovery, real evidence, dedup and SQLite migration."""
from __future__ import annotations

import json
import time
import threading

from fastapi.testclient import TestClient

from knowtrace.db import connect
from knowtrace.main import create_app
from knowtrace.cross_chapter import bridge_groups, completed_bridge_pairs

QUOTE = "进程同步通过互斥锁和信号量控制共享资源访问顺序，错误顺序可能导致死锁。"


class BridgeStub:
    text_model="test-bridge"
    vision_model=""
    def __init__(self,within_only=False):
        self.calls=[]
        self.within_only=within_only
        self.entered=threading.Event()
        self.release=threading.Event()
        self.release.set()
    def generate_relation_group(self,system,context):
        data=json.loads(context)
        self.calls.append(data)
        self.entered.set()
        assert self.release.wait(8), "Mock bridge response stalled"
        ids=[p["id"] for p in data["knowledge_points"]]
        if len(ids)<2:return '{"items":[]}'
        a,b=ids[0],ids[1]
        if self.within_only:
            # The provider is explicitly adversarial: offer an intra-chapter
            # edge in a cross-chapter run. It must never be stored.
            pairs=[(p,q) for i,p in enumerate(data["knowledge_points"])
                   for q in data["knowledge_points"][i+1:]
                   if p["chapter_id"]==q["chapter_id"]]
            if not pairs:return '{"items":[]}'
            a,b=pairs[0][0]["id"],pairs[0][1]["id"]
        return json.dumps({"items":[{
          "source_kp_id":a,"target_kp_id":b,"relation_type":"related",
          "rationale":"课件明确展示互斥与死锁之间存在共享资源管理方面的联系。",
          "source_chunk_id":data["sources"][0]["chunk_id"],
          "quote":QUOTE,
        }]},ensure_ascii=False)


def setup(http,db):
    course=http.post("/api/courses",json={"title":"操作系统"}).json()["id"]
    with connect(db) as conn:
        topics=[
          ("synchronization",["进程同步 信号量","互斥锁 进程同步"],QUOTE),
          ("deadlocks",["进程同步 死锁","信号量 死锁检测"],QUOTE),
          ("storage",["磁盘管理 调度算法","文件系统存储结构"],"磁盘存储通过磁头寻道和缓存减少访问延迟。"),
        ]
        for ch,names,quote in topics:
            conn.execute("INSERT INTO chapters(id,course_id,title) VALUES(?,?,?)",(ch,course,ch))
            conn.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,
                  media_type,storage_key,sha256,total_pages,status)
                  VALUES(?,?,'local',?,'application/pdf',?,?,1,'ready')""",
                  (ch+'doc',course,ch+'.pdf',ch+'.pdf',ch))
            conn.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,
                 page_start,page_end,content_kind,content_text)
                 VALUES(?,?,0,1,1,'text',?)""",(ch+'chunk',ch+'doc',quote))
            for j,name in enumerate(names):
                kid=ch+str(j)
                conn.execute("""INSERT INTO knowledge_points(id,course_id,chapter_id,
                    canonical_name,summary,review_status)
                    VALUES(?,?,?,?,?,'approved')""",(kid,course,ch,name,name+" "+quote))
                conn.execute("""INSERT INTO knowledge_point_sources(knowledge_point_id,chunk_id)
                               VALUES(?,?)""",(kid,ch+'chunk'))
    return course


def wait_done(http,cid):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        response=http.get(f"/api/courses/{cid}/relation-batches")
        assert response.status_code==200,response.text
        row=response.json()[0]
        if row["status"] in ("succeeded","stopped"):
            return row
        time.sleep(.03)
    raise AssertionError(str(row))


def test_cross_chapter_bridge_is_separate_from_completed_intra_chapter_pass(tmp_path):
    db=tmp_path/"bridge.sqlite3"
    stub=BridgeStub()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=stub)) as http:
        cid=setup(http,db)
        # First complete the intra-chapter run, to ensure bridge mode does NOT
        # erroneously exclude already-processed knowledge point IDs.
        first=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"grouped","group_size":2,"max_focus_points":6,"provider":"default"})
        assert first.status_code==201,first.text
        assert wait_done(http,cid)["status"]=="succeeded"
        start_calls=len(stub.calls)
        assert start_calls==3
        preview=http.get(f"/api/courses/{cid}/relation-batches/bridge-preview?limit=6&group_size=2")
        assert preview.status_code==200,preview.text
        estimate=preview.json()
        assert estimate["eligible"]==6
        assert estimate["actual_chapters"]==3
        assert estimate["unassigned_points"]==0
        assert estimate["source_documents"]==3
        assert estimate["candidate_groups"]>0
        bridge=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"bridge","group_size":2,"max_focus_points":6,"provider":"default"})
        assert bridge.status_code==201,bridge.text
        assert bridge.json()["mode"]=="bridge"
        assert wait_done(http,cid)["status"]=="succeeded"
        bridge_calls=stub.calls[start_calls:]
        assert len(bridge_calls)>0
        with connect(db) as conn:
            rows=conn.execute("""SELECT r.source_kp_id,r.target_kp_id,
                        a.chapter_id AS source_chapter,b.chapter_id AS target_chapter
                        FROM knowledge_relations r
                        JOIN knowledge_points a ON a.id=r.source_kp_id
                        JOIN knowledge_points b ON b.id=r.target_kp_id
                        WHERE a.course_id=? AND a.chapter_id<>b.chapter_id""",(cid,)).fetchall()
            assert rows, "Bridge should yield a real cross-chapter candidate"
            for row in rows:
                assert row["source_chapter"]!=row["target_chapter"]
            seen=completed_bridge_pairs(conn,cid)
            assert seen
        before=len(stub.calls)
        again=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"bridge","group_size":2,"max_focus_points":6,"provider":"default"})
        if again.status_code==201:
            assert wait_done(http,cid)["status"]=="succeeded"
            with connect(db) as conn:
                updated=completed_bridge_pairs(conn,cid)
            assert len(updated)>len(seen)
        else:
            assert again.status_code==409
        # A completed bridge pair must not be sent to the model twice.
        pairs=[frozenset(p["id"] for p in call["knowledge_points"]) for call in stub.calls[start_calls:]]
        assert len(pairs)==len(set(pairs))


def test_bridge_ignores_unrelated_chapters_and_has_bounded_group_sizes(tmp_path):
    db=tmp_path/"bridge.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        cid=setup(http,db)
        planned=bridge_groups(db,cid,set(),6,3)
        assert planned
        assert all(2<=len(g)<=3 for g in planned)
        assert all(len(set(g))==len(g) for g in planned)
        assert len({id for g in planned for id in g})==sum(map(len,planned))
        # The planner does not confuse two points from the same chapter
        # as a cross-chapter bridge by itself.
        chapters={"synchronization0":"sync","synchronization1":"sync",
                  "deadlocks0":"dead","deadlocks1":"dead",
                  "storage0":"storage","storage1":"storage"}
        assert all(len({chapters[k] for k in g})>1 for g in planned)


def test_bridge_rejects_model_generated_same_chapter_edge(tmp_path):
    db=tmp_path/"bridge.sqlite3"
    ai=BridgeStub(within_only=True)
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=ai)) as http:
        cid=setup(http,db)
        started=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"bridge","group_size":3,"max_focus_points":6,"provider":"default"})
        assert started.status_code==201,started.text
        assert wait_done(http,cid)["status"]=="succeeded"
        assert all(any(point["chapter_id"] for point in call["knowledge_points"])
                   for call in ai.calls)
        with connect(db) as conn:
            rows=conn.execute("""SELECT r.id,a.chapter_id,b.chapter_id
                FROM knowledge_relations r JOIN knowledge_points a ON a.id=r.source_kp_id
                JOIN knowledge_points b ON b.id=r.target_kp_id""").fetchall()
            assert all(r[1]!=r[2] for r in rows)


def test_bridge_stop_and_resume_after_committed_group(tmp_path):
    db=tmp_path/"stop-bridge.sqlite3"
    ai=BridgeStub()
    ai.release.clear()
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=ai)) as http:
        cid=setup(http,db)
        created=http.post(f"/api/courses/{cid}/relation-batches",json={
            "mode":"bridge","group_size":2,"max_focus_points":6,"provider":"default"})
        assert created.status_code==201,created.text
        assert ai.entered.wait(3)
        stopping=http.post(f"/api/relation-batches/{created.json()['id']}/stop")
        assert stopping.status_code==200,stopping.text
        ai.release.set()
        paused=wait_done(http,cid)
        assert paused["status"]=="stopped"
        assert paused["completed"]==1
        assert paused["completed_points"]==2
        assert len(ai.calls)==1
        recovered=http.post(f"/api/relation-batches/{created.json()['id']}/resume")
        assert recovered.status_code==200,recovered.text
        final=wait_done(http,cid)
        assert final["status"]=="succeeded"
        assert final["completed_points"]==final["total_points"]
        assert len(ai.calls)==final["total"]


def test_bridge_preview_explains_missing_chapters_in_single_source_file(tmp_path):
    db=tmp_path/"without-chapters.sqlite3"
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/"uploads")) as http:
        course=setup(http,db)
        with connect(db) as conn:
            # Move all six points under one original source, and remove their
            # chapter assignments. There is no honest cross-chapter evidence
            # until the user provides chapter metadata.
            conn.execute("""UPDATE knowledge_point_sources
                SET chunk_id='synchronizationchunk'""")
            conn.execute("UPDATE knowledge_points SET chapter_id=NULL WHERE course_id=?",(course,))
        r=http.get(f"/api/courses/{course}/relation-batches/bridge-preview")
        assert r.status_code==200,r.text
        body=r.json()
        assert body["actual_chapters"]==0
        assert body["source_documents"]==1
        assert body["unassigned_points"]==6
        assert body["candidate_groups"]==0
        assert "章节" in body["note"]
        impossible=http.post(f"/api/courses/{course}/relation-batches",
                             json={"mode":"bridge","max_focus_points":30})
        assert impossible.status_code==409
