"""Display-only streaming of user-visible answer text from local or MiMo models.

Do not expose reasoning_content or hidden thinking. Nothing is written to
knowledge tables until the *complete* response passes the normal evidence
validator. No retries after partially delivered tokens (possible API billing).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .ai import AIConfigError, AIServiceError, OllamaProvider
from .mimo import MimoProvider

MAX_OUTPUT_CHARS = 180_000
MAX_LINE_BYTES = 1_000_000


def mimo_relation_stream(provider: MimoProvider, messages: list[dict],
                         on_delta: Callable[[str], None]) -> str:
    if not provider.api_key or provider.api_key.startswith(("tp-", "ttp-")):
        raise AIConfigError("MiMo requires a configured sk- API Key")
    endpoint = urlparse(provider.base_url)
    if (endpoint.scheme != "https" or endpoint.hostname != "api.xiaomimimo.com"
            or endpoint.username or endpoint.password or endpoint.port not in (None, 443)
            or endpoint.path.rstrip("/") != "/v1" or endpoint.query or endpoint.fragment):
        raise AIConfigError("Invalid MiMo API endpoint")
    if provider.thinking not in ("enabled", "disabled"):
        raise AIConfigError("Invalid MiMo thinking setting")
    body = {
        "model": provider.text_model,
        "messages": messages,
        "stream": True,
        "thinking": {"type": provider.thinking},
        "max_completion_tokens": provider.max_output_tokens,
    }
    if provider.thinking == "disabled":
        body["temperature"] = 0.2
    request = Request(
        provider.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json", "Accept":"text/event-stream",
                 "api-key":provider.api_key}, method="POST"
    )
    pieces: list[str] = []
    received = 0
    done = False
    try:
        with urlopen(request, timeout=provider.timeout_seconds) as response:
            while True:
                line = response.readline(MAX_LINE_BYTES+1)
                if not line:break
                if len(line)>MAX_LINE_BYTES:
                    raise AIServiceError("MiMo stream event exceeds size limit")
                if not line.startswith(b"data:"):continue
                data=line[5:].strip()
                if data == b"[DONE]":
                    done=True
                    break
                if not data:continue
                try:
                    event=json.loads(data)
                except (ValueError,UnicodeError) as exc:
                    raise AIServiceError("Invalid MiMo stream event") from exc
                if isinstance(event,dict) and event.get("error"):
                    raise AIServiceError("MiMo streaming returned an API error")
                choices=event.get("choices") if isinstance(event,dict) else None
                delta=(choices[0].get("delta") or {}) if isinstance(choices,list) and choices else {}
                # Never forward reasoning_content, thinking, tool arguments, or source prompt.
                content=delta.get("content")
                if not isinstance(content,str) or not content:continue
                received+=len(content)
                if received>MAX_OUTPUT_CHARS:
                    raise AIServiceError("MiMo output exceeds response limit")
                pieces.append(content)
                on_delta(content)
    except HTTPError as exc:
        raise AIServiceError(f"MiMo streaming HTTP {exc.code}; check key, quota and model") from exc
    except (URLError,TimeoutError,UnicodeError,OSError) as exc:
        raise AIServiceError(f"MiMo streaming failed: {type(exc).__name__}") from exc
    result="".join(pieces)
    if not result.strip():
        raise AIServiceError("MiMo returned no visible stream content")
    if not done:
        raise AIServiceError("MiMo stream interrupted before completion")
    return result


def ollama_relation_stream(provider: OllamaProvider, prompt: str,
                           on_delta: Callable[[str], None]) -> str:
    if not provider.text_model:
        raise AIConfigError("Select an Ollama text model in AI settings")
    target=urlparse(provider.base_url)
    if (target.scheme != "http" or target.hostname not in ("127.0.0.1","localhost","::1")
            or target.username or target.password or target.query or target.fragment):
        raise AIConfigError("Only a local Ollama endpoint is permitted")
    body={
        "model":provider.text_model,"prompt":prompt,"stream":True,"think":False,
        "format":"json","options":{"temperature":0,"num_predict":3000},
    }
    request=Request(provider.base_url.rstrip("/")+"/api/generate",
        data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json"},method="POST")
    pieces=[]
    received=0
    finished=False
    try:
        with urlopen(request,timeout=provider.timeout_seconds) as response:
            while True:
                line=response.readline(MAX_LINE_BYTES+1)
                if not line:break
                if len(line)>MAX_LINE_BYTES:
                    raise AIServiceError("Ollama stream event exceeds size limit")
                try:
                    event=json.loads(line)
                except (ValueError,UnicodeError) as exc:
                    raise AIServiceError("Invalid Ollama stream event") from exc
                if event.get("error"):
                    raise AIServiceError("Ollama streaming failed")
                text=event.get("response")
                if isinstance(text,str) and text:
                    received+=len(text)
                    if received>MAX_OUTPUT_CHARS:
                        raise AIServiceError("Ollama output exceeds response limit")
                    pieces.append(text)
                    on_delta(text)
                if event.get("done") is True:
                    finished=True
                    break
    except HTTPError as exc:
        raise AIServiceError(f"Ollama HTTP {exc.code}") from exc
    except (URLError,TimeoutError,UnicodeError,OSError) as exc:
        raise AIServiceError(f"Ollama streaming failed: {type(exc).__name__}") from exc
    result="".join(pieces)
    if not result.strip():
        raise AIServiceError("Local Ollama returned no visible response")
    if not finished:
        raise AIServiceError("Ollama stream interrupted before completion")
    return result
