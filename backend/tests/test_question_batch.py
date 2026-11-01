"""Real API integration tests for multi-concept questions and saved checkpoints."""
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from knowtrace.db import transaction
from knowtrace.main import create_app
from knowtrace.question_batch_api import validate_cluster_reply


FIRST = "The sender encodes data into electrical signals before transmission."
SECOND = "The receiver decodes electrical signals into understandable messages."


def uid():
    return str(uuid4())


class FakeClusterProvider:
    text_model = "fake-bulk-model"

    def generate_cluster_questions(self, *, prompt: str) -> str:
        ctx = json.loads(prompt.split("知识点与可检索来源：\n", 1)[1])
        points = ctx["knowledge_points"]
        chunks = ctx["sources"]
        assert len(points) == 2 and len(chunks) == 2
        return json.dumps({"items": [{
            "question_type": "single_choice",
            "stem": "How do the transmitter and receiver cooperate during communication?",
            "options": {"A":"Encode and decode signals", "B":"Only forward IP packets",
                        "C":"Only save files locally","D":"Never process messages"},
            "correct_answer": "A",
            "explanation": "The sender encodes data; the receiver decodes signals.",
            "difficulty":2,
            "primary_knowledge_point_id":points[0]["id"],
            "knowledge_point_ids":[p["id"] for p in points],
            "evidence":[{"source_chunk_id":s["chunk_id"],
                         "quote":s["content_text"]} for s in chunks],
        }]}, ensure_ascii=False)


def populate(client: TestClient, db_path):
    response=client.post("/api/courses",json={"title":"Batch test"})
    assert response.status_code==201, response.text
    course=response.json()["id"]
    chapter,document=uid(),uid()
    points=[uid(),uid()]
    chunks=[uid(),uid()]
    with transaction(db_path) as db:
        db.execute("INSERT INTO chapters(id,course_id,title) VALUES (?,?,?)",
                   (chapter,course,"Chapter 1"))
        db.execute("""INSERT INTO documents
            (id,course_id,uploaded_by,original_filename,media_type,storage_key,sha256,status)
            VALUES (?,?,'local','lecture.pdf','application/pdf',?,?,'ready')""",
            (document,course,uid(),uid()))
        for i,(point,chunk,body) in enumerate(zip(points,chunks,(FIRST,SECOND))):
            db.execute("""INSERT INTO document_chunks
                (id,document_id,chunk_index,page_start,page_end,content_text)
                VALUES (?,?,?,?,?,?)""",(chunk,document,i,i+1,i+1,body))
            db.execute("""INSERT INTO knowledge_points
                (id,course_id,chapter_id,canonical_name,summary,review_status)
                VALUES (?,?,?,?,?,'approved')""",
                (point,course,chapter,f"Concept {i}",body))
            db.execute("""INSERT INTO knowledge_point_sources
                (knowledge_point_id,chunk_id) VALUES (?,?)""",(point,chunk))
    return course,chapter,points


def test_multiple_concepts_are_saved_and_not_regenerated(tmp_path):
    database=tmp_path/"batch.sqlite3"
    app=create_app(db_path=database, storage_dir=tmp_path/"uploads",
                   ai_provider=FakeClusterProvider())
    with TestClient(app) as http:
        course,chapter,points=populate(http,database)
        preview=http.get(f"/api/courses/{course}/question-batches/preview",
                         params={"chapter_id":chapter,"group_size":2})
        assert preview.status_code==200,preview.text
        assert preview.json()["eligible"]==2
        assert preview.json()["estimated_groups"]==1
        payload={"provider":"default","chapter_id":chapter,"group_size":2,
                 "max_groups":1,"questions_per_group":1,
                 "question_types":["single_choice"],"confirmed":True}
        bad=http.post(f"/api/courses/{course}/question-batches",
                      json={**payload,"confirmed":False})
        assert bad.status_code==422
        created=http.post(f"/api/courses/{course}/question-batches",json=payload)
        assert created.status_code==201,created.text
        batch_id=created.json()["id"]
        for _ in range(100):
            status=http.get(f"/api/question-batches/{batch_id}").json()
            if status["status"] not in ("running","queued","stopping"):
                break
            time.sleep(.05)
        assert status["status"]=="succeeded",status
        assert status["completed_groups"]==1
        assert status["created_questions"]==1
        questions=http.get(f"/api/courses/{course}/questions").json()
        assert len(questions)==1
        assert questions[0]["review_status"]=="pending"
        detail=http.get("/api/question-versions/"+questions[0]["version_id"]).json()
        mappings={k["knowledge_point_id"]:k["is_primary"] for k in detail["knowledge_points"]}
        assert set(mappings)==set(points)
        assert sum(mappings.values())==1
        assert len(detail["sources"])==2
        no_new=http.get(f"/api/courses/{course}/question-batches/preview")
        assert no_new.json()["uncovered"]==0
        assert http.post(f"/api/courses/{course}/question-batches",json=payload).status_code==409
    # Simulate restart using exactly the same persistent SQLite database.
    with TestClient(create_app(db_path=database, storage_dir=tmp_path/"uploads",
                               ai_provider=FakeClusterProvider())) as http:
        saved=http.get(f"/api/question-batches/{batch_id}").json()
        assert saved["status"]=="succeeded"
        assert saved["created_questions"]==1


def test_mapping_requires_source_for_each_concept():
    chunks={"a":{"chunk_id":"a","content_text":FIRST,"knowledge_point_ids":["p1"]},
            "b":{"chunk_id":"b","content_text":SECOND,"knowledge_point_ids":["p2"]}}
    base={"question_type":"true_false","stem":"A sender and receiver cooperate in a system.",
          "options":{},"correct_answer":True,
          "explanation":"The lecture defines each communication component.",
          "difficulty":2,"knowledge_point_ids":["p1","p2"],
          "primary_knowledge_point_id":"p1",
          "evidence":[{"source_chunk_id":"a","quote":FIRST}]}
    accepted,warnings=validate_cluster_reply(json.dumps({"items":[base]}),
        allowed_ids={"p1","p2"},chunks=chunks,seen_stems=set(),
        count=2,types={"true_false"})
    assert accepted==[]
    assert "lack source evidence" in warnings[0]
    base["evidence"].append({"source_chunk_id":"b","quote":SECOND})
    accepted,warnings=validate_cluster_reply(json.dumps({"items":[base]}),
        allowed_ids={"p1","p2"},chunks=chunks,seen_stems=set(),
        count=2,types={"true_false"})
    assert len(accepted)==1 and warnings==[]
