"""Local single-user AI settings. Secret writes are local-only and never echoed.

Stored beside the SQLite database, with restrictive file permissions. This
remains a single-user local application, NOT an authenticated cloud API.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .ai import OllamaProvider
from .mimo import MimoProvider

SETTINGS_LOCK = RLock()
MODELS = ("mimo-v2.6-flash", "mimo-v2.6-pro")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AISettingsUpdate(BaseModel):
    default_provider: Literal["ollama", "mimo"] | None = None
    mimo_model: Literal["mimo-v2.6-flash", "mimo-v2.6-pro"] | None = None
    mimo_api_key: str | None = Field(default=None, max_length=500)
    clear_mimo_key: bool = False
    ollama_text_model: str | None = Field(default=None, max_length=120)
    ollama_vision_model: str | None = Field(default=None, max_length=120)
    ollama_url: str | None = Field(default=None, max_length=250)


def local_request(request: Request):
    if request.client is None or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "AI settings are available only from localhost")


def valid_ollama_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS
                and not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment and parsed.path in ("", "/")
                and parsed.port is not None and 1 <= parsed.port <= 65535)
    except ValueError:
        return False


def read_settings(path: Path) -> dict:
    with SETTINGS_LOCK:
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            return settings if isinstance(settings, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}


def write_settings(path: Path, data: dict):
    with SETTINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".knowtrace-settings-", dir=str(path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, path)
            if os.name == "posix":
                os.chmod(path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def providers(path: Path):
    config = read_settings(path)
    local = OllamaProvider.from_env()
    mimo = MimoProvider.from_env()
    if "ollama_url" in config:
        local.base_url = config["ollama_url"]
    if "ollama_text_model" in config:
        local.text_model = config["ollama_text_model"]
    if "ollama_vision_model" in config:
        local.vision_model = config["ollama_vision_model"]
    if "mimo_model" in config:
        mimo.text_model = config["mimo_model"]
    if config.get("mimo_api_key"):
        mimo.api_key = config["mimo_api_key"]
    if config.get("default_provider") in ("mimo", "ollama"):
        default = config["default_provider"]
    else:
        default = os.getenv("KNOWTRACE_AI_PROVIDER", "ollama").lower()
    if default not in ("ollama", "mimo"):
        default = "ollama"
    return default, local, mimo


def public_status(path: Path) -> dict:
    default, local, mimo = providers(path)
    return {
        "default": default,
        "providers": [
            {"id": "ollama", "label": "本地 Ollama", "model": local.text_model,
             "available": bool(local.text_model), "vision_model": local.vision_model,
             "function_calling": False},
            {"id": "mimo", "label": "小米 MiMo API", "model": mimo.text_model,
             "available": bool(mimo.api_key) and not mimo.api_key.startswith(("tp-", "ttp-")),
             "vision_model": mimo.vision_model, "function_calling": True},
        ],
        "mimo_key_configured": bool(mimo.api_key) and not mimo.api_key.startswith(("tp-", "ttp-")),
        "mimo_key_source": ("local_settings" if read_settings(path).get("mimo_api_key")
                            else "environment" if mimo.api_key else "none"),
        "ollama_url": local.base_url,
        "ollama_text_model": local.text_model,
        "ollama_vision_model": local.vision_model,
        "mimo_model": mimo.text_model,
        "security": "API keys are stored server-side only and are never returned.",
    }


def register_settings_routes(app: FastAPI, path: Path):
    @app.post("/api/settings/ai/test")
    def test_ai_connection(request: Request, provider: Literal["ollama", "mimo"]):
        local_request(request)
        _, local, mimo = providers(path)
        try:
            if provider == "mimo":
                if not mimo.api_key:
                    raise HTTPException(409,"请先保存 MiMo API Key")
                test = replace(mimo, max_output_tokens=48, timeout_seconds=20)
                test._post([{"role":"user","content":"Return ONLY the word OK."}])
                return {"provider":"mimo","ok":True,"message":"MiMo API 连接与鉴权成功（测试调用可能产生少量费用）"}
            else:
                from urllib.request import urlopen
                from urllib.error import URLError
                if not valid_ollama_url(local.base_url):
                    raise HTTPException(422,"Invalid local Ollama URL")
                with urlopen(local.base_url.rstrip("/")+"/api/tags",timeout=5) as response:
                    if response.status != 200:
                        raise HTTPException(502,"Ollama 服务返回异常状态")
                return {"provider":"ollama","ok":True,"message":"本地 Ollama 服务可以连接"}
        except HTTPException:
            raise
        except Exception as exc:
            from .ai import AIConfigError, AIServiceError
            if isinstance(exc,(AIConfigError,AIServiceError)):
                raise HTTPException(502,str(exc)) from exc
            raise HTTPException(502,"Connection test failed: "+type(exc).__name__) from exc


    @app.get("/api/settings/ai")
    def get_ai_settings(request: Request):
        local_request(request)
        return public_status(path)

    @app.patch("/api/settings/ai")
    def set_ai_settings(request: Request, changes: AISettingsUpdate):
        local_request(request)
        if changes.clear_mimo_key and changes.mimo_api_key:
            raise HTTPException(422, "Cannot set and clear key in one request")
        key = changes.mimo_api_key.strip() if changes.mimo_api_key is not None else None
        if key is not None and (not key.startswith("sk-") or len(key) < 8 or any(c.isspace() for c in key)):
            raise HTTPException(422, "Use a valid sk- pay-as-you-go MiMo key")
        if changes.ollama_url is not None and not valid_ollama_url(changes.ollama_url):
            raise HTTPException(422, "Ollama URL must be http://localhost:PORT or http://127.0.0.1:PORT")
        for name in ("ollama_text_model", "ollama_vision_model"):
            value = getattr(changes, name)
            if value is not None and (any(c.isspace() for c in value) or len(value) > 120):
                raise HTTPException(422, "Ollama model name cannot contain whitespace")
        with SETTINGS_LOCK:
            state = read_settings(path)
            for field in ("default_provider", "mimo_model",
                          "ollama_text_model", "ollama_vision_model", "ollama_url"):
                value = getattr(changes, field)
                if value is not None:
                    state[field] = value
            if changes.clear_mimo_key:
                state.pop("mimo_api_key", None)
            elif key is not None:
                state["mimo_api_key"] = key
            write_settings(path, state)
        return public_status(path)
