# -*- coding: utf-8 -*-
"""CBoE 전체 GUI 서버: 시작 화면(작업 목록 · 새 작업 · 분석 진행) + 작업마다 검수 화면.

    /                 시작 화면 (static/home.html)
    /api/studio       작업 목록, 지금 분석, 환경 점검
    /api/inspect      고른 영상·대본 미리 보기 (길이, 줄 수)
    /api/projects     새 작업 만들기 (+ 바로 분석)
    /api/projects/{id}/run   분석 시작·이어서·다시
    /api/job, /api/job/cancel
    /api/pick         파일 고르기 창 (브라우저로 열었을 때; 데스크톱 창은 pywebview 대화상자)
    /api/open         작업 폴더·내보낸 파일 열기 (작업 공간 안만)
    /p/{id}/...       그 작업의 검수 화면 (server.app.create_app, 상대 주소로 동작)
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import guard, paths, pipeline
from ..studio import STAGE_LABELS, JobBusy, JobManager, Workspace, open_folder
from ..util import NO_WINDOW, log
from .app import STATIC, LocalOnly, SafeJSONResponse, create_app

SCRIPT_TYPES = {".txt", ".docx", ".srt", ".vtt", ".csv", ".tsv", ".json"}
SCRIPT_MAX_BYTES = 20 * 1024 * 1024

RUN_MODES = {"continue": [], "all": ["all"]}     # 이어서 (끝난 단계 건너뜀) / 처음부터 다시


class RunOptions(BaseModel):
    llm: bool = True
    agent_limit: int = Field(40, ge=0, le=999)
    jobs: int = Field(3, ge=1, le=8)
    model: Literal["sonnet", "haiku", "opus"] = "sonnet"


class NewProject(BaseModel):
    video: str
    script: str | None = None
    title: str | None = None
    start: float | None = None
    end: float | None = None
    run: bool = True
    options: RunOptions = RunOptions()


class RunRequest(BaseModel):
    mode: Literal["continue", "all"] = "continue"
    options: RunOptions = RunOptions()


class InspectRequest(BaseModel):
    video: str | None = None
    script: str | None = None


class PickRequest(BaseModel):
    kind: Literal["video", "script", "folder"] = "video"


class OpenRequest(BaseModel):
    path: str


PICK_CODE = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
kind = sys.argv[1]
if kind == "video":
    p = filedialog.askopenfilename(title="영상 파일 고르기", filetypes=[("영상", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.wmv"), ("모든 파일", "*.*")])
elif kind == "script":
    p = filedialog.askopenfilename(title="대본 파일 고르기", filetypes=[("대본", "*.txt *.docx *.srt *.vtt *.csv *.tsv *.json"), ("모든 파일", "*.*")])
else:
    p = filedialog.askdirectory(title="폴더 고르기")
sys.stdout.write(p or "")
"""


def pick_dialog(kind: str) -> str | None:
    """Native file dialog in a short-lived process (works from any server thread)."""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
        py = py.with_name("python.exe")
    try:
        r = subprocess.run([str(py), "-c", PICK_CODE, kind], capture_output=True, timeout=600,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return str(Path(out)) if out else None


TORCH_GPU_CODE = r"""
from cboe.util import device
import torch
d = device()
if d.startswith("xpu"):
    p = torch.xpu.get_device_properties(0)
    print(d, "|", f"{p.name}, {p.total_memory // 2**20} MiB")
elif d.startswith("cuda"):
    p = torch.cuda.get_device_properties(0)
    print(d, "|", f"{p.name}, {p.total_memory // 2**20} MiB")
else:
    print(d, "|")
"""


def _torch_gpu() -> tuple[str | None, str | None]:
    """(device, "name, MiB") as the model stages will see it, for a GPU nvidia-smi does not know
    (Intel Arc / Core Ultra through the torch xpu build). Runs in a child process so the GUI
    process does not load torch."""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
        py = py.with_name("python.exe")
    try:
        r = subprocess.run([str(py), "-c", TORCH_GPU_CODE], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", cwd=str(paths.ROOT), creationflags=NO_WINDOW)
        line = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:
        return None, None
    dev, _, gpu = line.partition("|")
    return (dev.strip() or None), (gpu.strip() or None)


def environment() -> dict:
    """환경 점검: GPU, ffmpeg, Claude 로그인, 모델 파일."""
    env = {"python": sys.version.split()[0]}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        env["gpu"] = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        env["gpu"] = None
    env["device"] = "cuda" if env["gpu"] else None
    if not env["gpu"]:
        env["device"], env["gpu"] = _torch_gpu()
    env["ffmpeg"] = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    llm = {"cli": bool(shutil.which("claude")), "logged_in": False, "method": None}
    if llm["cli"]:
        try:
            e = {**os.environ}
            e.pop("CLAUDECODE", None)
            r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=20,
                               encoding="utf-8", env=e, creationflags=NO_WINDOW)
            info = json.loads(r.stdout[r.stdout.find("{"):]) if "{" in r.stdout else {}
            llm["logged_in"] = bool(info.get("loggedIn"))
            llm["method"] = info.get("authMethod")
        except Exception as exc:
            llm["error"] = f"{type(exc).__name__}: {exc}"
    env["llm"] = llm
    models = paths.check()
    env["models"] = models
    env["models_missing"] = [k for k, ok in models.items() if not ok]
    return env


class ProjectApps:
    """Review apps per work folder, created on first visit."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.apps: dict[str, FastAPI] = {}
        self.lock = threading.Lock()

    def get(self, pid: str) -> FastAPI:
        with self.lock:
            app = self.apps.get(pid)
            if app is None:
                work = self.ws.work(pid)
                if not (work / "items.json").is_file():
                    raise FileNotFoundError("분석이 끝나지 않은 작업입니다")
                app = create_app(work)
                self.apps[pid] = app
            return app

    def drop(self, pid: str) -> None:
        with self.lock:
            app = self.apps.pop(pid, None)
        grabber = app.state.review._grabber if app is not None else None
        if grabber is not None:
            grabber.close()


class Studio:
    """Top-level ASGI app: /p/<id>/... goes to that project's review app, the rest to home."""

    def __init__(self, home: FastAPI, projects: ProjectApps):
        self.home = home
        self.projects = projects

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] in ("http", "websocket") else ""
        if path == "/p" or path.startswith("/p/"):
            pid, _, tail = path[3:].partition("/")
            if not tail and not path.endswith("/"):
                return await RedirectResponse("/p/" + quote(pid, safe="") + "/")(scope, receive, send)
            try:
                app = self.projects.get(pid)
            except (KeyError, FileNotFoundError) as exc:
                msg = str(exc) if isinstance(exc, FileNotFoundError) else "없는 작업입니다"
                page = f"<p>{html.escape(msg)}: {html.escape(pid)} · <a href='/'>작업 목록</a></p>"
                return await HTMLResponse(page, 404)(scope, receive, send)
            # Starlette routes on path minus root_path, so the path stays whole
            sub = dict(scope)
            sub["root_path"] = scope.get("root_path", "") + f"/p/{pid}"
            return await app(sub, receive, send)
        return await self.home(scope, receive, send)


def create_studio_app(workspace: Path | None = None) -> LocalOnly:
    ws = Workspace(workspace)
    jobs = JobManager(ws)
    projects = ProjectApps(ws)
    cache: dict = {}
    home = FastAPI(title="CBoE", default_response_class=SafeJSONResponse, docs_url="/api/docs", redoc_url=None,
                   openapi_url="/api/openapi.json")
    home.state.ws, home.state.jobs, home.state.projects = ws, jobs, projects

    @home.get("/", include_in_schema=False)
    def index():
        html = (STATIC / "home.html").read_text(encoding="utf-8")
        v = max(int((STATIC / n).stat().st_mtime) for n in ("home.html", "home.js", "home.css", "app.css"))
        return HTMLResponse(html.replace("{{v}}", str(v)), headers={"Cache-Control": "no-store"})

    home.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    dropped: set = set()

    def job_status():
        st = jobs.status()
        if st and st["state"] != "running":
            key = (st.get("project"), st.get("started"))
            if key not in dropped:                # the run rewrote the files: build the review app again, once
                dropped.add(key)
                projects.drop(st["project"])
        return st

    @home.get("/api/studio")
    def studio(refresh_env: bool = False):
        if refresh_env or "env" not in cache:
            cache["env"] = environment()
        st = job_status()
        return {"workspace": str(ws.root), "projects": ws.summaries(st), "job": st, "env": cache["env"],
                "stage_labels": STAGE_LABELS}

    @home.get("/api/job")
    def job():
        return {"job": job_status()}

    @home.post("/api/job/cancel")
    def cancel():
        return {"job": jobs.cancel()}

    @home.post("/api/inspect")
    def inspect(body: InspectRequest):
        from .. import script as script_mod
        from ..util import ffprobe
        out: dict = {}
        if body.video:
            p = Path(body.video)
            if not p.is_file():
                out["video"] = {"ok": False, "error": "파일이 없습니다"}
            else:
                try:
                    info = ffprobe(p)
                    rule = guard.limit(p, info["duration"])
                    error = None if info["has_audio"] else "소리가 없는 영상입니다"
                    if rule and rule["dev_end"] <= 0:
                        error = f"연구 규칙: {rule['title']}은 처리하지 않는 시험 영상입니다"
                    out["video"] = {"ok": error is None, **info, "name": p.name, "error": error, "guard": rule}
                except Exception as exc:
                    out["video"] = {"ok": False, "error": f"영상을 읽지 못했습니다 · {exc}"}
        if body.script:
            p = Path(body.script)
            if not p.is_file():
                out["script"] = {"ok": False, "error": "파일이 없습니다"}
            elif p.suffix.lower() not in SCRIPT_TYPES:
                out["script"] = {"ok": False, "error": "대본은 txt · docx · srt · vtt · csv · tsv · json 파일입니다"}
            elif p.stat().st_size > SCRIPT_MAX_BYTES:
                out["script"] = {"ok": False, "error": "대본 파일이 너무 큽니다"}
            else:
                try:
                    d = script_mod.read(p)
                    named = sum(1 for ln in d["lines"] if ln.get("speaker"))
                    out["script"] = {"ok": bool(d["lines"]), "name": p.name, "lines": len(d["lines"]), "named": named,
                                     "characters": d["characters"][:20], "timed": d["timed"],
                                     "preview": [f"{ln.get('speaker') or '?'}: {ln['text']}" for ln in d["lines"][:4]],
                                     "error": None if d["lines"] else "대사 줄을 찾지 못했습니다"}
                except Exception as exc:
                    out["script"] = {"ok": False, "error": f"대본을 읽지 못했습니다 · {exc}"}
        return out

    @home.post("/api/projects")
    def new_project(body: NewProject):
        if body.start is not None and body.end is not None and body.end <= body.start:
            raise HTTPException(400, "끝 시각이 시작보다 빨라요")
        if body.run and jobs.running():
            raise HTTPException(409, "다른 작업을 분석하는 중입니다. 끝난 뒤에 시작하세요")
        try:
            pid = ws.create(body.video, body.script, body.title, body.start, body.end)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            log.exception("create failed")
            raise HTTPException(400, f"작업을 만들지 못했습니다 · {type(exc).__name__}: {exc}") from None
        warning = None
        st = jobs.status()
        if body.run:
            try:
                st = jobs.start(pid, body.options.model_dump())
            except RuntimeError as exc:           # made, but another run started in between
                warning = f"작업은 만들었지만 분석을 시작하지 못했습니다 · {exc}"
        return {"project": ws.summary(pid, st), "job": st, "warning": warning}

    @home.post("/api/projects/{pid}/run")
    def run(pid: str, body: RunRequest):
        try:
            ws.work(pid)
        except KeyError:
            raise HTTPException(404, f"없는 작업입니다: {pid}") from None
        try:
            st = jobs.start(pid, body.options.model_dump(), RUN_MODES[body.mode])
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from None
        projects.drop(pid)
        return {"job": st, "project": ws.summary(pid, st)}

    @home.get("/api/projects/{pid}/thumb")
    def thumb(pid: str):
        """A small still of the project (inside its window), cached as thumb.jpg."""
        try:
            work = ws.work(pid)
        except KeyError:
            raise HTTPException(404, "없는 작업입니다") from None
        out = work / "thumb.jpg"
        if not out.is_file():
            meta = ws.meta(pid)
            video = meta.get("video")
            if not video or not Path(video).is_file():
                raise HTTPException(404, "영상 파일이 없습니다")
            t0 = float(meta.get("t0") or 0.0)
            t1 = float(meta.get("t1") or meta.get("duration") or t0 + 60.0)
            t = t0 + min(30.0, 0.1 * max(0.0, t1 - t0))
            tmp = work / "thumb.part.jpg"
            r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", video,
                                "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", str(tmp)],
                               capture_output=True, timeout=30, creationflags=NO_WINDOW)
            if r.returncode or not tmp.is_file():
                raise HTTPException(500, "프레임을 읽지 못했습니다")
            tmp.replace(out)
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @home.post("/api/projects/{pid}/delete")
    def delete(pid: str):
        """Not a real delete: the folder moves to <workspace>/.trash so it can be brought back."""
        try:
            work = ws.work(pid)
        except KeyError:
            raise HTTPException(404, "없는 작업입니다") from None
        st = jobs.status()
        if (st and st["state"] == "running" and st.get("project") == pid) or pipeline.busy(work):
            raise HTTPException(409, "분석 중인 작업은 지울 수 없습니다. 먼저 멈추세요")
        projects.drop(pid)
        try:
            dest = ws.trash(pid)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        jobs.forget(pid)
        return {"ok": True, "moved_to": str(dest)}

    @home.post("/api/pick")
    def pick(body: PickRequest):
        return {"path": pick_dialog(body.kind)}

    @home.post("/api/open")
    def open_path(body: OpenRequest):
        if not open_folder(body.path, ws.root):
            raise HTTPException(400, "작업 공간 안의 폴더만 엽니다")
        return {"ok": True}

    return LocalOnly(Studio(home, projects))
