"""API regression tests on an isolated temporary SQLite database."""
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from knowtrace.bkt import BKTParameters, update_mastery
from knowtrace.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.sqlite3", storage_dir=tmp_path / "uploads")
    with TestClient(app) as instance:
        yield instance


def make_course(client, title="Computer Networks"):
    response = client.post("/api/courses", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_and_courses(client):
    assert client.get("/health").json()["status"] == "ok"
    cid = make_course(client)
    assert client.get("/api/courses").json()[0]["id"] == cid
    assert client.get(f"/api/courses/{cid}").json()["title"] == "Computer Networks"
    assert client.get("/api/courses/unknown").status_code == 404


def test_chapter_and_knowledge_point_course_integrity(client):
    cid, other = make_course(client), make_course(client, "Other")
    chapter = client.post(f"/api/courses/{cid}/chapters", json={"title": "Transport", "ordinal": 7}).json()
    assert chapter["id"]
    bad = client.post(f"/api/courses/{other}/knowledge-points", json={"canonical_name": "TCP", "chapter_id": chapter["id"]})
    assert bad.status_code == 422
    good = client.post(f"/api/courses/{cid}/knowledge-points", json={"canonical_name": "TCP", "chapter_id": chapter["id"]})
    assert good.status_code == 201
    assert client.get(f"/api/courses/{cid}/knowledge-points").json()[0]["canonical_name"] == "TCP"


def test_pdf_upload_and_page_provenance(client):
    cid = make_course(client)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((70, 70), "TCP handshake relies on sequence numbers")
    page = pdf.new_page()
    page.insert_text((70, 70), "UDP is a transport protocol")
    content = pdf.tobytes()
    response = client.post(f"/api/courses/{cid}/documents", files={"file": ("lecture.pdf", content, "application/pdf")})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["total_pages"] == 2 and data["chunks"] == 2
    chunks = client.get(f"/api/documents/{data['id']}/chunks").json()
    assert chunks[0]["page_start"] == 1 and "TCP handshake" in chunks[0]["content_text"]
    assert chunks[1]["page_start"] == 2 and "UDP" in chunks[1]["content_text"]


def test_pptx_upload(client):
    cid = make_course(client)
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[5])
    slide.shapes.title.text = "OSI seven-layer architecture"
    out = BytesIO()
    pres.save(out)
    response = client.post(f"/api/courses/{cid}/documents", files={"file": ("network.pptx", out.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
    assert response.status_code == 201, response.text
    assert response.json()["chunks"] >= 1


def test_reject_unsupported_and_fake_pdf(client):
    cid = make_course(client)
    bad_ext = client.post(f"/api/courses/{cid}/documents", files={"file": ("sample.txt", b"text", "text/plain")})
    assert bad_ext.status_code == 415
    invalid = client.post(f"/api/courses/{cid}/documents", files={"file": ("sample.pdf", b"fake", "application/pdf")})
    assert invalid.status_code == 422


def test_bkt_update():
    params = BKTParameters()
    posterior_good, after_good = update_mastery(0.2, True, params)
    posterior_bad, after_bad = update_mastery(0.2, False, params)
    assert 0 < posterior_bad < posterior_good < 1
    assert after_good > posterior_good and after_bad > posterior_bad
    with pytest.raises(ValueError):
        update_mastery(1.1, True)


def test_quality_flags_sparse_diagram_and_page_preview(client):
    cid = make_course(client)
    pdf = fitz.open()
    first = pdf.new_page()
    first.insert_text((70, 70), "Network protocol architecture: " + "layered architecture " * 8)
    second = pdf.new_page()
    second.insert_text((70, 70), "2.1 Communication Model")
    second.draw_rect(fitz.Rect(70, 100, 480, 400), fill=(0.7, 0.8, 0.9))
    third = pdf.new_page()  # blank page still must be reported
    response = client.post(f"/api/courses/{cid}/documents", files={"file": ("diagram.pdf", pdf.tobytes(), "application/pdf")})
    assert response.status_code == 201
    doc_id = response.json()["id"]
    quality = client.get(f"/api/documents/{doc_id}/quality")
    assert quality.status_code == 200
    result = quality.json()
    assert result["total_pages"] == 3
    assert result["pages_needing_review"] == 2
    assert result["pages"][0]["needs_review"] is False
    assert result["pages"][1]["reason"] == "low_text"
    assert result["pages"][2]["reason"] == "no_text"
    preview = client.get(f"/api/documents/{doc_id}/pages/2/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.content.startswith(b"\x89PNG")
    assert client.get(f"/api/documents/{doc_id}/pages/4/preview").status_code == 404
    assert client.get(f"/api/documents/{doc_id}/quality").status_code == 200
    assert client.get("/api/documents/not-existing/quality").status_code == 404


def test_pptx_quality_and_no_preview(client):
    cid = make_course(client)
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[5])
    slide.shapes.title.text = "Ethernet"
    output = BytesIO()
    pres.save(output)
    uploaded = client.post(f"/api/courses/{cid}/documents", files={"file": ("slides.pptx", output.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
    assert uploaded.status_code == 201
    did = uploaded.json()["id"]
    assert client.get(f"/api/documents/{did}/quality").json()["pages"][0]["reason"] == "low_text"
    assert client.get(f"/api/documents/{did}/pages/1/preview").status_code == 415


class FakeAI:
    text_model = "mock-text"
    vision_model = "mock-vision"

    def __init__(self):
        self.calls = []

    def extract(self, pages):
        self.calls.append({"type": "extract", "pages": pages})
        import json
        # The test page explicitly includes the quotation.
        if 1 in pages and "DATA COMMUNICATION" in pages[1]:
            return json.dumps({"items": [
                {"name": "Data communication model", "summary": "System model and roles in a communication system.",
                 "page": 1, "quote": "DATA COMMUNICATION SYSTEM includes sender channel and receiver", "difficulty": 2},
                {"name": "Hallucinated concept", "summary": "This has no evidence in provided document.",
                 "page": 1, "quote": "This nonexistent excerpt is not in the uploaded course material", "difficulty": 1},
            ]})
        return json.dumps({"items": [{"name": "Source system", "summary": "Diagram contains a source system and a destination.",
                                     "page": 2, "quote": "source system includes source and transmitter", "difficulty": 1}]})

    def describe_visual(self, image_b64):
        self.calls.append({"type": "vision", "bytes": len(image_b64)})
        return "Diagram: source system includes source and transmitter; channel leads to destination system. " * 3


def test_extract_knowledge_grounded_pending_review_and_dedup(tmp_path):
    import json
    ai = FakeAI()
    app = create_app(db_path=tmp_path / "test.sqlite3", storage_dir=tmp_path / "uploads", ai_provider=ai)
    with TestClient(app) as client:
        cid = make_course(client)
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((30, 80), "DATA COMMUNICATION SYSTEM includes sender channel and receiver")
        page.insert_text((30, 110), "DATA COMMUNICATION is the transfer of data through a channel, for example by a transmitter.")
        page = pdf.new_page()
        page.insert_text((30, 80), "2.1 Model")
        page.draw_rect(fitz.Rect(30, 100, 400, 360), fill=(0.5, 0.7, 0.9))
        uploaded = client.post(f"/api/courses/{cid}/documents", files={"file": ("course.pdf", pdf.tobytes(), "application/pdf")})
        assert uploaded.status_code == 201
        did = uploaded.json()["id"]
        result = client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 1, "page_end": 1})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["created"] == 1 and data["reused"] == 0
        assert len(data["warnings"]) == 1 and "quotation" in data["warnings"][0]
        kp = client.get(f"/api/documents/{did}/knowledge-points").json()
        assert len(kp) == 1
        assert kp[0]["review_status"] == "pending"
        assert kp[0]["page"] == 1 and kp[0]["source_kind"] == "text"
        again = client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 1, "page_end": 1})
        assert again.json()["created"] == 0 and again.json()["reused"] == 1
        reviewed = client.post(f"/api/knowledge-points/{kp[0]['id']}/review", json={"decision": "approved"})
        assert reviewed.status_code == 200
        assert client.get(f"/api/documents/{did}/knowledge-points").json()[0]["review_status"] == "approved"
        assert client.post(f"/api/knowledge-points/{kp[0]['id']}/review", json={"decision": "unknown"}).status_code == 422
        assert client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 2, "page_end": 2}).status_code == 422
        visual = client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 2, "page_end": 2, "include_visual": True})
        assert visual.status_code == 200, visual.text
        assert visual.json()["vision_pages"] == [2]
        points = client.get(f"/api/documents/{did}/knowledge-points").json()
        assert len(points) == 2 and points[1]["source_kind"] == "figure_description"
        assert "unverified" in points[1]["evidence_note"]
        assert client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 1, "page_end": 5}).status_code == 422
        assert client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 3}).status_code == 422


def test_model_unconfigured_and_invalid_json_rejected(tmp_path):
    from knowtrace.ai import OllamaProvider, AIServiceError, grounded_items
    class BadAI:
        text_model = "mock"
        def extract(self, pages):
            return "not-json"
    app = create_app(db_path=tmp_path / "test.sqlite3", storage_dir=tmp_path / "uploads", ai_provider=BadAI())
    with TestClient(app) as client:
        cid = make_course(client)
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((30, 50), "DATA COMMUNICATION SYSTEM includes sender channel and receiver")
        page.insert_text((30, 70), "DATA COMMUNICATION SYSTEM includes sender channel and receiver")
        up = client.post(f"/api/courses/{cid}/documents", files={"file": ("course.pdf", pdf.tobytes(), "application/pdf")})
        did = up.json()["id"]
        failure = client.post(f"/api/documents/{did}/extract-knowledge", json={"page_start": 1})
        assert failure.status_code == 502
        assert client.get(f"/api/documents/{did}/knowledge-points").json() == []
    provider = OllamaProvider(text_model="")
    from knowtrace.ai import AIConfigError
    with pytest.raises(AIConfigError):
        provider.extract({1: "text"})
    with pytest.raises(AIServiceError):
        grounded_items("{broken", {1: "source"})
