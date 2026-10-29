"""Native MiMo SSE / Ollama NDJSON visible-text streaming and failure behavior."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from knowtrace.ai import OllamaProvider, AIServiceError
from knowtrace.mimo import MimoProvider
from knowtrace.relation_streaming import mimo_relation_stream, ollama_relation_stream


class FakeStream:
    def __init__(self,lines):
        self.content=io.BytesIO(b"".join(lines))
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def readline(self,limit=-1):
        return self.content.readline(limit)


def sse(event):
    return b"data: "+json.dumps(event,ensure_ascii=False).encode("utf-8")+b"\n\n"


def test_mimo_stream_displays_only_visible_content_and_assembles_json():
    parts=['{"items":[','{"source_kp_id":"one"}',']}']
    lines=[
        sse({"choices":[{"delta":{"reasoning_content":"private chain-of-thought"}}]}),
        *(sse({"choices":[{"delta":{"content":chunk}}]}) for chunk in parts),
        b"data: [DONE]\n\n",
    ]
    provider=MimoProvider(api_key="sk-test",thinking="disabled")
    received=[]
    with patch("knowtrace.relation_streaming.urlopen",return_value=FakeStream(lines)) as url:
        raw=mimo_relation_stream(provider,[{"role":"user","content":"Return JSON"}],received.append)
    assert raw=="".join(parts)
    assert received==parts
    assert "private chain-of-thought" not in raw
    request=url.call_args.args[0]
    body=json.loads(request.data)
    assert body["stream"] is True
    assert body["model"]=="mimo-v2.6-flash"
    assert request.get_header("Api-key")=="sk-test"


def test_mimo_stream_interruption_is_not_success():
    provider=MimoProvider(api_key="sk-test")
    with patch("knowtrace.relation_streaming.urlopen",return_value=FakeStream([
        sse({"choices":[{"delta":{"content":"unfinished"}}]}),
    ])):
        with pytest.raises(AIServiceError,match="interrupted"):
            mimo_relation_stream(provider,[{"role":"user","content":"text"}],lambda _:None)


def test_ollama_stream_displays_response_not_thinking():
    provider=OllamaProvider(text_model="qwen3.5:4b")
    tokens=['{"items":','[]}']
    events=[
        {"response":tokens[0],"thinking":"hidden thoughts","done":False},
        {"response":tokens[1],"done":False},
        {"done":True,"total_duration":1000},
    ]
    lines=[json.dumps(event).encode()+b"\n" for event in events]
    received=[]
    with patch("knowtrace.relation_streaming.urlopen",return_value=FakeStream(lines)) as url:
        raw=ollama_relation_stream(provider,"Create links",received.append)
    assert raw=="".join(tokens)
    assert received==tokens
    assert "hidden thoughts" not in raw
    request=url.call_args.args[0]
    body=json.loads(request.data)
    assert body["stream"] is True and body["think"] is False
    assert body["format"]=="json"
    assert body["model"]=="qwen3.5:4b"


def test_ollama_stream_interruption_is_not_success():
    provider=OllamaProvider(text_model="test")
    with patch("knowtrace.relation_streaming.urlopen",return_value=FakeStream([
        b'{"response":"half","done":false}\n',
    ])):
        with pytest.raises(AIServiceError,match="interrupted"):
            ollama_relation_stream(provider,"test",lambda _:None)
