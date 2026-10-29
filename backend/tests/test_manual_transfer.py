"""Copy/paste external AI handoff: strict evidence, idempotency and no API calls."""
import json

import fitz
import pytest
from fastapi.testclient import TestClient

from knowtrace.main import create_app
from knowtrace.manual_transfer import parse_reply

QUOTE = "The data source transforms the information message into digital signals"
PAGE = (QUOTE + " for transmission over a network, then the receiving system "
        "recovers the original message from the received signal.")


class Stub:
    text_model = "mock"
    vision_model = ""

    def extract(self, pages):
        return json.dumps({"items": [{"name": "Data source", "summary": "Converts information to signals.",
                                      "page": 1, "quote": QUOTE, "difficulty": 2}]})

    def generate_questions(self, **kwargs):
        raise AssertionError("External import must never call a model")


@pytest.fixture
def env(tmp_path):
    with TestClient(create_app(db_path=tmp_path/"data.sqlite3",
                               storage_dir=tmp_path/"uploads", ai_provider=Stub())) as http:
        cid=http.post("/api/courses",json={"title":"Data Communications"}).json()["id"]
        pdf=fitz.open()
        page=pdf.new_page()
        page.insert_text((40,70), QUOTE)
        page.insert_text((40,95), "for transmission over a network, then the receiving system")
        page.insert_text((40,120), "recovers the original message from the received signal.")
        uploaded=http.post(f"/api/courses/{cid}/documents",
                           files={"file":("lecture.pdf",pdf.tobytes(),"application/pdf")})
        assert uploaded.status_code==201,uploaded.text
        did=uploaded.json()["id"]
        extracted=http.post(f"/api/documents/{did}/extract-knowledge",json={"page_start":1})
        assert extracted.status_code==200,extracted.text
        kid=extracted.json()["created_ids"][0]
        assert http.post(f"/api/knowledge-points/{kid}/review",
                         json={"decision":"approved"}).status_code==200
        chunks=http.get(f"/api/documents/{did}/chunks").json()
        yield http,cid,did,kid,chunks[0]["id"]


def question(chunk_id, quote=QUOTE, name="What does the source transform into a digital signal?"):
    return {"question_type":"single_choice", "stem":name,
            "options":{"A":"Information message","B":"Physical textbook",
                       "C":"Ink on paper","D":"Analog clock"},
            "correct_answer":"A","explanation":"The data source transforms an information message.",
            "difficulty":2,"source_chunk_id":chunk_id,"quote":quote}


def test_external_question_prompt_and_import(env):
    http,cid,did,kid,chunk=env
    prep=http.post(f"/api/knowledge-points/{kid}/external-question-prompt",
                   json={"count":2,"question_types":["single_choice"]})
    assert prep.status_code==200,prep.text
    assert chunk in prep.json()["prompt"] and QUOTE in prep.json()["prompt"]
    assert prep.json()["source_chunks"]==1
    assert prep.json()["requires_review"] is True
    out= {"items":[question(chunk)]}
    raw="下面是 JSON 答复：\n" + chr(96)*3 + "json\n" + json.dumps(out) + "\n" + chr(96)*3
    response=http.post(f"/api/knowledge-points/{kid}/import-external-questions",
        json={"count":2,"question_types":["single_choice"],"response_text":raw})
    assert response.status_code==200,response.text
    assert response.json()["created"]==1
    assert response.json()["origin"]=="manual_external"
    vid=response.json()["question_version_ids"][0]
    detail=http.get(f"/api/question-versions/{vid}").json()
    assert detail["review_status"]=="pending"
    assert detail["origin"]=="imported"
    assert detail["sources"][0]["chunk_id"]==chunk
    assert detail["sources"][0]["page_start"]==1
    assert "External AI" in detail["sources"][0]["evidence_note"]
    assert http.get(f"/api/courses/{cid}/questions",params={"status":"approved"}).json()==[]
    duplicate=http.post(f"/api/knowledge-points/{kid}/import-external-questions",
        json={"count":2,"question_types":["single_choice"],"response_text":json.dumps(out)})
    assert duplicate.status_code==200
    assert duplicate.json()["created"]==0
    assert len(http.get(f"/api/courses/{cid}/questions").json()) == 1


def test_external_question_hallucinations_are_blocked(env):
    http,cid,did,kid,chunk=env
    wrong={"items":[question(chunk,"Completely invented evidence about an unrelated system")]}
    result=http.post(f"/api/knowledge-points/{kid}/import-external-questions",json={
        "count":1,"question_types":["single_choice"],"response_text":json.dumps(wrong)})
    assert result.status_code==200 and result.json()["created"]==0
    assert "source quotation not found" in result.json()["warnings"][0]
    wrongid={"items":[question("not-allowed")]}
    result=http.post(f"/api/knowledge-points/{kid}/import-external-questions",json={
        "count":1,"question_types":["single_choice"],"response_text":json.dumps(wrongid)})
    assert result.status_code==200 and result.json()["created"]==0
    assert http.get(f"/api/courses/{cid}/questions").json()==[]
    bad=http.post(f"/api/knowledge-points/{kid}/import-external-questions",json={
        "count":1,"question_types":["single_choice"],"response_text":"AI says something only"})
    assert bad.status_code==422


def test_external_knowledge_prompt_and_import(env):
    http,cid,did,kid,chunk=env
    prepared=http.post(f"/api/documents/{did}/external-knowledge-prompt",
                       json={"page_start":1,"page_end":1})
    assert prepared.status_code==200,prepared.text
    assert QUOTE in prepared.json()["prompt"]
    point={"items":[{"name":"Signal transmission","summary":"Transmission through communication networks.",
                     "page":1,"quote":QUOTE,"difficulty":1}]}
    result=http.post(f"/api/documents/{did}/import-external-knowledge",
         json={"page_start":1,"page_end":1,"response_text":json.dumps(point)})
    assert result.status_code==200,result.text
    assert result.json()["created"]==1
    imported_id=result.json()["created_ids"][0]
    detail=http.get(f"/api/knowledge-points/{imported_id}").json()
    assert detail["review_status"]=="pending"
    assert detail["sources"][0]["chunk_id"]==chunk
    assert "External AI" in detail["sources"][0]["evidence_note"]
    again=http.post(f"/api/documents/{did}/import-external-knowledge",
         json={"page_start":1,"response_text":json.dumps(point)})
    assert again.status_code==200 and again.json()["created"]==0
    assert again.json()["reused"]==1


def test_external_knowledge_bad_page_or_fake_evidence(env):
    http,cid,did,kid,chunk=env
    for data in [{"page_start":2,"page_end":2},{"page_start":1,"page_end":5}]:
        assert http.post(f"/api/documents/{did}/external-knowledge-prompt",json=data).status_code==422
    fake={"items":[{"name":"Fake extraction","summary":"Definitely not in the lecture page.",
                    "page":1,"quote":"A source quotation entirely absent from the document","difficulty":2}]}
    result=http.post(f"/api/documents/{did}/import-external-knowledge",
        json={"page_start":1,"response_text":json.dumps(fake)})
    assert result.status_code==200 and result.json()["created"]==0
    assert len(result.json()["warnings"])==1
    not_ready=http.post(f"/api/documents/nonexistent/external-knowledge-prompt",json={})
    assert not_ready.status_code==404


def test_external_reply_parser_formats_and_ambiguity():
    obj='{"items":[]}'
    assert parse_reply(obj)==obj
    assert parse_reply("回复如下：\n"+chr(96)*3+"json\n"+obj+"\n"+chr(96)*3)==obj
    assert parse_reply("这是结果："+obj+"\n以上。")==obj
    assert parse_reply('[]') == obj
    assert parse_reply('{"questions":[]}') == obj
    assert parse_reply('{"knowledge_points":[]}') == obj
    with pytest.raises(Exception):
        parse_reply("第一版 "+obj+" 第二版 "+obj)
