"""Knowledge point edit/review workflows, provenance, and history regression tests."""
from __future__ import annotations

import fitz
import pytest
from fastapi.testclient import TestClient

from knowtrace.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.sqlite3", storage_dir=tmp_path / "uploads")
    with TestClient(app) as http:
        yield http


def new_course(client, title="Networks"):
    result = client.post("/api/courses", json={"title": title})
    assert result.status_code == 201
    return result.json()["id"]


def test_edit_reopens_approval_and_tracks_changes(client):
    cid = new_course(client)
    chapter = client.post(f"/api/courses/{cid}/chapters", json={"title": "Data communication"}).json()["id"]
    kp = client.post(f"/api/courses/{cid}/knowledge-points", json={
        "canonical_name": "Data communication model", "summary": "Original explanation", "difficulty": 2}).json()["id"]
    before = client.get(f"/api/knowledge-points/{kp}").json()
    assert before["review_status"] == "approved" and before["sources"] == []
    assert client.patch(f"/api/knowledge-points/{kp}", json={}).status_code == 422
    changed = client.patch(f"/api/knowledge-points/{kp}", json={
        "canonical_name": "  Communication system model  ",
        "summary": "Source, transmission system and destination system",
        "difficulty": 1, "chapter_id": chapter})
    assert changed.status_code == 200, changed.text
    record = changed.json()
    assert record["canonical_name"] == "Communication system model"
    assert record["summary"].startswith("Source")
    assert record["difficulty"] == 1 and record["chapter_id"] == chapter
    assert record["review_status"] == "pending"
    by_course = client.get(f"/api/courses/{cid}/knowledge-points").json()
    assert by_course[0]["review_status"] == "pending"
    assert client.post(f"/api/knowledge-points/{kp}/review", json={"decision": "approved"}).status_code == 200
    assert client.get(f"/api/knowledge-points/{kp}").json()["review_status"] == "approved"
    # Idempotent reviews and no-op changes should not create audit records.
    client.post(f"/api/knowledge-points/{kp}/review", json={"decision": "approved"})
    client.patch(f"/api/knowledge-points/{kp}", json={"summary": record["summary"]})
    history = client.get(f"/api/knowledge-points/{kp}/history").json()
    assert [entry["action"] for entry in history] == ["edited", "approved"]
    assert history[0]["before"]["summary"] == "Original explanation"
    assert history[0]["after"]["review_status"] == "pending"
    assert history[1]["before"]["review_status"] == "pending"
    assert history[1]["after"]["review_status"] == "approved"
    assert client.post(f"/api/knowledge-points/{kp}/review", json={"decision": "rejected"}).status_code == 200
    assert client.patch(f"/api/knowledge-points/{kp}", json={"summary": "Revised explanation"}).json()["review_status"] == "pending"
    assert [entry["action"] for entry in client.get(f"/api/knowledge-points/{kp}/history").json()] == ["edited", "approved", "rejected", "edited"]


def test_invalid_edits_are_atomic(client):
    course_a, course_b = new_course(client, "A"), new_course(client, "B")
    other_chapter = client.post(f"/api/courses/{course_b}/chapters", json={"title": "Not our course"}).json()["id"]
    id_a = client.post(f"/api/courses/{course_a}/knowledge-points", json={"canonical_name": "OSI Layer"}).json()["id"]
    client.post(f"/api/courses/{course_a}/knowledge-points", json={"canonical_name": "TCP"})
    cases = [
        ({"canonical_name": None}, 422),
        ({"canonical_name": "   "}, 422),
        ({"summary": "  "}, 422),
        ({"difficulty": 0}, 422),
        ({"difficulty": 4}, 422),
        ({"chapter_id": ""}, 422),
        ({"chapter_id": other_chapter}, 422),
        ({"canonical_name": " t c p "}, 409),
        ({"unexpected": "field"}, 422),
    ]
    for body, expected in cases:
        resp = client.patch(f"/api/knowledge-points/{id_a}", json=body)
        assert resp.status_code == expected, (body, resp.text)
    assert client.get(f"/api/knowledge-points/{id_a}").json()["canonical_name"] == "OSI Layer"
    assert client.get(f"/api/knowledge-points/{id_a}/history").json() == []
    assert client.patch("/api/knowledge-points/missing", json={"summary": "description"}).status_code == 404
    assert client.get("/api/knowledge-points/missing").status_code == 404
    assert client.get("/api/knowledge-points/missing/history").status_code == 404
    assert client.post("/api/knowledge-points/missing/review", json={"decision": "approved"}).status_code == 404


class FakeProvider:
    text_model = "mock"
    vision_model = ""

    def extract(self, pages):
        assert 1 in pages
        return '{"items":[{"name":"Signal source","summary":"A signal source creates an original electrical signal.","quote":"Signal source converts the message into an original electrical signal","page":1,"difficulty":2}]}'


def test_edit_preserves_ai_evidence_and_review(client, tmp_path):
    app = create_app(db_path=tmp_path / "ai.sqlite3", storage_dir=tmp_path / "ai-uploads", ai_provider=FakeProvider())
    with TestClient(app) as http:
        cid = new_course(http)
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((20, 55), "Signal source converts the message into an original electrical signal")
        page.insert_text((20, 85), "Transmission system sends the data to its destination")
        upload = http.post(f"/api/courses/{cid}/documents", files={"file": ("lecture.pdf", pdf.tobytes(), "application/pdf")})
        assert upload.status_code == 201, upload.text
        doc_id = upload.json()["id"]
        result = http.post(f"/api/documents/{doc_id}/extract-knowledge", json={"page_start": 1})
        assert result.status_code == 200, result.text
        kp = result.json()["created_ids"][0]
        before = http.get(f"/api/knowledge-points/{kp}").json()
        assert before["review_status"] == "pending"
        assert before["sources"][0]["document_id"] == doc_id
        assert before["sources"][0]["page_start"] == 1
        evidence = before["sources"][0]["evidence_note"]
        changed = http.patch(f"/api/knowledge-points/{kp}", json={"summary": "The source converts a message into an electrical signal."})
        assert changed.status_code == 200
        assert changed.json()["review_status"] == "pending"
        assert changed.json()["sources"][0]["evidence_note"] == evidence
        assert http.post(f"/api/knowledge-points/{kp}/review", json={"decision": "approved"}).status_code == 200
        after = http.get(f"/api/documents/{doc_id}/knowledge-points").json()
        assert after[0]["summary"] == "The source converts a message into an electrical signal."
        assert after[0]["source_kind"] == "text" and after[0]["review_status"] == "approved"
        assert [e["action"] for e in http.get(f"/api/knowledge-points/{kp}/history").json()] == ["edited", "approved"]
