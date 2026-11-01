"""End-to-end tests of quiz records, BKT, and published question versions."""
import json
import fitz
import pytest
from fastapi.testclient import TestClient
from knowtrace.main import create_app
from knowtrace.db import connect

QUOTE = "The source sends a signal to the transmission system"


class FakeAI:
    text_model = "mock"
    vision_model = ""

    def extract(self, pages):
        return json.dumps({"items": [{"name": "Source system", "summary": "Source sends a signal.",
            "quote": QUOTE, "page": 1, "difficulty": 2}]})

    def generate_questions(self, *, knowledge_name, knowledge_summary, sources, count, question_types):
        src = sources[0]["id"]
        return json.dumps({"items": [
            {"question_type": "single_choice", "stem": "Which component sends the signal to the network?",
             "options": {"A":"Source", "B":"Receiver", "C":"Monitor", "D":"Printer"},
             "correct_answer":"A", "explanation":"The source sends the signal.",
             "difficulty":1,"source_chunk_id":src,"quote":QUOTE},
            {"question_type":"true_false", "stem":"The source sends a signal to the transmission system.",
             "options":{},"correct_answer":True,"explanation":"Source sends the signal.",
             "difficulty":1,"source_chunk_id":src,"quote":QUOTE}]})


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "test.sqlite3"
    with TestClient(create_app(db_path=db, storage_dir=tmp_path / "uploads", ai_provider=FakeAI())) as http:
        yield http, db


def seed(http):
    course = http.post("/api/courses", json={"title":"Networks"}).json()["id"]
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((25,60), QUOTE)
    page.insert_text((25,100), "Transmission and reception of signals are important in communication.")
    upload = http.post(f"/api/courses/{course}/documents",files={"file":("notes.pdf",pdf.tobytes(),"application/pdf")})
    assert upload.status_code == 201
    did = upload.json()["id"]
    extracted = http.post(f"/api/documents/{did}/extract-knowledge",json={"page_start":1})
    assert extracted.status_code == 200,extracted.text
    kp = extracted.json()["created_ids"][0]
    assert http.post(f"/api/knowledge-points/{kp}/generate-questions",json={}).status_code == 409
    http.post(f"/api/knowledge-points/{kp}/review",json={"decision":"approved"})
    generated = http.post(f"/api/knowledge-points/{kp}/generate-questions",json={"count":2})
    assert generated.status_code == 200,generated.text
    versions = generated.json()["question_version_ids"]
    assert len(versions) == 2
    return course,kp,versions


def approve(http,versions):
    for vid in versions:
        assert http.post(f"/api/question-versions/{vid}/review",json={"decision":"approved"}).status_code == 200


def test_quiz_answer_and_bkt_report(env):
    http,db = env
    course,kp,versions = seed(http)
    assert http.post(f"/api/courses/{course}/quiz-sessions",json={"count":1}).status_code == 409
    approve(http,versions)
    started = http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2})
    assert started.status_code == 201,started.text
    session = started.json()
    assert session["status"] == "active" and session["answered"] == 0
    assert len(session["items"]) == 2
    for item in session["items"]:
        assert "result" not in item and "correct_answer" not in item and "explanation" not in item
    single = next(i for i in session["items"] if i["question_type"] == "single_choice")
    boolean = next(i for i in session["items"] if i["question_type"] == "true_false")
    assert http.post(f"/api/quiz-items/{single['quiz_item_id']}/answer",json={
        "client_request_id":"bad-type", "response":True}).status_code == 422
    correct = http.post(f"/api/quiz-items/{single['quiz_item_id']}/answer",json={
        "client_request_id":"first-answer-111", "response":"A", "elapsed_ms":1200})
    assert correct.status_code == 200,correct.text
    assert correct.json()["is_correct"] and correct.json()["mastery"]["before"] == pytest.approx(.2)
    assert correct.json()["mastery"]["after"] > .2
    replay = http.post(f"/api/quiz-items/{single['quiz_item_id']}/answer",json={
        "client_request_id":"first-answer-111", "response":"A"})
    assert replay.status_code == 200 and replay.json()["answer_id"] == correct.json()["answer_id"]
    assert http.post(f"/api/quiz-items/{single['quiz_item_id']}/answer",json={
        "client_request_id":"second-answer-111", "response":"B"}).status_code == 409
    wrong = http.post(f"/api/quiz-items/{boolean['quiz_item_id']}/answer",json={
        "client_request_id":"second-answer-222", "response":False})
    assert wrong.status_code == 200 and wrong.json()["is_correct"] is False
    assert wrong.json()["mastery"]["before"] == pytest.approx(correct.json()["mastery"]["after"])
    assert http.get(f"/api/quiz-sessions/{session['id']}").json()["status"] == "completed"
    report = http.get(f"/api/courses/{course}/learning-report").json()
    assert report["answered"] == 2 and report["correct"] == 1 and report["accuracy"] == .5
    point = next(x for x in report["knowledge_points"] if x["knowledge_point_id"] == kp)
    assert point["observations"] == 2 and point["accuracy"] == .5
    assert point["p_mastery"] == pytest.approx(wrong.json()["mastery"]["after"])
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM answer_records").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM mastery_events").fetchone()[0] == 2


def test_latest_approval_controls_selection(env):
    http,_ = env
    course,kp,versions = seed(http)
    approve(http,versions)
    updated = http.post(f"/api/question-versions/{versions[0]}/revise",json={
        "stem":"Which component generates the outgoing signal?"})
    assert updated.status_code == 201
    v2 = updated.json()["id"]
    one = http.post(f"/api/courses/{course}/quiz-sessions",json={"count":1})
    assert one.status_code == 201
    assert one.json()["items"][0]["question_version_id"] == versions[1]
    assert http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2}).status_code == 409
    http.post(f"/api/question-versions/{v2}/review",json={"decision":"approved"})
    assert http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2}).status_code == 201
    http.patch(f"/api/knowledge-points/{kp}",json={"summary":"New explanation"})
    assert http.post(f"/api/courses/{course}/quiz-sessions",json={"count":1}).status_code == 409


def test_duplicate_keys_types_and_missing_resources(env):
    http,_ = env
    course,_,versions = seed(http)
    approve(http,versions)
    items = http.post(f"/api/courses/{course}/quiz-sessions",json={"count":2}).json()["items"]
    one = next(i for i in items if i["question_type"] == "single_choice")
    two = next(i for i in items if i["question_type"] == "true_false")
    url = f"/api/quiz-items/{one['quiz_item_id']}/answer"
    assert http.post(url,json={"client_request_id":"key-1111111","response":"B"}).status_code == 200
    assert http.post(url,json={"client_request_id":"key-1111111","response":"A"}).status_code == 409
    other = f"/api/quiz-items/{two['quiz_item_id']}/answer"
    assert http.post(other,json={"client_request_id":"key-1111111","response":True}).status_code == 409
    assert http.post(other,json={"client_request_id":"key-2222222","response":"true"}).status_code == 422
    assert http.post(other,json={"client_request_id":"key-2222222","response":True}).status_code == 200
    assert http.post(other,json={"client_request_id":"key-3333333","response":False}).status_code == 409
    assert http.get("/api/quiz-sessions/missing").status_code == 404
    assert http.post("/api/quiz-items/missing/answer",json={"client_request_id":"key-4444444","response":"A"}).status_code == 404
    assert http.get("/api/courses/missing/learning-report").status_code == 404
    # Adaptive is now a supported, single-concept diagnosis mode.
    adaptive=http.post(f"/api/courses/{course}/quiz-sessions",
                       json={"mode":"adaptive","count":1})
    assert adaptive.status_code == 201, adaptive.text
    assert adaptive.json()["mode"] == "adaptive"
    assert http.post(f"/api/courses/{course}/quiz-sessions",
                     json={"mode":"unsupported"}).status_code == 422
    assert http.post(f"/api/courses/{course}/quiz-sessions",json={"count":0}).status_code == 422


def test_persists_after_restart(tmp_path):
    db = tmp_path / "persistent.sqlite3"
    uploads = tmp_path / "uploads"
    with TestClient(create_app(db_path=db,storage_dir=uploads,ai_provider=FakeAI())) as http:
        first = http.post("/api/courses",json={"title":"Empty"}).json()["id"]
        assert http.get(f"/api/courses/{first}/learning-report").json()["accuracy"] is None
        assert http.post(f"/api/courses/{first}/quiz-sessions",json={"count":1}).status_code == 409
        course,_,versions = seed(http)
        approve(http,versions)
        session = http.post(f"/api/courses/{course}/quiz-sessions",json={"count":1}).json()
        item = session["items"][0]
        answer = "A" if item["question_type"] == "single_choice" else True
        submitted = http.post(f"/api/quiz-items/{item['quiz_item_id']}/answer",json={
            "client_request_id":"persisted-key", "response":answer})
        assert submitted.status_code == 200,submitted.text
        sid = session["id"]
    with TestClient(create_app(db_path=db,storage_dir=uploads,ai_provider=FakeAI())) as http:
        assert http.get(f"/api/quiz-sessions/{sid}").json()["status"] == "completed"
        report = http.get(f"/api/courses/{course}/learning-report").json()
        assert report["answered"] == 1 and report["knowledge_points"][0]["observations"] == 1

def test_pending_blind_trial_does_not_spoil_answers_or_change_mastery(env):
    http, db = env
    course, kp, versions = seed(http)  # generated candidates are pending
    assert http.post(f"/api/courses/{course}/quiz-sessions",
                     json={"count":1}).status_code == 409
    assert http.post(f"/api/courses/{course}/quiz-sessions",
                     json={"count":1,"mode":"adaptive","source":"pending"}).status_code == 422
    created = http.post(f"/api/courses/{course}/quiz-sessions",
                        json={"count":2,"source":"pending","mode":"normal"})
    assert created.status_code == 201, created.text
    quiz = created.json()
    assert quiz["blind_pending"] is True
    assert quiz["correct"] is None
    assert quiz["answered"] == 0
    assert len(quiz["items"]) == 2
    assert all(item["provisional"] and not item["answered"] for item in quiz["items"])
    assert all("correct_answer" not in item and "result" not in item
               for item in quiz["items"])
    first, second = quiz["items"]
    first_answer = http.post(f"/api/quiz-items/{first['quiz_item_id']}/answer",
       json={"client_request_id":"blind-first-1000","response":
             "A" if first["question_type"]=="single_choice" else True})
    assert first_answer.status_code == 200, first_answer.text
    assert first_answer.json()["deferred"] is True
    assert "correct_answer" not in first_answer.json()
    assert "is_correct" not in first_answer.json()
    mid = http.get(f"/api/quiz-sessions/{quiz['id']}").json()
    assert mid["correct"] is None and mid["status"] == "active"
    assert mid["answered"] == 1
    assert mid["items"][0]["answered"]
    assert "result" not in mid["items"][0]
    assert mid["items"][0]["submitted_response"] == (
        "A" if first["question_type"]=="single_choice" else True)
    assert http.get(f"/api/courses/{course}/learning-report").json()["answered"] == 0
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mastery_events").fetchone()[0] == 0
    final = http.post(f"/api/quiz-items/{second['quiz_item_id']}/answer",
       json={"client_request_id":"blind-last-1000","response":
             "A" if second["question_type"]=="single_choice" else True})
    assert final.status_code == 200, final.text
    assert final.json()["diagnostic"] is False
    complete = http.get(f"/api/quiz-sessions/{quiz['id']}").json()
    assert complete["blind_pending"] and complete["status"] == "completed"
    assert complete["correct"] is not None
    assert all("correct_answer" in item["result"] for item in complete["items"])
    official = http.get(f"/api/courses/{course}/learning-report").json()
    assert official["answered"] == 0
    assert official["provisional_answers"] == 2
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mastery_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mastery_states").fetchone()[0] == 0

