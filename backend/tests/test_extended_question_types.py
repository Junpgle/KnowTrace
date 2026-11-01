"""Regression tests for multi-select, short answers and non-BKT subjectivity."""
import json
import sqlite3

import fitz
from fastapi.testclient import TestClient

from knowtrace.db import connect, initialize
from knowtrace.main import create_app
from knowtrace.questions import validate_question_fields

QUOTE = "The source sends messages to the transmission channel"


class ExtendedAI:
    text_model = "mock"
    vision_model = ""

    def extract(self, pages):
        return json.dumps({"items":[{
            "name":"Source system", "summary":"Sends messages to the channel.",
            "quote":QUOTE, "page":1, "difficulty":2,
        }]})

    def generate_questions(self, *, knowledge_name, knowledge_summary, sources, count, question_types):
        chunk = sources[0]["id"]
        options = {"A":"Encodes a message", "B":"Deletes all messages",
                   "C":"Transmits a signal", "D":"Disables all links"}
        return json.dumps({"items":[{
            "question_type":"multiple_choice",
            "stem":"Which tasks can a source system perform in communication?",
            "options":options, "correct_answer":["A","C"],
            "explanation":"The source emits messages and signals.",
            "difficulty":2, "source_chunk_id":chunk, "quote":QUOTE,
        },{
            "question_type":"short_answer",
            "stem":"Describe the role of a source system in communication.",
            "options":{},
            "correct_answer":"The source prepares a message and sends it through the transmission channel.",
            "explanation":"Mention message preparation, sending, and the transmission system.",
            "difficulty":2, "source_chunk_id":chunk, "quote":QUOTE,
        }]})

    def grade_short_answer(self, *, prompt):
        assert "student_answer" in prompt and "reference" in prompt
        return json.dumps({"verdict":"uncertain","feedback":"请补充信号的传输环节，再对照课件复核。"})

def seed(http):
    course = http.post("/api/courses",json={"title":"Communication"}).json()["id"]
    pdf=fitz.open()
    page=pdf.new_page()
    page.insert_text((20,80),QUOTE)
    upload=http.post(f"/api/courses/{course}/documents",
        files={"file":("network.pdf",pdf.tobytes(),"application/pdf")})
    assert upload.status_code==201,upload.text
    extracted=http.post(f"/api/documents/{upload.json()['id']}/extract-knowledge",json={"page_start":1})
    assert extracted.status_code==200,extracted.text
    kp=extracted.json()["created_ids"][0]
    assert http.post(f"/api/knowledge-points/{kp}/review",
        json={"decision":"approved"}).status_code==200
    generated=http.post(f"/api/knowledge-points/{kp}/generate-questions",
       json={"count":2,"question_types":["multiple_choice","short_answer"]})
    assert generated.status_code==200,generated.text
    ids=generated.json()["question_version_ids"]
    assert len(ids)==2,generated.text
    return course,kp,ids

def test_extended_question_types_and_subjective_review(tmp_path):
    db=tmp_path/"quiz.sqlite3"
    with TestClient(create_app(db_path=db, storage_dir=tmp_path/"uploads", ai_provider=ExtendedAI())) as http:
        course,kp,ids=seed(http)
        for vid in ids:
            assert http.post(f"/api/question-versions/{vid}/review",
                json={"decision":"approved"}).status_code==200
        quiz=http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2})
        assert quiz.status_code==201,quiz.text
        items=quiz.json()["items"]
        multi=next(i for i in items if i["question_type"]=="multiple_choice")
        short=next(i for i in items if i["question_type"]=="short_answer")
        assert "correct_answer" not in multi and "correct_answer" not in short
        endpoint=f"/api/quiz-items/{multi['quiz_item_id']}/answer"
        assert http.post(endpoint,json={"client_request_id":"invalid-order","response":["C","A"]}).status_code==422
        answer=http.post(endpoint,json={"client_request_id":"valid-multi-100","response":["A","C"]})
        assert answer.status_code==200,answer.text
        assert answer.json()["is_correct"] is True
        assert answer.json()["mastery"] is None
        short_url=f"/api/quiz-items/{short['quiz_item_id']}/answer"
        assert http.post(short_url,json={"client_request_id":"empty-short-100","response":"   "}).status_code==422
        response=http.post(short_url,json={"client_request_id":"valid-short-100",
            "response":"It sends the prepared message to the channel."})
        assert response.status_code==200,response.text
        assert response.json()["is_correct"] is None
        assert response.json()["diagnostic"] is False
        assert "reference" not in response.json()
        assessment=f"/api/quiz-items/{short['quiz_item_id']}/short-answer-review"
        self_mark=http.post(assessment,json={"mode":"self","verdict":"learned"})
        assert self_mark.status_code==200,self_mark.text
        assert self_mark.json()["verdict"]=="learned" and self_mark.json()["diagnostic"] is False
        assert http.post(assessment,json={"mode":"ai"}).status_code==409
        qdetail=http.get(f"/api/quiz-sessions/{quiz.json()['id']}").json()
        assert qdetail["objective_total"]==1 and qdetail["subjective_total"]==1
        assert qdetail["evaluated_subjective"]==1
        assert next(i for i in qdetail["items"] if i["question_type"]=="short_answer")["evaluation"]["verdict"]=="learned"
        report=http.get(f"/api/courses/{course}/learning-report").json()
        assert report["answered"]==1 and report["correct"]==1, report
        with connect(db) as connection:
            assert connection.execute("SELECT COUNT(*) FROM mastery_events").fetchone()[0]==0
            assert connection.execute("SELECT COUNT(*) FROM short_answer_evaluations").fetchone()[0]==1

        fresh=http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2}).json()["items"]
        short2=next(i for i in fresh if i["question_type"]=="short_answer")
        assert http.post(f"/api/quiz-items/{short2['quiz_item_id']}/answer",
            json={"client_request_id":"ai-short-300","response":"The source produces a signal."}).status_code==200
        ai_mark=http.post(f"/api/quiz-items/{short2['quiz_item_id']}/short-answer-review",
            json={"mode":"ai","provider":"default"})
        assert ai_mark.status_code==200,ai_mark.text
        assert ai_mark.json()["verdict"]=="uncertain"
        with connect(db) as connection:
            assert connection.execute("SELECT COUNT(*) FROM mastery_events").fetchone()[0]==0

def test_open_answer_and_multi_choice_validation():
    options={"A":"Alpha","B":"Bravo","C":"Charlie","D":"Delta"}
    assert validate_question_fields("multiple_choice","Which are valid options?",options,["A","C"],
          "The source explains these selections.",2) is None
    assert validate_question_fields("multiple_choice","Which are valid options?",options,["C","A"],
          "The source explains these selections.",2) is not None
    assert validate_question_fields("short_answer","Describe the network source.",{},
          "A detailed reference solution.","Explain the model and reasoning.",2) is None
    assert validate_question_fields("short_answer","Describe the network source.",{},
          "", "Explain the model and reasoning.",2) is not None

def test_schema_upgrade_retains_existing_question_versions(tmp_path):
    # Create a genuine legacy schema in a private SQLite database, then
    # migrate using the actual startup routine.
    from knowtrace.db import SCHEMA_PATH
    path=tmp_path/"legacy.sqlite3"
    old=SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "IN ('single_choice','multiple_choice','true_false','short_answer')",
        "IN ('single_choice','true_false')")
    with sqlite3.connect(str(path)) as connection:
        connection.executescript(old)
        connection.execute("INSERT INTO courses(id,title) VALUES ('legacy-course','Legacy')")
        connection.execute("INSERT INTO questions(id,course_id,origin) VALUES ('q1','legacy-course','ai')")
        connection.execute("""INSERT INTO question_versions
            (id,question_id,version_no,question_type,stem,options_json,
             correct_answer_json,explanation,difficulty,review_status)
            VALUES ('v1','q1',1,'single_choice','Old source question?',
              '{"A":"1","B":"2","C":"3","D":"4"}','"A"','Historical explanation.',1,'pending')""")
    initialize(path)
    initialize(path)
    with connect(path) as db:
        rows=db.execute("SELECT id,question_type,stem FROM question_versions").fetchall()
        assert len(rows)==1 and rows[0]["id"]=="v1"
        assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
        sql=db.execute("SELECT sql FROM sqlite_master WHERE name='question_versions'").fetchone()["sql"]
        assert "'short_answer'" in sql
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='question_versions'").fetchone()[0] > 0
