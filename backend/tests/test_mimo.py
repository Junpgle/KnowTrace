"""MiMo protocol compatibility, tool-use budgets and genuine lazy retrieval."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from knowtrace.ai import AIConfigError, AIServiceError
from knowtrace.mimo import MimoProvider
from knowtrace.main import create_app


def mock_response(content=None, *, tool_calls=None, usage=None, reasoning=None):
    message = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message}],
            "usage": usage or {"total_tokens": 15, "prompt_tokens": 10, "completion_tokens": 5}}


def tool_call(name, args, id="call_123"):
    return {"id": id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def test_mimo_http_shape_and_secret_is_server_side():
    p = MimoProvider(api_key="sk-private", text_model="mimo-v2.6-flash")
    context = MagicMock()
    context.__enter__.return_value.read.return_value = json.dumps(mock_response('{"items":[]}')).encode()
    with patch("knowtrace.mimo.urlopen", return_value=context) as opener:
        assert p.extract({2: "这是一个至少十个字符的数据通信系统的说明。"}) == '{"items":[]}'
    request = opener.call_args.args[0]
    body = json.loads(request.data)
    assert request.full_url == "https://api.xiaomimimo.com/v1/chat/completions"
    assert body["model"] == "mimo-v2.6-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert "tools" not in body
    assert request.get_header("Api-key") == "sk-private"
    assert "sk-private" not in request.data.decode()


def test_mimo_requires_key_and_locked_official_host():
    with pytest.raises(AIConfigError):
        MimoProvider(api_key="").extract({1: "text"})
    with pytest.raises(AIConfigError, match="Token Plan"):
        MimoProvider(api_key="tp-package-key").extract({1: "text"})
    with pytest.raises(AIConfigError, match="Token Plan"):
        MimoProvider(api_key="ttp-team-package-key").extract({1: "text"})
    with pytest.raises(AIConfigError):
        MimoProvider(api_key="valid", base_url="http://127.0.0.1:8000/v1").extract({1: "text"})
    with pytest.raises(AIConfigError):
        MimoProvider(api_key="valid", base_url="https://evil.example/v1").extract({1: "text"})


def test_mimo_does_not_send_full_document_until_tools_requested(tmp_path):
    db = tmp_path / "source.sqlite3"
    from knowtrace.db import initialize, connect
    initialize(db)
    with connect(db) as cx:
        cx.execute("INSERT INTO users(id,username) VALUES ('local','local')")
        cx.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course1','local','Networks')")
        cx.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,media_type,storage_key,
                    sha256,total_pages,status) VALUES('doc1','course1','local','lecture.pdf',
                    'application/pdf','file.pdf','abc',2,'ready')""")
        content = "The source sends a digital bit stream to a modem for transmission."
        cx.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,page_start,page_end,
                    content_kind,content_text,content_sha256)
                    VALUES('chunk1','doc1',0,2,2,'text',?,'sha')""",(content,))
        cx.execute("""INSERT INTO knowledge_points(id,course_id,canonical_name,review_status)
                    VALUES('kp1','course1','Data system','approved')""")
        cx.execute("""INSERT INTO knowledge_point_sources(knowledge_point_id,chunk_id)
                    VALUES ('kp1','chunk1')""")
    p = MimoProvider(api_key="fake")
    calls = []
    question = {"items": [{"question_type": "single_choice",
             "stem": "What is sent to the modem before transmission?",
             "options": {"A": "Digital bit stream", "B": "Ink", "C": "Paper", "D": "Sound"},
             "correct_answer": "A", "explanation": "The source sends the bit stream to a modem.",
             "difficulty": 1, "source_chunk_id": "chunk1",
             "quote": "The source sends a digital bit stream"}]}
    responses = [
        mock_response(tool_calls=[tool_call("read_source_chunk", {"chunk_id": "chunk1"})], reasoning="finding evidence"),
        mock_response(content=json.dumps(question))
    ]
    def fake_post(messages, tools=None):
        calls.append((json.loads(json.dumps(messages)), tools))
        return responses.pop(0)
    p._post = fake_post
    result = p.generate_questions_lazy(db_path=db, knowledge_point_id="kp1",
              knowledge_name="Data system",knowledge_summary="A summary.",count=1,
              question_types=["single_choice"])
    assert result["tool_calls"] == 1
    assert result["retrieved_chunks"] == 1
    assert "The source sends a digital" not in json.dumps(calls[0][0])
    assert "chunk1" in json.dumps(calls[0][0])
    assert calls[0][1][0]["function"]["name"] == "read_source_chunk"
    assert calls[1][0][-1]["role"] == "tool"
    assert calls[1][0][-1]["tool_call_id"] == "call_123"
    assert "The source sends a digital" in calls[1][0][-1]["content"]
    assert calls[1][0][-2]["reasoning_content"] == "finding evidence"
    from knowtrace.questions import validate_generated_questions
    valid, warnings = validate_generated_questions(result["raw"], result["sources"],
                                        count=1,permitted_types={"single_choice"})
    assert len(valid) == 1 and warnings == []
    # A second run first discovers a short search hit, then reads the source.
    steps = [
        mock_response(tool_calls=[tool_call("search_source_chunks", {"query": "digital", "limit": 2}, "search1")]),
        mock_response(tool_calls=[tool_call("read_source_chunk", {"chunk_id": "chunk1", "offset": 0}, "read1")]),
        mock_response(content=json.dumps(question)),
    ]
    messages_before = []
    def search_then_read(messages, tools=None):
        messages_before.append(json.loads(json.dumps(messages)))
        return steps.pop(0)
    p._post = search_then_read
    second = p.generate_questions_lazy(db_path=db, knowledge_point_id="kp1",
              knowledge_name="Data system", knowledge_summary="A summary.", count=1,
              question_types=["single_choice"])
    assert second["tool_calls"] == 2 and second["retrieved_chunks"] == 1
    assert "The source sends a digital bit stream" not in json.dumps(messages_before[0])
    assert "suggested_offset" in messages_before[1][-1]["content"]
    assert "The source sends a digital bit stream" in messages_before[2][-1]["content"]


def test_fake_mimo_no_retrieval_cannot_fake_source(tmp_path):
    from knowtrace.db import initialize, connect
    db = tmp_path / "no-evidence.sqlite3"
    initialize(db)
    with connect(db) as cx:
        cx.execute("INSERT INTO users(id,username) VALUES ('local','local')")
        cx.execute("INSERT INTO courses(id,owner_user_id,title) VALUES ('course1','local','Networks')")
        cx.execute("""INSERT INTO documents(id,course_id,uploaded_by,original_filename,media_type,storage_key,
                    sha256,total_pages,status) VALUES('doc1','course1','local','lecture.pdf',
                    'application/pdf','file.pdf','abc',1,'ready')""")
        cx.execute("""INSERT INTO document_chunks(id,document_id,chunk_index,page_start,page_end,
                    content_kind,content_text,content_sha256)
                    VALUES('c1','doc1',0,1,1,'text','Evidence text of the lecture.','sha')""")
        cx.execute("INSERT INTO knowledge_points(id,course_id,canonical_name) VALUES('kp1','course1','Knowledge')")
        cx.execute("INSERT INTO knowledge_point_sources(knowledge_point_id,chunk_id) VALUES ('kp1','c1')")
    p = MimoProvider(api_key="fake")
    p._post = lambda messages, tools=None: mock_response('{"items":[{"question_type":"single_choice","stem":"An invented question","options":{"A":"One","B":"Two","C":"Three","D":"Four"},"correct_answer":"A","explanation":"Invented question explanation.","difficulty":2,"source_chunk_id":"c1","quote":"Evidence text of the lecture."}]}')
    result = p.generate_questions_lazy(db_path=db,knowledge_point_id="kp1",
                                       knowledge_name="Knowledge",knowledge_summary="summary",
                                       count=1,question_types=["single_choice"])
    assert result["tool_calls"] == 0
    assert result["sources"] == {}
    from knowtrace.questions import validate_generated_questions
    questions, warnings = validate_generated_questions(result["raw"],result["sources"],
                                                       count=1,permitted_types={"single_choice"})
    assert questions == []
    assert "source quotation" in warnings[0]


def test_tool_budget_and_invalid_names_fail_safely():
    p = MimoProvider(api_key="fake", max_tool_calls=1)
    p._post = lambda messages, tools=None: mock_response(tool_calls=[
        tool_call("read_source_chunk", {"chunk_id": "c1"}, id="a"),
        tool_call("read_source_chunk", {"chunk_id": "c2"}, id="b")])
    with pytest.raises(AIServiceError, match="budget"):
        p._tool_loop([{"role": "user", "content": "something"}], lambda name, args: {})


def test_disjoint_windows_cannot_fake_continuous_verbatim_quote():
    from knowtrace.questions import validate_generated_questions
    question = {"items": [{
        "question_type": "single_choice", "stem": "Where is the signal transmitted next?",
        "options": {"A": "Phone network", "B": "Printer", "C": "Keyboard", "D": "Screen"},
        "correct_answer": "A", "explanation": "It flows through the public telephone network.",
        "difficulty": 2, "source_chunk_id": "chunk1",
        "quote": "The source transmits a digital bit stream to the public telephone network",
    }]}
    a = "The source transmits a digital bit stream to"
    b = "the public telephone network and the destination"
    # Joining two retrieval windows and removing whitespace must not
    # manufacture a quotation that appears in neither individual window.
    src = {"chunk1": {"content_text": a + "\n" + b,
                      "evidence_segments": [a, b]}}
    accepted, warnings = validate_generated_questions(json.dumps(question), src,
                                permitted_types={"single_choice"}, count=1)
    assert accepted == []
    assert "exact source quotation not found" in warnings[0]


def test_provider_status_does_not_expose_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMO_API_KEY", "sk-hidden-from-client")
    monkeypatch.setenv("KNOWTRACE_AI_PROVIDER", "ollama")
    with TestClient(create_app(db_path=tmp_path/"app.sqlite3",
                               storage_dir=tmp_path/"upload")) as client:
        data = client.get("/api/ai/providers").json()
        assert data["default"] == "ollama"
        assert data["providers"][1]["id"] == "mimo"
        assert data["providers"][1]["available"] is True
        assert data["providers"][1]["function_calling"] is True
        assert "sk-hidden-from-client" not in json.dumps(data)
    monkeypatch.setenv("MIMO_API_KEY", "tp-plan-key")
    with TestClient(create_app(db_path=tmp_path/"app2.sqlite3",
                               storage_dir=tmp_path/"uploads2")) as client:
        assert client.get("/api/ai/providers").json()["providers"][1]["available"] is False


def test_mimo_vision_sends_native_png_data_url_without_local_ollama():
    """The PDF image must go to MiMo's own multimodal Chat Completions API."""
    import base64
    provider = MimoProvider(api_key="sk-demo-only", text_model="mimo-v2.6-flash")
    page_png = b"\x89PNG\r\n\x1a\n" + b"small test page"
    encoded = base64.b64encode(page_png).decode("ascii")
    context = MagicMock()
    context.__enter__.return_value.read.return_value = json.dumps(
        mock_response("Diagram: a source system sends digital data via a communication channel to a destination.")
    ).encode("utf-8")
    with patch("knowtrace.mimo.urlopen", return_value=context) as opener, \
         patch("knowtrace.ai.OllamaProvider.from_env", side_effect=AssertionError("Must not call Ollama")):
        description = provider.describe_visual(encoded)
    assert "source system" in description
    request = opener.call_args.args[0]
    assert request.full_url == "https://api.xiaomimimo.com/v1/chat/completions"
    payload = json.loads(request.data)
    assert payload["model"] == "mimo-v2.6-flash"
    user_content = payload["messages"][1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"] == "data:image/png;base64," + encoded
    assert user_content[1]["type"] == "text"
    assert request.get_header("Api-key") == "sk-demo-only"
    assert "sk-demo-only" not in request.data.decode("utf-8")
    assert provider.vision_model == "mimo-v2.6-flash"


def test_mimo_vision_rejects_missing_invalid_or_unreadable_image_reply():
    provider = MimoProvider(api_key="sk-demo-only")
    with pytest.raises(AIServiceError, match="empty"):
        provider.describe_visual("")
    with pytest.raises(AIServiceError, match="too large"):
        provider.describe_visual("A"*12_000_001)
    provider._post = lambda messages, tools=None: mock_response(None)
    with pytest.raises(AIServiceError, match="no readable"):
        provider.describe_visual("YWJj")


def test_mimo_api_extracts_from_sparse_pdf_image(tmp_path, monkeypatch):
    """PDF scan -> MiMo image description -> extracted pending knowledge."""
    import fitz
    from knowtrace.db import connect
    provider = MimoProvider(api_key="sk-test", text_model="mimo-v2.6-pro")
    image_summary = ("图中展示数据通信模型，源系统通过传输系统向目的系统发送信号；"
                     "源设备、发送设备和接收设备之间有明确箭头。")
    requests = []
    def fake_post(messages, tools=None):
        requests.append(messages)
        content = messages[-1]["content"]
        if isinstance(content, list):
            assert content[0]["type"] == "image_url"
            assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
            return mock_response(image_summary)
        assert image_summary in content
        return mock_response(json.dumps({"items": [{
            "name": "数据通信模型的组成",
            "summary": "通过源系统、传输系统及目的系统描述信号传递的基本组成关系。",
            "page": 1, "quote": "源系统通过传输系统向目的系统发送信号",
            "difficulty": 2,
        }]},ensure_ascii=False))
    provider._post = fake_post
    monkeypatch.setattr(MimoProvider, "from_env", staticmethod(lambda: provider))
    app = create_app(db_path=tmp_path/"vision.sqlite3", storage_dir=tmp_path/"uploads")
    with TestClient(app) as http:
        course = http.post("/api/courses", json={"title":"Computer Networks"}).json()["id"]
        pdf=fitz.open()
        p=pdf.new_page()
        p.draw_rect(fitz.Rect(25, 50, 515, 470), fill=(0.7,0.8,0.9))
        upload=http.post(f"/api/courses/{course}/documents",
            files={"file":("scan.pdf",pdf.tobytes(),"application/pdf")})
        assert upload.status_code==201,upload.text
        did=upload.json()["id"]
        missing_text=http.post(f"/api/documents/{did}/extract-knowledge",
            json={"provider":"mimo","include_visual":False})
        assert missing_text.status_code==422
        result=http.post(f"/api/documents/{did}/extract-knowledge",
            json={"provider":"mimo","include_visual":True})
        assert result.status_code==200,result.text
        assert result.json()["vision_pages"] == [1]
        assert result.json()["created"]==1
        points=http.get(f"/api/documents/{did}/knowledge-points").json()
        assert points[0]["review_status"]=="pending"
        assert points[0]["source_kind"]=="figure_description"
        assert "unverified" in points[0]["evidence_note"]
        assert len(requests)==2  # visual call then grounded extraction call
        with connect(tmp_path/"vision.sqlite3") as db:
            stored=db.execute("SELECT content_kind FROM document_chunks WHERE document_id=? "
                "AND content_kind='figure_description'",(did,)).fetchall()
            assert len(stored)==1
