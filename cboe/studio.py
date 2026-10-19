# -*- coding: utf-8 -*-
"""작업 목록과 분석 실행 (GUI 시작 화면의 바탕).

- ``Workspace``: the folder that holds one work folder per project; summaries for the
  project list (status, candidate and decision counts, exports).
- ``JobManager``: runs ``python -m cboe run <work> ...`` as a windowless subprocess (one at
  a time — the stages share one 12 GB GPU), follows its log (``== stage x``, ``PROGRESS``
  lines) for the progress screen, keeps the log in ``<work>/gui_run.log`` and the outcome in
  ``<work>/gui_job.json``, and can cancel the whole process tree. The run is put in a Windows
  job object that is closed with this process, so it never outlives a crashed or killed GUI.
"""
from __future__ import annotations

import ast
import collections
import copy
import datetime as dt
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import pipeline
from .util import NO_WINDOW, ROOT, log, read_json, write_json

STAGE_LABELS = {
    "ingest": "영상 준비", "separate": "음원 분리", "cuts": "장면 나누기", "sed": "소리 검출",
    "asr": "받아쓰기", "align": "대본 정렬", "sfx": "효과음 후보", "music": "음악 후보",
    "speaker": "화자 근거", "screen": "화면 근거", "dialog_ev": "대사 근거", "recommend": "추천",
    "agent": "재확인 에이전트", "placement": "초안 배치",
}
ALL_STAGES = pipeline.STAGES + ["placement"]
# the file each stage writes last (sed: env.npz after the three score files)
KEY_OUTPUT = {
    "ingest": "audio/mix_16k.wav", "separate": "audio/effects_16k.wav", "cuts": "cuts.json",
    "sed": "env.npz", "asr": "asr.json", "align": "dialogue.json", "sfx": "sfx.json",
    "music": "music.json", "speaker": "speaker.json", "screen": "screen.json", "dialog_ev": "dialog_ev.json",
    "recommend": "items.json", "agent": "agent_done.json", "placement": "draft.json",
}
STAGE_START = re.compile(r"== stage (\w+)$")
STAGE_END = re.compile(r"== stage (\w+): (\{.*\})\s*$")
PROGRESS = re.compile(r"PROGRESS (\w+) (\d+)/(\d+) ?(.*)$")
CLEARED = re.compile(r"cleared outputs of: (.*)$")
BAD_ID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class JobBusy(RuntimeError):
    """Another analysis is running (here, in another window, or from the command line)."""


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def default_workspace() -> Path:
    return Path(os.environ.get("CBOE_WORKSPACE", str(ROOT / "work")))


def safe_id(title: str, root: Path) -> str:
    """ASCII folder name for a new project (tools on Windows are happier with ASCII paths)."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:40]
    if len(base) < 3:
        base = "work-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    pid, k = base, 2
    while (root / pid).exists():
        pid, k = f"{base}-{k}", k + 1
    return pid


def inside(path, root: Path) -> Path | None:
    """``path`` resolved, when it lies in ``root`` (the folder itself included)."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
    except (OSError, ValueError):
        return None
    return p if p == r or r in p.parents else None


def open_folder(path, root: Path) -> bool:
    """Show a folder of the workspace in Explorer; anything else is refused."""
    p = inside(path, root)
    if p is None or not p.is_dir() or not hasattr(os, "startfile"):
        return False
    os.startfile(str(p))  # noqa: S606 (local desktop app, folder inside the workspace)
    return True


# ------------------------------------------------------------------ projects
class Workspace:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or default_workspace())
        self.root.mkdir(parents=True, exist_ok=True)

    def work(self, pid: str) -> Path:
        """The work folder of ``pid``. The id must be the folder's own name: no separators,
        no '..', no other spelling of the same folder ('DEMO', 'demo.')."""
        if not pid or pid.startswith(".") or BAD_ID.search(pid):
            raise KeyError(pid)
        try:
            root = self.root.resolve()
            w = (root / pid).resolve()
        except (OSError, ValueError):
            raise KeyError(pid) from None
        if w.parent != root or w.name != pid or not (w / "project.json").is_file():
            raise KeyError(pid)
        return w

    def meta(self, pid: str) -> dict:
        return read_json(self.work(pid) / "project.json", {}) or {}

    def trash(self, pid: str) -> Path:
        work = self.work(pid)
        dest = self.root / ".trash" / f"{pid}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            work.rename(dest)
        except OSError as exc:
            raise RuntimeError(f"작업 폴더를 옮기지 못했습니다 (다른 프로그램이 파일을 여는 중일 수 있음) · {exc}") from None
        return dest

    def ids(self) -> list[str]:
        out = [d.name for d in self.root.iterdir()
               if d.is_dir() and not d.name.startswith(".") and (d / "project.json").is_file()]
        return sorted(out, key=lambda n: (self.root / n / "project.json").stat().st_mtime, reverse=True)

    def summary(self, pid: str, job: dict | None = None) -> dict:
        from urllib.parse import quote

        from .state import Project
        w = self.work(pid)
        p = Project(w)
        meta = p.meta
        done = [s for s in pipeline.STAGES if _finished(w, s)]
        items, draft = w / "items.json", w / "draft.json"
        if items.exists() and draft.exists() and draft.stat().st_mtime >= items.stat().st_mtime:
            done.append("placement")                  # a draft older than the candidates is stale
        running = bool(job and job.get("project") == pid and job.get("state") == "running")
        last = read_json(w / "gui_job.json")
        if running:
            status = "running"
        elif pipeline.busy(w):
            status = "external"                       # another window or a command line run
        elif len(done) == len(ALL_STAGES):
            status = "analysed"
        elif last and last.get("state") in ("failed", "cancelled"):
            status = last["state"]
        elif any(s != "ingest" for s in done):
            status = "partial"
        else:
            status = "new"
        counts = recs = None
        decided = total = 0
        if items.exists():
            try:
                eff = p.effective_items()
                counts = dict(collections.Counter(it["kind"] for it in eff))
                recs = dict(collections.Counter(it["final"] for it in eff))
                decided = sum(1 for it in eff if it.get("decided"))
                total = len(eff)
            except Exception:                         # a half-written file during a run
                counts = recs = None
        agent = None
        ad = read_json(w / "agent_done.json")
        if ad:
            failed = ad.get("failed")
            agent = {"done": int(ad.get("total") or 0), "left": len(ad.get("left") or []),
                     "failed": len(failed) if isinstance(failed, list) else int(failed or 0)}
        exports = []
        if (w / "export").is_dir():
            for f in sorted((w / "export").iterdir()):
                if f.suffix.lower() in (".srt", ".vtt"):
                    exports.append({"name": f.name, "path": str(f),
                                    "mtime": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
        t0, t1 = float(meta.get("t0") or 0.0), meta.get("t1")
        video = meta.get("video") or ""
        script = meta.get("script") or ""
        return {
            "id": pid, "title": meta.get("title") or pid, "video": video, "video_name": Path(video).name if video else "",
            "video_ok": bool(video and Path(video).is_file()), "script": script,
            "script_name": Path(script).name if script else "", "t0": t0, "t1": t1,
            "minutes": round(((float(t1) if t1 else t0) - t0) / 60, 1), "duration": meta.get("duration"),
            "status": status, "stages_done": done, "progress": round(len(done) / len(ALL_STAGES), 3),
            "counts": counts, "recs": recs, "decided": decided, "total": total, "exports": exports,
            "modified": dt.datetime.fromtimestamp((w / "project.json").stat().st_mtime).isoformat(timespec="seconds"),
            "review_url": f"/p/{quote(pid, safe='')}/" if items.exists() else None,
            "last_run": last, "work": str(w), "sample": bool(meta.get("sample")), "agent": agent,
        }

    def summaries(self, job: dict | None = None) -> list[dict]:
        out = []
        for pid in self.ids():
            try:
                out.append(self.summary(pid, job))
            except Exception as exc:                  # one broken folder must not hide the others
                out.append({"id": pid, "title": pid, "status": "broken", "error": f"{type(exc).__name__}: {exc}"})
        return out

    def create(self, video: str, script: str | None, title: str | None, start: float | None,
               end: float | None) -> str:
        from .stages.ingest import init
        video = str(Path(video).expanduser())
        if not Path(video).is_file():
            raise ValueError(f"영상 파일이 없습니다: {video}")
        if script and not Path(script).is_file():
            raise ValueError(f"대본 파일이 없습니다: {script}")
        title = (title or Path(video).stem).strip()
        pid = safe_id(title, self.root)
        work = self.root / pid
        try:
            init(work, video, script or None, float(start or 0.0), end, title)
        except Exception as exc:
            if work.exists() and not any(work.iterdir()):
                work.rmdir()
            if isinstance(exc, ValueError):
                msg = str(exc)
                if "no audio" in msg:
                    raise ValueError("소리가 없는 영상입니다") from None
                if "bad window" in msg:
                    raise ValueError("처리 구간이 영상 길이를 벗어납니다") from None
            raise
        return pid


# ------------------------------------------------------------- job object
class KillOnClose:
    """Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: every process put in it
    (and everything those start) ends when this process ends, however it ends."""

    def __init__(self):
        self.handle = None
        self._resume = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        self._resume = ctypes.WinDLL("ntdll").NtResumeProcess
        self._resume.argtypes = [wintypes.HANDLE]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        h = k32.CreateJobObjectW(None, None)
        if not h:
            return
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # ExtendedLimitInformation
            k32.CloseHandle(h)
            return
        self.handle, self._k32 = h, k32

    def adopt_and_resume(self, proc: subprocess.Popen) -> bool:
        """Put a process started with CREATE_SUSPENDED in the job, then let it run."""
        ok = False
        if self.handle is not None:
            ok = bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))
        if self._resume is not None:
            self._resume(int(proc._handle))
        return ok


CREATE_SUSPENDED = 0x00000004


# ----------------------------------------------------------------------- jobs
class JobManager:
    """One analysis subprocess at a time."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.lock = threading.RLock()            # status() is also called while start() holds it
        self.job: dict | None = None
        self.proc: subprocess.Popen | None = None
        self._cancel = False
        self._jobobj = KillOnClose()

    # ------------------------------------------------------------- control
    def running(self) -> bool:
        return bool(self.job and self.job["state"] == "running")

    def start(self, pid: str, options: dict | None = None, force: list[str] | None = None) -> dict:
        with self.lock:
            if self.running():
                raise JobBusy(f"이미 분석 중입니다: {self.job['title']}")
            work = self.ws.work(pid)
            if pipeline.busy(work):
                raise JobBusy("이 작업은 다른 창이나 명령줄에서 분석하는 중입니다")
            o = {"llm": True, "agent_limit": 40, "jobs": 3, "model": "sonnet", **(options or {})}
            force = [f for f in (force or []) if f in pipeline.STAGES or f == "all"]
            cmd = self.command(work, o, force)
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            env.pop("CLAUDECODE", None)
            meta = read_json(work / "project.json", {}) or {}
            try:
                proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        creationflags=NO_WINDOW | (CREATE_SUSPENDED if os.name == "nt" else 0))
            except OSError as exc:
                raise RuntimeError(f"분석을 시작하지 못했습니다 · {exc}") from None
            try:
                if not self._jobobj.adopt_and_resume(proc):
                    log.warning("analysis process is not in the kill-on-close job object")
            except Exception:                                    # never leave it suspended
                log.exception("job object")
                proc.kill()
                raise RuntimeError("분석을 시작하지 못했습니다 (프로세스 묶기 실패)") from None
            stages = [{"name": s, "label": STAGE_LABELS[s], "state": "pending", "seconds": None, "note": ""}
                      for s in ALL_STAGES]
            job = {"project": pid, "title": meta.get("title") or pid, "state": "running", "started": now_iso(),
                   "t_start": time.time(), "finished": None, "options": o, "force": force, "stages": stages,
                   "current": None, "log": collections.deque(maxlen=400), "error": None, "returncode": None,
                   "cmd": cmd}
            self.job, self.proc, self._cancel = job, proc, False
            threading.Thread(target=self._follow, args=(proc, job, work), daemon=True).start()
            return self.status()

    @staticmethod
    def command(work: Path, o: dict, force: list[str]) -> list[str]:
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
            py = py.with_name("python.exe")              # pythonw has no usable stdout pipe
        cmd = [str(py), "-m", "cboe", "run", str(work), "--agent-limit", str(int(o["agent_limit"])),
               "--jobs", str(int(o["jobs"])), "--llm-model", str(o["model"])]
        if not o["llm"]:
            cmd.append("--no-llm")
        if force:
            cmd += ["--force", ",".join(force)]
        return cmd

    def cancel(self) -> dict | None:
        with self.lock:
            proc = self.proc
            if not self.running() or proc is None or proc.poll() is not None:
                return self.status()
            self._cancel = True
        # the run starts ffmpeg / claude children: take the whole tree down
        r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=NO_WINDOW)
        if r.returncode != 0 and proc.poll() is None:
            proc.kill()
        return self.status()

    def wait(self, timeout: float = 5.0) -> bool:
        """Until the running job has been recorded (after cancel); False on timeout."""
        t = time.time()
        while self.running() and time.time() - t < timeout:
            time.sleep(0.05)
        return not self.running()

    def forget(self, pid: str) -> None:
        """Drop a finished job of a project that was deleted."""
        with self.lock:
            if self.job and self.job["project"] == pid and not self.running():
                self.job = None

    # -------------------------------------------------------------- follow
    def _stage(self, job, name):
        return next((s for s in job["stages"] if s["name"] == name), None)

    def _parse(self, job, line: str) -> None:
        m = STAGE_END.search(line)
        if m:
            st = self._stage(job, m.group(1))
            if st:
                try:
                    info = ast.literal_eval(m.group(2))
                except (ValueError, SyntaxError):
                    info = {}
                if not isinstance(info, dict):
                    info = {}
                st["state"] = "skipped" if info.get("skipped") else "done"
                st["seconds"] = info.get("seconds")
                st["note"] = _note(m.group(1), info)
            if job["current"] and job["current"]["stage"] == m.group(1):
                job["current"] = None
            return
        m = STAGE_START.search(line)
        if m:
            st = self._stage(job, m.group(1))
            if st:
                st["state"] = "running"
                job["current"] = {"stage": st["name"], "label": st["label"], "done": None, "total": None, "note": ""}
            return
        m = PROGRESS.search(line)
        if m and job["current"] and job["current"]["stage"] == m.group(1):
            job["current"].update(done=int(m.group(2)), total=int(m.group(3)), note=m.group(4).strip())
            return
        m = CLEARED.search(line)
        if m:
            job["cleared"] = [x.strip() for x in m.group(1).split(",")]

    def _follow(self, proc: subprocess.Popen, job: dict, work: Path) -> None:
        """Read the run's output until it ends, whatever goes wrong on this side: an
        unread pipe would stall the run, and only this thread ends the 'running' state."""
        logf = None
        try:
            logf = open(work / "gui_run.log", "a", encoding="utf-8")
            logf.write(f"\n===== {now_iso()} {' '.join(job['cmd'])}\n")
        except OSError:
            log.exception("gui_run.log")
            logf = None
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if logf is not None:
                    try:
                        logf.write(line + "\n")
                        logf.flush()
                    except OSError:                              # disk full etc.: keep showing progress
                        log.exception("gui_run.log")
                        logf = None
                try:
                    with self.lock:
                        job["log"].append(line)
                        self._parse(job, line)
                except Exception:                                # one bad line must not stop the reader
                    log.exception("job log line")
        except Exception:
            log.exception("job output")
            if proc.poll() is None:
                proc.kill()
        finally:
            rc = proc.wait()
            if logf is not None:
                try:
                    logf.close()
                except OSError:
                    pass
            with self.lock:
                job["returncode"] = rc
                job["finished"] = now_iso()
                if rc == 0:
                    job["state"] = "done"                       # it finished before a late cancel
                elif self._cancel and self.job is job:
                    job["state"] = "cancelled"
                else:
                    job["state"] = "failed"
                    tb = [ln for ln in job["log"] if ln.strip()][-6:]
                    job["error"] = "\n".join(tb) or f"종료 코드 {rc}"
                for st in job["stages"]:
                    if st["state"] == "running":
                        st["state"] = "failed" if job["state"] == "failed" else job["state"]
                job["current"] = None
                try:
                    write_json(work / "gui_job.json", {"state": job["state"], "started": job["started"],
                                                       "finished": job["finished"], "error": job["error"],
                                                       "seconds": round(time.time() - job["t_start"], 1),
                                                       "options": job["options"], "force": job["force"],
                                                       "stages": job["stages"]})
                except OSError:
                    log.exception("gui_job.json")

    def status(self) -> dict | None:
        with self.lock:                          # the follower thread appends to the log
            return self._status()

    def _status(self) -> dict | None:
        job = self.job
        if job is None:
            return None
        out = copy.deepcopy({k: v for k, v in job.items() if k not in ("log", "t_start", "cmd")})
        out["log_tail"] = list(job["log"])[-60:]
        out["elapsed"] = round((time.time() if job["state"] == "running" else
                                _ts(job["finished"]) or time.time()) - job["t_start"], 1)
        done = sum(1 for s in job["stages"] if s["state"] in ("done", "skipped"))
        cur = job["current"]
        frac = done
        if cur and cur.get("total"):
            frac += min(cur["done"] / cur["total"], 1.0)
        out["fraction"] = round(frac / len(job["stages"]), 3)
        return out


def _finished(work: Path, stage: str) -> bool:
    """The stage's last file is there (screen and dialog evidence are saved per batch and
    marked complete only at the end of the stage)."""
    f = work / KEY_OUTPUT[stage]
    if not f.exists():
        return False
    if stage in ("screen", "dialog_ev"):
        return (read_json(f, {}) or {}).get("complete") is not False
    return True


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _note(stage: str, info: dict) -> str:
    """Short Korean result line for a finished stage."""
    if info.get("skipped"):
        return "이전 결과 사용"
    if stage == "sfx" and "candidates" in info:
        return f"후보 {info['candidates']}개 (목록 밖 {info.get('unknown', 0)})"
    if stage == "music" and "candidates" in info:
        return f"구간 {info.get('segments', 0)}개 · 후보 {info['candidates']}개"
    if stage == "asr" and "words" in info:
        return f"{info['words']}어절"
    if stage == "align" and "lines" in info:
        return f"대사 {info['lines']}줄"
    if stage == "cuts" and "cuts" in info:
        return f"장면 전환 {info['cuts']}개"
    if stage == "agent" and "ran" in info:
        return f"{info['ran']}개 다시 확인" + (f" · 실패 {info['failed']}" if info.get("failed") else "")
    if stage == "placement" and "cells" in info:
        return f"자막 {info['cells']}칸"
    if stage == "recommend" and "agent_queue" in info:
        return f"에이전트로 넘김 {info['agent_queue']}개"
    return ""
