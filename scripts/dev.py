#!/usr/bin/env python3
"""跨平台開發啟動器(Windows / macOS / Linux 皆可)。

用途:一個終端機把整套本機開發環境拉起來——
  1) docker compose 起 postgres + redis 並等健康
  2) 對 app 資料庫跑 `alembic upgrade head`
  3) 同時啟動 backend(uvicorn)、celery worker、frontend(vite),彙整輸出;
     Ctrl+C 一次全部關閉。

用法:
  python scripts/dev.py            # 啟動全部(預設)
  python scripts/dev.py --setup    # 一次性安裝:建 venv + 後端依賴 + 前端 npm install
  python scripts/dev.py --no-web   # 不起前端(只跑後端/worker,例如純 API 開發)

注意:
  - Docker Desktop / daemon 需先啟動(腳本只檢查,不代啟)。
  - Ollama 需另外常駐並 `ollama pull <config/models.yaml 的 model>`;腳本只檢查可達性。
  - 這是開發便利工具,非正式部署(正式為 P7/P8 的 compose + Caddy)。
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"

# 與 backend/.env / config/models.yaml 對齊的本機預設。
PG_PORT = 5433
REDIS_PORT = 6379
API_PORT = 8000
WEB_PORT = 5173
OLLAMA_URL = "http://localhost:11434"


def venv_python() -> Path:
    return BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def log(msg: str) -> None:
    print(f"[dev] {msg}", flush=True)


def die(msg: str) -> "None":
    print(f"[dev] 錯誤:{msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None) -> int:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode


def port_open(port: int, host: str = "localhost") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_port(name: str, port: int, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            log(f"{name} 就緒(:{port})")
            return
        time.sleep(1)
    die(f"{name} 在 {timeout}s 內未就緒(:{port});確認 docker compose 是否正常。")


# ── 前置檢查 ────────────────────────────────────────────────────────────────
def check_docker() -> None:
    if shutil.which("docker") is None:
        die("找不到 docker;請安裝並啟動 Docker Desktop。")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        die("Docker daemon 未執行;請先啟動 Docker Desktop 再重試。")


def check_ollama() -> None:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        log("Ollama 可達")
    except Exception:
        log("警告:Ollama 未偵測到(聊天串流會回 error);請 `ollama serve` 並 pull 對應模型。")


# ── setup(一次性)────────────────────────────────────────────────────────────
def do_setup() -> None:
    check_docker()
    py = venv_python()
    if not py.exists():
        log("建立後端 venv…")
        if run([sys.executable, "-m", "venv", ".venv"], cwd=BACKEND) != 0:
            die("建立 venv 失敗。")
    log("安裝後端依賴(pip install -e .[dev])…")
    if run([str(py), "-m", "pip", "install", "-e", ".[dev]"], cwd=BACKEND) != 0:
        die("後端依賴安裝失敗。")
    log("安裝前端依賴(npm install)…")
    npm = ["cmd", "/c", "npm", "install"] if IS_WINDOWS else ["npm", "install"]
    if run(npm, cwd=FRONTEND) != 0:
        die("前端依賴安裝失敗。")
    log("完成。記得另外:ollama pull(見 config/models.yaml 的 model),然後 `python scripts/dev.py`。")


# ── 啟動 ─────────────────────────────────────────────────────────────────────
def preflight() -> None:
    check_docker()
    if not venv_python().exists():
        die("後端 venv 不存在;先跑一次:python scripts/dev.py --setup")
    if not (FRONTEND / "node_modules").exists():
        die("前端 node_modules 不存在;先跑一次:python scripts/dev.py --setup")


def start_infra_and_migrate() -> None:
    log("啟動 postgres + redis(docker compose up -d)…")
    if run(["docker", "compose", "up", "-d"], cwd=ROOT) != 0:
        die("docker compose 啟動失敗。")
    wait_port("postgres", PG_PORT)
    wait_port("redis", REDIS_PORT)
    log("套用資料庫 migration(alembic upgrade head)…")
    if run([str(venv_python()), "-m", "alembic", "upgrade", "head"], cwd=BACKEND) != 0:
        die("alembic upgrade 失敗。")


def spawn(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen[str]:
    kwargs: dict[str, object] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # 便於整組終止
    else:
        kwargs["start_new_session"] = True
    # 子程序(vite / celery / structlog 中文)一律吐 UTF-8;強制 Python 子程序也用 UTF-8,
    # 並以 UTF-8 解碼(errors=replace 兜底),避免在 cp950 等非 UTF-8 console 上解碼爆掉。
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **kwargs,  # type: ignore[arg-type]
    )


def pump(name: str, proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[{name}] {line}", end="", flush=True)


def kill(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_all(no_web: bool) -> None:
    preflight()
    check_ollama()
    start_infra_and_migrate()

    py = str(venv_python())
    procs: dict[str, subprocess.Popen[str]] = {}

    procs["api"] = spawn(
        "api",
        [py, "-m", "uvicorn", "app.main:app", "--reload", "--port", str(API_PORT)],
        BACKEND,
    )
    # -Q ingest,default:文件匯入走獨立 queue(PHASE_2 §2.3)
    worker_cmd = [py, "-m", "celery", "-A", "app.workers.celery_app:celery_app", "worker",
                  "-l", "info", "-Q", "ingest,default"]
    if IS_WINDOWS:
        worker_cmd += ["--pool=solo"]  # Windows 無 fork,prefork 收不到任務
    procs["worker"] = spawn("worker", worker_cmd, BACKEND)

    if not no_web:
        web_cmd = ["cmd", "/c", "npm", "run", "dev"] if IS_WINDOWS else ["npm", "run", "dev"]
        procs["web"] = spawn("web", web_cmd, FRONTEND)

    for name, proc in procs.items():
        threading.Thread(target=pump, args=(name, proc), daemon=True).start()

    log(f"全部啟動。API http://localhost:{API_PORT}  前端 http://localhost:{WEB_PORT}  (Ctrl+C 全部關閉)")

    stopping = False

    def shutdown(*_: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        log("關閉中…")
        for proc in procs.values():
            kill(proc)

    try:
        while not stopping:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    log(f"子程序 '{name}' 已結束(exit {proc.returncode});一併關閉其餘。")
                    shutdown()
                    break
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()

    for proc in procs.values():
        try:
            proc.wait(timeout=10)
        except Exception:
            kill(proc)
    log("已全部關閉(基礎設施仍在背景;需要時 `docker compose stop`)。")


def main() -> None:
    # 轉發子程序輸出時,cp950 等 console 可能無法編碼 vite 的 ➜ / 方框字元;改 replace 兜底
    # (保留原編碼,中文仍正常,僅印不出的符號變 '?'),避免 print 時 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="本機開發啟動器(跨平台)")
    parser.add_argument("--setup", action="store_true", help="一次性安裝依賴(venv/pip/npm)")
    parser.add_argument("--no-web", action="store_true", help="不啟動前端(只跑後端/worker)")
    args = parser.parse_args()

    if args.setup:
        do_setup()
        return
    run_all(no_web=args.no_web)


if __name__ == "__main__":
    main()
