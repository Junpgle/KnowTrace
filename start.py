#!/usr/bin/env python3
"""KnowTrace local development launcher.

Run from any working directory: python3 start.py
This script owns only the processes it starts; it never kills existing servers.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
API_URL = "http://127.0.0.1:8000/health"
WEB_URL = "http://127.0.0.1:5173/"
STARTUP_TIMEOUT = 90


class LaunchError(RuntimeError):
    """User-actionable startup failure."""


def say(message: str) -> None:
    print(f"[KnowTrace] {message}", flush=True)


def command(
    argv: list[str], *, cwd: Path = ROOT, quiet: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, check=False, text=True,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )


def local_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def require_port(port: int) -> None:
    # Vite and uvicorn both bind on IPv4 loopback only.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise LaunchError(
                f"127.0.0.1:{port} 已被占用。请先关闭已有的开发服务，再重试。"
                " 脚本不会终止其他程序。"
            ) from exc


def setup_backend(force_install: bool) -> Path:
    if sys.version_info < (3, 11):
        raise LaunchError("需要 Python 3.11 或更新版本。")
    python = local_python()
    if not python.is_file():
        say("首次运行：创建项目专用 Python 虚拟环境 .venv ...")
        result = command([sys.executable, "-m", "venv", str(VENV)])
        if result.returncode:
            raise LaunchError("创建 .venv 失败，请检查 Python 的 venv / pip 组件。")
    if not python.is_file():
        raise LaunchError(f"无法找到虚拟环境 Python：{python}")

    check = command(
        [str(python), "-c", "import fastapi, uvicorn, fitz, pptx, multipart"],
        quiet=True,
    )
    if force_install or check.returncode:
        say("安装后端 Python 依赖（首次运行可能需要下载）...")
        result = command(
            [str(python), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")]
        )
        if result.returncode:
            raise LaunchError("后端依赖安装失败，请检查 pip 输出和网络连接。")
    return python


def setup_frontend(force_install: bool) -> str:
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise LaunchError(
            "未找到 Node.js / npm。请安装 Node.js 22 LTS（推荐）后重新运行。"
        )
    version = subprocess.run(
        [node, "-p", "process.versions.node"], capture_output=True, text=True, check=False
    )
    try:
        major, minor, *_ = (int(part) for part in version.stdout.strip().split("."))
    except ValueError as exc:
        raise LaunchError("无法读取 Node.js 版本，请检查 Node.js 安装。") from exc
    if major < 20 or (major == 20 and minor < 19):
        raise LaunchError(f"当前 Node.js {major}.{minor}，需要 20.19+ 或更新版本。")

    vite = FRONTEND / "node_modules" / ".bin" / (
        "vite.cmd" if os.name == "nt" else "vite"
    )
    if force_install or not vite.exists():
        say("安装前端 npm 依赖（首次运行可能需要下载）...")
        install = "ci" if (FRONTEND / "package-lock.json").is_file() else "install"
        result = command([npm, install], cwd=FRONTEND)
        if result.returncode:
            raise LaunchError("前端依赖安装失败，请检查 npm 输出和网络连接。")
    return npm


def spawn(argv: list[str], cwd: Path, label: str) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {"cwd": cwd}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    say(f"启动{label} ...")
    return subprocess.Popen(argv, **options)


def ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.8) as response:
            return 200 <= response.status < 400
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def wait_for_services(
    backend_process: subprocess.Popen[bytes],
    frontend_process: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    backend_announced = False
    while time.monotonic() < deadline:
        if backend_process.poll() is not None:
            raise LaunchError(
                f"后端提前退出（退出码 {backend_process.returncode}），请查看上方日志。"
            )
        if frontend_process.poll() is not None:
            raise LaunchError(
                f"前端提前退出（退出码 {frontend_process.returncode}），请查看上方日志。"
            )
        backend_up = ready(API_URL)
        if backend_up and not backend_announced:
            say("后端 API 已就绪：http://127.0.0.1:8000/docs")
            backend_announced = True
        if backend_up and ready(WEB_URL):
            return
        time.sleep(0.5)
    raise LaunchError(
        "启动超时：前端或后端未在 90 秒内就绪，请检查上方输出。"
    )


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if os.name == "nt":
        # npm and uvicorn --reload launch child processes on Windows.
        # taskkill /T targets only the process tree started by this launcher.
        command(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], quiet=True
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="一键启动 KnowTrace 前后端并自动打开网页。"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="启动服务，但不自动打开浏览器"
    )
    parser.add_argument(
        "--install", action="store_true", help="强制重新检查并安装前后端依赖"
    )
    parser.add_argument(
        "--no-install", action="store_true", help="仅使用已安装的依赖"
    )
    args = parser.parse_args()
    if args.install and args.no_install:
        parser.error("--install 和 --no-install 不能同时使用")

    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    try:
        if not (BACKEND / "requirements.txt").is_file() or not (
            FRONTEND / "package.json"
        ).is_file():
            raise LaunchError("找不到 backend/ 或 frontend/，请在完整项目仓库中运行。")

        # Check both ports before touching dependencies or other processes.
        require_port(8000)
        require_port(5173)

        if args.no_install:
            python = local_python()
            if not python.is_file() or command(
                [str(python), "-c", "import fastapi, uvicorn, fitz, pptx, multipart"],
                quiet=True,
            ).returncode:
                raise LaunchError("后端环境尚未安装，请去掉 --no-install 参数。")
            npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
            vite = FRONTEND / "node_modules" / ".bin" / (
                "vite.cmd" if os.name == "nt" else "vite"
            )
            if not npm or not vite.exists():
                raise LaunchError("前端环境尚未安装，请去掉 --no-install 参数。")
        else:
            python = setup_backend(args.install)
            npm = setup_frontend(args.install)

        backend_process = spawn(
            [str(python), "-m", "uvicorn", "knowtrace.main:app", "--reload",
             "--host", "127.0.0.1", "--port", "8000"],
            BACKEND, "FastAPI 后端",
        )
        frontend_process = spawn(
            [str(npm), "run", "dev", "--", "--strictPort"],
            FRONTEND, "Vue / Vite 前端",
        )
        wait_for_services(backend_process, frontend_process)
        say(f"前后端已就绪，网页地址：{WEB_URL}")
        if not args.no_browser:
            if not webbrowser.open(WEB_URL, new=2):
                say("无法自动调用默认浏览器，请手动打开上方网页地址。")
        say("保持此窗口开启以运行服务；按 Ctrl+C 停止前后端。")

        while True:
            if backend_process.poll() is not None:
                raise LaunchError("后端服务已退出，请检查日志。")
            if frontend_process.poll() is not None:
                raise LaunchError("前端服务已退出，请检查日志。")
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("收到退出信号。")
        return 0
    except (LaunchError, OSError) as exc:
        say(f"启动失败：{exc}")
        return 1
    finally:
        say("正在关闭本次启动的前后端进程 ...")
        stop_process(frontend_process)
        stop_process(backend_process)


if __name__ == "__main__":
    raise SystemExit(main())
