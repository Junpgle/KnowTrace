"""PDF source → chapter mapping should never discard vetted knowledge or edges."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knowtrace.db import connect, initialize
from knowtrace.main import create_app


def seed(db):
    initialize(db)
    with connect(db) as c:
        c.execute("INSERT INTO users(id,username) VALUES ('local','local')")
        c.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course1','local','网络')")
        c.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course2','local','其他')")
        c.execute("INSERT INTO chapters(id,course_id,title,ordinal) VALUES ('ch1','course1','第一章 基础',1)")
        c.execute("INSERT INTO chapters(id,course_id,title,ordinal) VALUES ('other','course2','不属于本课',1)")
        for did, filename, media in [
            ('d1','第一章 基础.pdf','application/pdf'),
            ('d2','第二章 网络层.pdf','application/pdf'),
            ('d3','补充.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation'),
        ]:
            c.execute("""INSERT INTO documents
                (id,course_id,uploaded_by,original_filename,media_type,storage_key,sha256,status)
                VALUES (?,?,?,?,?,?,?,'ready')""",
                (did,'course1','local',filename,media,did,'a'*64))
            c.execute("""INSERT INTO document_chunks
                (id,document_id,chunk_index,page_start,page_end,content_text)
                VALUES (?,?,0,1,1,'证据文本')""", ('chunk-'+did,did))
        for pid, ch, status in [
            ('p1',None,'approved'), ('p2',None,'approved'),
            ('p3',None,'approved'), ('p4',None,'approved'),
            ('p5','ch1','approved'), ('p6',None,'pending'),
            ('p7',None,'rejected'),
        ]:
            c.execute("""INSERT INTO knowledge_points
                (id,course_id,chapter_id,canonical_name,review_status)
                VALUES (?,?,?,?,?)""", (pid,'course1',ch,'concept-'+pid,status))
        for pid,did in [
            ('p1','d1'),('p2','d2'),('p3','d1'),('p3','d2'),
            ('p4','d1'),('p4','d3'),('p5','d1'),('p7','d1'),
        ]:
            c.execute("""INSERT INTO knowledge_point_sources(knowledge_point_id,chunk_id)
                VALUES (?,?)""",(pid,'chunk-'+did))
        c.execute("""INSERT INTO knowledge_relations
            (id,source_kp_id,target_kp_id,relation_type,review_status)
            VALUES ('r1','p1','p5','related','approved')""")


@pytest.fixture
def app_db(tmp_path):
    db=tmp_path/'source.sqlite3'
    seed(db)
    with TestClient(create_app(db_path=db,storage_dir=tmp_path/'files')) as http:
        yield http,db


def test_source_preview_and_bulk_preserves_approval_and_relation(app_db):
    http,db=app_db
    preview=http.get('/api/courses/course1/source-chapters/preview')
    assert preview.status_code==200,preview.text
    body=preview.json()
    assert body['unclassified_points']==6
    assert body['pdf_documents']==2
    assert body['no_source_points']==1
    assert body['multi_document_points']==2
    assert body['single_pdf_points']==3
    docs={d['document_id']:d for d in body['documents']}
    assert docs['d1']['matching_chapter_id']=='ch1'
    assert docs['d2']['matching_chapter_id'] is None
    with connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM chapters WHERE course_id=?',('course1',)).fetchone()[0]==1
        assert c.execute('SELECT chapter_id FROM knowledge_points WHERE id=?',('p1',)).fetchone()[0] is None

    payload={'confirmed':True,'mappings':[
        {'document_id':'d1','chapter_id':'ch1'},
        {'document_id':'d2','chapter_title':'第二章 网络层'},
    ]}
    result=http.post('/api/courses/course1/source-chapters/apply',json=payload)
    assert result.status_code==200,result.text
    applied=result.json()
    assert applied['assigned_points']==3
    assert len(applied['created_chapters'])==1
    assert applied['remaining_unclassified']==3
    assert applied['skipped_ambiguous']==1
    assert applied['skipped_unmapped_or_no_source']==2

    with connect(db) as c:
        rows={r['id']:dict(r) for r in c.execute(
            'SELECT id,chapter_id,review_status FROM knowledge_points')}
        assert rows['p1']=={'id':'p1','chapter_id':'ch1','review_status':'approved'}
        assert rows['p2']['chapter_id']==applied['created_chapters'][0]['id']
        assert rows['p3']['chapter_id'] is None  # source PDFs disagree
        assert rows['p4']['chapter_id'] is None  # missing PPTX mapping
        assert rows['p5']['chapter_id']=='ch1'  # never overwrite
        assert rows['p6']['chapter_id'] is None
        assert rows['p7']['review_status']=='rejected'  # not silently approved
        assert c.execute("SELECT COUNT(*) FROM knowledge_point_events WHERE action='edited'").fetchone()[0]==3

    graph=http.get('/api/courses/course1/knowledge-graph').json()
    assert graph['stats']['approved_semantic_edges']==1
    again=http.post('/api/courses/course1/source-chapters/apply',json=payload)
    assert again.status_code==200,again.text
    assert again.json()['assigned_points']==0
    assert again.json()['created_chapters']==[]


def test_multiple_pdf_sources_can_share_chapter_when_mapping_agrees(app_db):
    http,db=app_db
    result=http.post('/api/courses/course1/source-chapters/apply',json={
        'confirmed':True,
        'mappings':[{'document_id':'d1','chapter_id':'ch1'},
                    {'document_id':'d2','chapter_id':'ch1'}],
    })
    assert result.status_code==200,result.text
    assert result.json()['assigned_points']==4
    with connect(db) as c:
        assert c.execute('SELECT chapter_id FROM knowledge_points WHERE id=?',('p3',)).fetchone()[0]=='ch1'


@pytest.mark.parametrize('payload',[
    {'confirmed':False,'mappings':[{'document_id':'d1','chapter_id':'ch1'}]},
    {'confirmed':True,'mappings':[{'document_id':'d1','chapter_id':'other'}]},
    {'confirmed':True,'mappings':[{'document_id':'d3','chapter_title':'补充'}]},
    {'confirmed':True,'mappings':[{'document_id':'d1','chapter_id':'ch1'},
                                  {'document_id':'d1','chapter_title':'新章'}]},
    {'confirmed':True,'mappings':[{'document_id':'d1','chapter_id':'ch1',
                                  'chapter_title':'混用'}]},
])
def test_invalid_mapping_never_writes(app_db,payload):
    http,db=app_db
    response=http.post('/api/courses/course1/source-chapters/apply',json=payload)
    assert response.status_code in (409,422)
    with connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM chapters WHERE course_id='course1'").fetchone()[0]==1
        assert c.execute("SELECT chapter_id FROM knowledge_points WHERE id='p1'").fetchone()[0] is None


def test_other_course_and_missing_course(app_db):
    http,_=app_db
    assert http.get('/api/courses/not-found/source-chapters/preview').status_code==404
    assert http.post('/api/courses/not-found/source-chapters/apply',json={
        'confirmed':True,'mappings':[{'document_id':'d1','chapter_id':'ch1'}]
    }).status_code==404
