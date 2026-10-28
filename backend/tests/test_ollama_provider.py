"""Validate Ollama transport payload without network/model access."""
import json
from unittest.mock import MagicMock, patch

from knowtrace.ai import OllamaProvider


def test_ollama_generate_disables_thinking_for_json():
    provider = OllamaProvider(text_model="qwen3.5:4b")
    context = MagicMock()
    context.__enter__.return_value.read.return_value = json.dumps({"response": '{"items": []}'}).encode()
    with patch("knowtrace.ai.urlopen", return_value=context) as send:
        assert provider.extract({1: "这里是课程数据通信的相关材料。"}) == '{"items": []}'
    request = send.call_args.args[0]
    payload = json.loads(request.data)
    assert payload["model"] == "qwen3.5:4b"
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["format"] == "json"


def test_ollama_generate_disables_thinking_for_vision():
    provider = OllamaProvider(vision_model="qwen3.5:4b")
    context = MagicMock()
    context.__enter__.return_value.read.return_value = json.dumps({"response": "一张数据通信模型图"}).encode()
    with patch("knowtrace.ai.urlopen", return_value=context) as send:
        assert provider.describe_visual("dGVzdA==") == "一张数据通信模型图"
    payload = json.loads(send.call_args.args[0].data)
    assert payload["think"] is False
    assert payload["images"] == ["dGVzdA=="]
    assert "format" not in payload
