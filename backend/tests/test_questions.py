"""AI question bank: structure/evidence gating, review and immutable versions."""
from __future__ import annotations

import json
import sqlite3

import fitz
import pytest
from fastapi.testclient import TestClient

from knowtrace.ai import AIServiceError
from knowtrace.db import connect
from knowtrace.main import create_app
from knowtrace.questions import validate_generated_questions


SOURCE_QUOTE = "Signal source converts a message into an electrical signal"


class FakeQuestionProvider:
    text_model = "mock-questions"
    vision_model = ""

    def __init__(self, bad_json=False):
        self.bad_json = bad_json

    def extract(self, pages):
        return json.dumps({"items": [{
            "name": "Signal source", "summary": "Converts a message into an electrical signal.",
            "quote": SOURCE_QUOTE, "page": 1, "difficulty": 2,
        }]})

    def generate_questions(self, *, knowledge_name, knowledge_summary, sources, count, question_types):
        assert knowledge_name == "Signal source"
        assert len(sources) == 1 and SOURCE_QUOTE in sources[0]["content_text"]
        if self.bad_json:
            return "not json"
        chunk = sources[0]["id"]
        return json.dumps({"items": [
            {"question_type": "single_choice", "stem": "What does a source do in a data communication system?",
             "options": {"A": "Generate the electrical signal", "B": "Encrypt hard drives",
                         "C": "Route IP packets", "D": "Discard all data"},
             "correct_answer": "A", "explanation": "The source converts the message to a signal.",
             "difficulty": 2, "source_chunk_id": chunk,
             "quote": "Invented source excerpt that is not in the lecture"},
            {"question_type": "single_choice", "stem": "What does a source do in a data communication system?",
             "options": {"A": "Generate the electrical signal", "B": "Encrypt hard drives",
                         "C": "Route IP packets", "D": "Discard all data"},
             "correct_answer": "A", "explanation": "The source converts the message to a signal.",
             "difficulty": 2, "source_chunk_id": chunk, "quote": SOURCE_QUOTE},
            {"question_type": "true_false", "stem": "A signal source creates an electrical signal from a message.",
             "options": {}, "correct_answer": True,
             "explanation": "The provided text describes the source converting the message.",
             "difficulty": 1, "source_chunk_id": chunk, "quote": SOURCE_QUOTE},
        ]}, ensure_ascii=False)


def make_test_course(http):
    r = http.post("/api/courses", json={"title": "Computer Networks"})
    assert r.status_code == 201
    return r.json()["id"]


def prepare_approved_knowledge(http):
    course = make_test_course(http)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((40, 70), SOURCE_QUOTE)
    page.insert_text((40, 110), "The sender and receiver are part of a communication system.")
    result = http.post(f"/api/courses/{course}/documents", files={"file": ("lecture.pdf", pdf.tobytes(), "application/pdf")})
    assert result.status_code == 201, result.text
    did = result.json()["id"]
    kps = http.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 1})
    assert kps.status_code == 200, kps.text
    kp_id = kps.json()["created_ids"][0]
    assert http.post(f"/api/knowledge-points/{kp_id}/review", json={"decision": "approved"}).status_code == 200
    return course, did, kp_id


@pytest.fixture
def setup(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    app = create_app(db_path=db_path, storage_dir=tmp_path / "uploads", ai_provider=FakeQuestionProvider())
    with TestClient(app) as http:
        yield http, db_path


def test_generate_grounded_pending_questions(setup):
    http, _ = setup
    course, doc, kp = prepare_approved_knowledge(http)
    r = http.post(f"/api/knowledge-points/{kp}/generate-questions", json={"count": 2})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] == 2
    assert data["review_status"] == "pending"
    assert len(data["warnings"]) == 1 and "source quotation" in data["warnings"][0]
    all_rows = http.get(f"/api/courses/{course}/questions").json()
    assert len(all_rows) == 2
    assert all(row["review_status"] == "pending" for row in all_rows)
    assert {row["question_type"] for row in all_rows} == {"single_choice", "true_false"}
    single = http.get(f"/api/question-versions/{data['question_version_ids'][0]}").json()
    assert single["options"] == {"A": "Generate the electrical signal", "B": "Encrypt hard drives",
                                 "C": "Route IP packets", "D": "Discard all data"}
    assert single["correct_answer"] == "A"
    assert single["sources"][0]["document_id"] == doc
    assert single["sources"][0]["page_start"] == 1
    assert single["knowledge_points"][0]["knowledge_point_id"] == kp
    assert single["knowledge_points"][0]["is_primary"] == 1
    tf = http.get(f"/api/question-versions/{data['question_version_ids'][1]}").json()
    assert tf["correct_answer"] is True
    assert http.get(f"/api/courses/{course}/questions", params={"status": "approved"}).json() == []
    assert http.get(f"/api/courses/{course}/questions", params={"status": "pending"}).status_code == 200
    # Running again should not create more copies of the same question stems.
    again = http.post(f"/api/knowledge-points/{kp}/generate-questions", json={"count": 2})
    assert again.status_code == 200
    assert again.json()["created"] == 0
    assert len(http.get(f"/api/courses/{course}/questions").json()) == 2


def test_approve_revise_keeps_approved_version_immutable(setup):
    http, db_path = setup
    course, _, kp = prepare_approved_knowledge(http)
    output = http.post(f"/api/knowledge-points/{kp}/generate-questions", json={"count": 1}).json()
    v1 = output["question_version_ids"][0]
    approved = http.post(f"/api/question-versions/{v1}/review", json={"decision": "approved"})
    assert approved.status_code == 200
    assert http.get(f"/api/question-versions/{v1}").json()["approved_at"]
    assert http.post(f"/api/question-versions/{v1}/review", json={"decision": "rejected"}).status_code == 409
    with connect(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE question_versions SET stem=? WHERE id=?", ("overwritten", v1))
    revised = http.post(f"/api/question-versions/{v1}/revise", json={
        "stem": "What does the signal source convert into an original signal?"})
    assert revised.status_code == 201, revised.text
    v2 = revised.json()["id"]
    assert revised.json()["version_no"] == 2 and revised.json()["review_status"] == "pending"
    assert revised.json()["sources"] == http.get(f"/api/question-versions/{v1}").json()["sources"]
    assert revised.json()["knowledge_points"] == http.get(f"/api/question-versions/{v1}").json()["knowledge_points"]
    assert http.get(f"/api/question-versions/{v1}").json()["review_status"] == "approved"
    assert http.post(f"/api/question-versions/{v1}/revise", json={"stem": "Any sufficiently long stem"}).status_code == 409
    assert http.post(f"/api/question-versions/{v2}/review", json={"decision": "approved"}).status_code == 200
    history = http.get(f"/api/questions/{revised.json()['question_id']}/versions").json()
    assert [v["version_no"] for v in history] == [1, 2]
    listed = http.get(f"/api/courses/{course}/questions").json()
    assert listed[0]["version_id"] == v2
    assert listed[0]["version_no"] == 2


def test_reject_and_require_new_revision(setup):
    http, _ = setup
    _, _, kp = prepare_approved_knowledge(http)
    v = http.post(f"/api/knowledge-points/{kp}/generate-questions", json={"count": 1}).json()["question_version_ids"][0]
    assert http.post(f"/api/question-versions/{v}/review", json={"decision": "rejected"}).status_code == 200
    assert http.post(f"/api/question-versions/{v}/review", json={"decision": "approved"}).status_code == 409
    revised = http.post(f"/api/question-versions/{v}/revise", json={"explanation": "The lecture describes message-to-signal conversion."})
    assert revised.status_code == 201
    assert revised.json()["review_status"] == "pending"


def test_validation_rejects_unsafe_structures():
    src = {"chunk1": {"content_text": SOURCE_QUOTE}}
    good = {"question_type": "single_choice", "stem": "What does a source convert into a signal?",
            "options": {"A": "Messages", "B": "Phone lines", "C": "Screens", "D": "Tables"},
            "correct_answer": "A", "explanation": "It converts a message into a signal.",
            "difficulty": 1, "source_chunk_id": "chunk1", "quote": SOURCE_QUOTE}
    bads = [
        {**good, "options": {"A": "Same", "B": "Same", "C": "Other", "D": "Fourth"}},
        {**good, "correct_answer": "E"},
        {**good, "source_chunk_id": "unknown"},
        {**good, "difficulty": 4},
        {**good, "quote": "invented quotation that does not occur"},
        {**good, "question_type": "essay"},
        {**good, "question_type": "true_false", "options": {}, "correct_answer": "true"},
    ]
    items, warnings = validate_generated_questions(json.dumps({"items": bads+[good]}), src,
                            permitted_types={"single_choice", "true_false"}, count=2)
    assert len(items) == 1 and len(warnings) == len(bads)
    with pytest.raises(AIServiceError):
        validate_generated_questions("not json",src,permitted_types={"single_choice"},count=2)
    with pytest.raises(AIServiceError):
        validate_generated_questions('{"results":[]}',src,permitted_types={"single_choice"},count=2)


def test_permissions_and_validation(setup):
    http, _ = setup
    cid = make_test_course(http)
    kp = http.post(f"/api/courses/{cid}/knowledge-points",json={"canonical_name":"Unlinked"}).json()["id"]
    assert http.post(f"/api/knowledge-points/{kp}/generate-questions",json={}).status_code == 422
    assert http.post("/api/knowledge-points/invalid/generate-questions",json={}).status_code == 404
    course, doc, good_kp = prepare_approved_knowledge(http)
    assert http.post(f"/api/knowledge-points/{good_kp}/generate-questions",json={"count":5}).status_code == 422
    assert http.post(f"/api/knowledge-points/{good_kp}/generate-questions",json={"question_types":["single_choice","single_choice"]}).status_code == 422
    assert http.post(f"/api/knowledge-points/{good_kp}/generate-questions",json={"question_types":["essay"]}).status_code == 422
    assert http.get(f"/api/courses/{course}/questions",params={"status":"wrong"}).status_code == 422
    assert http.get("/api/question-versions/invalid").status_code == 404
    assert http.get("/api/questions/invalid/versions").status_code == 404
    pending = http.post(f"/api/documents/{doc}/extract-knowledge",json={"page_start":1}).json()
    assert pending["reused"] == 1
    assert http.patch(f"/api/knowledge-points/{good_kp}",json={"summary":"Changed summary."}).status_code == 200
    assert http.post(f"/api/knowledge-points/{good_kp}/generate-questions",json={}).status_code == 409


def test_bad_model_response_is_no_questions(tmp_path):
    db = tmp_path / "err.sqlite3"
    app = create_app(db_path=db,storage_dir=tmp_path/"uploads",ai_provider=FakeQuestionProvider(bad_json=True))
    with TestClient(app) as http:
        course, _, kp = prepare_approved_knowledge(http)
        r = http.post(f"/api/knowledge-points/{kp}/generate-questions", json={"count":2})
        assert r.status_code == 502
        assert http.get(f"/api/courses/{course}/questions").json() == []
        with connect(db) as cx:
            job = cx.execute("SELECT status FROM processing_jobs WHERE job_type='question_generate'").fetchone()
            assert job["status"] == "failed"


def test_question_one_click_approve_respects_risk_and_audits(setup):
    http, db_path = setup
    course, _, kp = prepare_approved_knowledge(http)
    response = http.post(f"/api/knowledge-points/{kp}/generate-questions",
                         json={"count":2})
    assert response.status_code == 200, response.text
    ids = response.json()["question_version_ids"]
    preview = http.get(f"/api/courses/{course}/questions/bulk-approval-preview")
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["total"] == 2 and data["eligible"] == 2
    assert all("correct_answer" not in row and "explanation" not in row
               for row in data["questions"])
    with connect(db_path) as db:
        db.execute("UPDATE question_sources SET evidence_note=? WHERE question_version_id=?",
                   ("unverified model text, inspect original PDF", ids[1]))
        db.commit()
    changed = http.get(f"/api/courses/{course}/questions/bulk-approval-preview").json()
    assert changed["eligible"] == 1 and changed["flagged"] == 1
    assert next(q["issues"] for q in changed["questions"] if q["id"] == ids[1])
    route = f"/api/courses/{course}/questions/bulk-approve"
    assert http.post(route, json={
        "confirmed":False, "accept_unverified_answers":True,
        "question_version_ids":ids}).status_code == 422
    assert http.post(route, json={
        "confirmed":True, "accept_unverified_answers":False,
        "question_version_ids":ids}).status_code == 422
    applied = http.post(route, json={
        "confirmed":True, "accept_unverified_answers":True,
        "question_version_ids":ids+ids})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == [ids[0]]
    assert len(applied.json()["skipped"]) == 1
    assert http.get(f"/api/question-versions/{ids[0]}").json()["review_status"] == "approved"
    assert http.get(f"/api/question-versions/{ids[1]}").json()["review_status"] == "pending"
    with connect(db_path) as db:
        events = db.execute("SELECT * FROM question_bulk_review_events").fetchall()
        assert len(events) == 1
        assert events[0]["question_version_id"] == ids[0]
        assert json.loads(events[0]["details_json"])["correct_answer_verified"] is False
    # Repeating the exact same request must not double-count previously approved items.
    repeated = http.post(route, json={
        "confirmed":True, "accept_unverified_answers":True,
        "question_version_ids":ids})
    assert repeated.status_code == 200
    assert repeated.json()["applied"] == []
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM question_bulk_review_events").fetchone()[0] == 1


def test_question_bulk_approve_blocks_cross_course_and_stale_versions(setup):
    http, db_path = setup
    course_a, _, kp_a = prepare_approved_knowledge(http)
    course_b, _, kp_b = prepare_approved_knowledge(http)
    older = http.post(f"/api/knowledge-points/{kp_a}/generate-questions",
                      json={"count":1}).json()["question_version_ids"][0]
    foreign = http.post(f"/api/knowledge-points/{kp_b}/generate-questions",
                        json={"count":1}).json()["question_version_ids"][0]
    revised = http.post(f"/api/question-versions/{older}/revise",
                        json={"stem":"What is the job of the original sender?"})
    assert revised.status_code == 201, revised.text
    newest = revised.json()["id"]
    preview = http.get(f"/api/courses/{course_a}/questions/bulk-approval-preview").json()
    assert [r["id"] for r in preview["questions"]] == [newest]
    attempted = http.post(f"/api/courses/{course_a}/questions/bulk-approve",json={
        "confirmed":True,"accept_unverified_answers":True,
        "question_version_ids":[older,foreign,newest]})
    assert attempted.status_code == 200, attempted.text
    assert attempted.json()["applied"] == [newest]
    assert len(attempted.json()["skipped"]) == 2
    assert http.get(f"/api/question-versions/{older}").json()["review_status"] == "pending"
    assert http.get(f"/api/question-versions/{foreign}").json()["review_status"] == "pending"
    assert http.get(f"/api/question-versions/{newest}").json()["review_status"] == "approved"
    assert http.get("/api/courses/unknown/questions/bulk-approval-preview").status_code == 404
