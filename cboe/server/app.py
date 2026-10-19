# -*- coding: utf-8 -*-
"""Review screen server (검수 화면, deck 3.1~3.5 and 5.10).

The page is a static single-page app (``static/``). Every change the writer makes
is stored through ``Project.set_decision`` / ``Project.set_placement``; the server
then re-runs placement (끼워 넣기) and answers with the whole screen state, so the
list, timeline and video overlay are always drawn from one fresh draft —
"고칠 때마다 배치를 다시 계산".

Video and frames are limited to the project window [t0, t1): the page never seeks
outside it and ``/api/frame`` clamps into it, so sealed parts of a test video are
not shown (same rule as ``media.Media``).
"""
from __future__ import annotations

import json
import math
from html import escape
from urllib.parse import quote
import mimetypes
import subprocess
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..state import Project
from ..util import log, read_json

STATIC = Path(__file__).resolve().with_name("static")
FINALS = ("include", "exclude", "check")
KINDS = ("sfx", "music", "speaker")
MIN_DUR = 5 / 6                       # shortest cell (DESIGN §6)
INPUT_FILES = ("project.json", "items.json", "dialogue.json", "review.json", "cuts.json", "script.json")


# --------------------------------------------------------------------- bodies
class ItemUpdate(BaseModel):
    decision: Literal["include", "exclude"] | None = None
    phrase: str | None = None
    short: str | None = None
    name: str | None = None
    scope: Literal["line", "speaker", "voice"] | None = None


class BulkUpdate(BaseModel):
    ids: list[str]
    decision: Literal["include", "exclude"] | None = None


class PlacementFix(BaseModel):
    action: Literal["exclude", "move", "swap_prev", "clear"]
    t: float | None = None


class ExportRequest(BaseModel):
    format: Literal["srt", "vtt"] = "srt"


# ---------------------------------------------------------------- utilities
def _finite(x):
    """NaN/inf -> None so the browser's JSON.parse never chokes."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_of(value: str) -> str:
    v = (value or "").strip().lower()
    if v.startswith("["):                                   # [::1]:8765
        return v.split("]")[0] + "]"
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


class LocalOnly:
    """Only this PC's own pages may use the server: the Host header must be localhost (no DNS
    rebinding), and requests that change something are refused when another site sent them."""

    def __init__(self, app, extra_hosts=()):
        self.app = app
        self.hosts = LOCAL_HOSTS | set(extra_hosts)

    def __getattr__(self, name):                            # .home, .state ... of the wrapped app
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            from urllib.parse import urlsplit
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            bad = _host_of(headers.get("host", "")) not in self.hosts
            if not bad and scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS"):
                origin = headers.get("origin")
                if origin is not None:
                    bad = origin == "null" or _host_of(urlsplit(origin).netloc) not in self.hosts
                if headers.get("sec-fetch-site") == "cross-site":
                    bad = True
            if bad:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                from fastapi.responses import PlainTextResponse
                return await PlainTextResponse("다른 곳에서 온 요청은 받지 않습니다", 403)(scope, receive, send)
        return await self.app(scope, receive, send)


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        try:
            text = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except ValueError:
            text = json.dumps(_finite(content), ensure_ascii=False, separators=(",", ":"))
        return text.encode("utf-8")


def text_width(text: str) -> float:
    """Reading width (II.2 셈법): Hangul 1, everything else 0.5."""
    return sum(1.0 if "가" <= ch <= "힣" else 0.5 for ch in text or "")


class PlacementError(ValueError):
    """A placement fix that cannot be applied (answered as 409 with a Korean message)."""


def swap_prev_time(draft: dict, item: dict, fps: float, t_min: float = 0.0) -> float:
    """Start time for '앞 칸과 바꾸기': the sound goes into the gap right before the
    cell that precedes it, as late as possible so it stays close to the real sound.

    Raises PlacementError when there is no previous cell or the gap is shorter
    than the shortest cell (5/6 s) plus the 2-frame margins.
    """
    fr2 = 2.0 / (fps or 25.0)
    iid = item["id"]
    nr = next((e for e in draft.get("no_room") or [] if e.get("item") == iid), None)
    t = nr.get("t") if nr else None
    t = float(t if t is not None else item.get("onset", item.get("t0", 0.0)))
    cells = [c for c in draft.get("cells") or [] if iid not in (c.get("refs") or [])]
    prev = [c for c in cells if float(c["start"]) < t - 1e-6]
    if not prev:
        raise PlacementError("앞 칸이 없어 바꿀 수 없습니다")
    pc = max(prev, key=lambda c: (float(c["start"]), float(c["end"])))
    gap_end = float(pc["start"])
    gap_start = max([float(c["end"]) for c in cells if c is not pc and float(c["start"]) < gap_end] + [t_min])
    text = (nr or {}).get("text") or item.get("short") or item.get("phrase") or ""
    want = min(7.0, max(MIN_DUR, text_width(text) / 14 + 1.0))
    if gap_end - gap_start - 2 * fr2 < MIN_DUR - 1e-6:
        raise PlacementError(f"앞 칸 앞의 빈틈이 {max(0.0, gap_end - gap_start):.2f}초뿐이라 옮길 자리가 없습니다")
    early, late = gap_start + fr2, gap_end - fr2 - want
    # millisecond grid without crossing either 2-frame margin
    return math.ceil(early * 1000) / 1000 if early >= late else math.floor(late * 1000) / 1000


def export_warnings(chk: dict) -> list[str]:
    out = []
    if chk.get("check"):
        out.append(f"확인 필요 {len(chk['check'])}개는 자막에 넣지 않았습니다")
    if chk.get("check_speaker"):
        out.append(f"화자 확인 필요 {len(chk['check_speaker'])}줄은 이름 없이 대사만 넣었습니다")
    if chk.get("no_room"):
        out.append(f"배치 확인 필요 {len(chk['no_room'])}개는 넣을 자리가 없어 빠졌습니다")
    if chk.get("exclude_recommended"):
        out.append(f"제외 추천 {len(chk['exclude_recommended'])}개를 아직 확정하지 않았습니다 (자막에는 넣지 않음)")
    if chk.get("unnamed"):
        out.append(f"문구가 [소리] 그대로인 효과음 {len(chk['unnamed'])}개")
    if chk.get("violations"):
        out.append(f"가독성 한도를 넘는 칸 {len(chk['violations'])}개")
    return out


class FrameGrabber:
    """JPEG stills for the page (OpenCV seek; ffmpeg when OpenCV cannot open the file)."""

    def __init__(self, video: str):
        self.video = video
        self._lock = threading.Lock()
        self._cap = None            # cv2.VideoCapture | False (cannot open) | None (not tried)
        self._cache: OrderedDict = OrderedDict()

    def _read_cv(self, t: float):
        import cv2
        if self._cap is None:
            cap = cv2.VideoCapture(self.video)
            if not cap.isOpened():
                cap.release()
                self._cap = False
            else:
                self._cap = cap
        if self._cap is False:
            return None
        self._cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = self._cap.read()
        return frame if ok else None

    def _read_ffmpeg(self, t: float):
        import cv2
        import numpy as np
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", self.video, "-frames:v", "1",
               "-an", "-sn", "-f", "image2pipe", "-vcodec", "png", "pipe:"]
        from ..util import NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if out.returncode or not out.stdout:
            return None
        return cv2.imdecode(np.frombuffer(out.stdout, np.uint8), cv2.IMREAD_COLOR)

    def jpeg(self, t: float, width: int) -> bytes:
        import cv2
        key = (round(t, 2), int(width))
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            frame = self._read_cv(t)
            if frame is None:
                frame = self._read_ffmpeg(t)
            if frame is None:
                raise RuntimeError(f"cannot read a frame at {t:.2f}s")
            h, w = frame.shape[:2]
            if width < w:
                frame = cv2.resize(frame, (int(width), max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
            if not ok:
                raise RuntimeError("jpeg encode failed")
            data = buf.tobytes()
            self._cache[key] = data
            while len(self._cache) > 120:
                self._cache.popitem(last=False)
            return data

    def close(self) -> None:
        with self._lock:
            if self._cap not in (None, False):
                self._cap.release()
            self._cap = None


# ------------------------------------------------------------------ service
class ReviewService:
    """One work folder behind one lock: decisions, placement, screen state."""

    def __init__(self, work):
        self.project = Project(work)
        self.lock = threading.RLock()
        self.version = 0
        self.error: str | None = None
        self.exports: dict[str, Path] = {}
        self._draft: dict | None = None
        self._sig = None
        self._grabber: FrameGrabber | None = None

    # ---------------------------------------------------------------- draft
    def _signature(self):
        sig = []
        for name in INPUT_FILES:
            try:
                st = self.project.path(name).stat()
                sig.append((name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                sig.append((name, None, None))
        return tuple(sig)

    def recompute(self) -> dict:
        try:
            self._draft = self.project.recompute()
            self.error = None
        except Exception as exc:                       # placement is still evolving; keep the page usable
            log.exception("placement failed")
            self.error = f"배치 계산 오류 · {type(exc).__name__}: {exc}"
            if self._draft is None:
                self._draft = read_json(self.project.path("draft.json")) or {}
        self._draft.setdefault("cells", [])
        self._draft.setdefault("no_room", [])
        self._sig = self._signature()
        return self._draft

    def reload_draft(self) -> None:
        """After Project.export(), which recomputes and writes draft.json itself."""
        d = read_json(self.project.path("draft.json"))
        if d is not None:
            self._draft = d
            self._draft.setdefault("cells", [])
            self._draft.setdefault("no_room", [])
            self.error = None
            self._sig = self._signature()

    def draft(self) -> dict:
        if self._draft is None or self._sig != self._signature():
            return self.recompute()             # first call, or inputs changed outside the page
        return self._draft

    # ---------------------------------------------------------------- items
    def item(self, item_id: str) -> dict:
        for it in self.project.items():
            if it["id"] == item_id:
                return it
        raise HTTPException(404, f"없는 후보입니다: {item_id}")

    def window(self) -> tuple[float, float, float]:
        meta = self.project.meta
        fps = float(meta.get("fps") or 25.0)
        t0 = float(meta.get("t0") or 0.0)
        t1 = meta.get("t1")
        t1 = float(t1) if t1 is not None else float(meta.get("duration") or t0)
        return t0, t1, fps

    def grabber(self, video: str) -> FrameGrabber:
        if self._grabber is None or self._grabber.video != video:
            if self._grabber is not None:
                self._grabber.close()
            self._grabber = FrameGrabber(video)
        return self._grabber

    def export_check(self) -> tuple[dict | None, str | None]:
        """``Project.export_check()`` and an error message when it cannot run."""
        try:
            return self.project.export_check(), None
        except Exception as exc:
            log.exception("export_check failed")
            return None, f"내보내기 검사 오류 · {type(exc).__name__}: {exc}"

    # ---------------------------------------------------------------- state
    def state(self, recompute: bool = False) -> dict:
        with self.lock:
            if recompute:
                self.version += 1
            draft = self.recompute() if recompute else self.draft()
            p = self.project
            meta = p.meta
            t0, t1, fps = self.window()
            eff = p.effective_items()
            lines = p.dialogue()
            cells = draft.get("cells", [])
            no_room = {e.get("item"): e for e in draft.get("no_room", [])}

            cells_by_ref: dict[str, list[str]] = {}
            for c in cells:
                for r in c.get("refs") or []:
                    cells_by_ref.setdefault(r, []).append(c["id"])
            for it in eff:
                ids = list(cells_by_ref.get(it["id"], []))
                if it["kind"] == "speaker":
                    lid = (it.get("speaker") or {}).get("line_id")
                    ids += [c for c in cells_by_ref.get(lid, []) if c not in ids]
                it["cells"] = ids
                it["no_room"] = no_room.get(it["id"])
                if it["no_room"] is not None and it["kind"] in ("sfx", "music"):
                    try:                                  # can '앞 칸과 바꾸기' work? the page says why not
                        it["swap_prev"] = {"t": swap_prev_time(draft, it, fps, t0)}
                    except PlacementError as exc:
                        it["swap_prev"] = {"error": str(exc)}

            summary = {"total": len(eff), "decided": sum(1 for it in eff if it["decided"]),
                       "final": {f: 0 for f in FINALS}, "rec": {f: 0 for f in FINALS},
                       "kind": {k: 0 for k in KINDS}, "by_kind": {k: {f: 0 for f in FINALS} for k in KINDS},
                       "no_room": len(no_room), "cells": len(cells),
                       "violations": sum(1 for c in cells if (c.get("check") or {}).get("violations"))}
            for it in eff:
                k, f = it.get("kind"), it.get("final", "check")
                r = it.get("rec") if it.get("rec") in FINALS else "check"
                summary["final"][f] = summary["final"].get(f, 0) + 1
                summary["rec"][r] += 1
                summary["kind"][k] = summary["kind"].get(k, 0) + 1
                summary["by_kind"].setdefault(k, {x: 0 for x in FINALS})[f] += 1

            video = meta.get("video")
            project = {"title": meta.get("title") or p.work.name, "fps": fps, "t0": t0, "t1": t1,
                       "duration": meta.get("duration"), "width": meta.get("width"), "height": meta.get("height"),
                       "video": bool(video and Path(video).is_file()), "video_url": "media/video",
                       "video_name": Path(video).name if video else None, "sample": bool(meta.get("sample")),
                       "work": str(p.work)}
            chk, chk_error = self.export_check()
            return {"version": self.version, "project": project,
                    "dialogue": [{k: v for k, v in ln.items() if k != "words"} for ln in lines],
                    "items": eff, "draft": draft, "cuts": p.cuts(), "characters": p.characters(),
                    "summary": summary, "export_check": chk, "error": self.error or chk_error}


# ---------------------------------------------------------------------- app
def create_app(work) -> FastAPI:
    """FastAPI app for one work folder (``project.json`` must exist)."""
    work = Path(work)
    if not (work / "project.json").is_file():
        raise FileNotFoundError(f"{work}: project.json not found")
    svc = ReviewService(work)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if svc._grabber is not None:
            svc._grabber.close()

    app = FastAPI(title="CBoE 검수 화면", default_response_class=SafeJSONResponse, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.review = svc

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        v = max(int((STATIC / n).stat().st_mtime) for n in ("index.html", "app.js", "app.css"))
        # under the studio the page lives at /p/<id>/: relative URLs resolve against this base,
        # and data-home tells the page where the project list is
        root = request.scope.get("root_path", "").rstrip("/")
        base = "/".join(quote(seg, safe="") for seg in root.split("/")) + "/"      # /p/<id>/ as a URL
        html = (html.replace("{{v}}", str(v)).replace("{{base}}", escape(base, quote=True))
                .replace("{{home}}", "/" if root else ""))
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # ---------------------------------------------------------------- state
    @app.get("/api/state")
    def get_state():
        return svc.state()

    @app.post("/api/items/bulk")
    def bulk_update(body: BulkUpdate):
        with svc.lock:
            known = {it["id"] for it in svc.project.items()}
            ids = list(dict.fromkeys(body.ids))
            missing = [i for i in ids if i not in known]
            if missing:
                raise HTTPException(404, f"없는 후보입니다: {', '.join(missing[:5])}")
            for i in ids:
                svc.project.set_decision(i, decision=body.decision)
            return svc.state(recompute=True)

    @app.post("/api/items/{item_id}")
    def update_item(item_id: str, body: ItemUpdate):
        fields = {k: getattr(body, k) for k in body.model_fields_set}
        for k in ("phrase", "short", "name"):
            if isinstance(fields.get(k), str):
                fields[k] = fields[k].strip() or None
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        with svc.lock:
            it = svc.item(item_id)
            if it["kind"] == "speaker" and ("phrase" in fields or "short" in fields):
                raise HTTPException(400, "화자 항목은 대사 글을 바꾸지 않습니다")
            if it["kind"] != "speaker" and ("name" in fields or "scope" in fields):
                raise HTTPException(400, "이름과 범위는 화자 항목에만 씁니다")
            if "phrase" in fields and fields["phrase"] is None:
                fields.setdefault("short", None)          # back to the pipeline's phrase pair
            if fields.get("scope") is None:
                fields.pop("scope", None)
            svc.project.set_decision(item_id, **fields)
            return svc.state(recompute=True)

    @app.post("/api/placement/{item_id}")
    def fix_placement(item_id: str, body: PlacementFix):
        with svc.lock:
            it = svc.item(item_id)
            if it["kind"] not in ("sfx", "music"):
                raise HTTPException(400, "배치 고치기는 효과음과 음악 후보에만 씁니다")
            t0, t1, fps = svc.window()
            p = svc.project
            if body.action == "exclude":
                p.set_decision(item_id, decision="exclude")
            elif body.action == "clear":
                p.set_placement(item_id, "clear")
            else:
                if body.action == "move":
                    if body.t is None or not math.isfinite(body.t):
                        raise HTTPException(422, "옮길 시각(t)이 필요합니다")
                    if not (t0 <= body.t < t1):
                        raise HTTPException(400, f"처리 구간({t0:.1f}~{t1:.1f}초) 밖으로는 옮길 수 없습니다")
                    t = round(float(body.t), 3)
                else:
                    try:
                        t = swap_prev_time(svc.draft(), it, fps, t0)
                    except PlacementError as exc:
                        raise HTTPException(409, str(exc)) from None
                p.set_placement(item_id, "move", t)
                eff = next((e for e in p.effective_items() if e["id"] == item_id), {})
                if not (eff.get("decided") and eff.get("final") == "include"):
                    p.set_decision(item_id, decision="include")   # moving it means the writer keeps it
            return svc.state(recompute=True)

    # --------------------------------------------------------------- export
    @app.post("/api/export")
    def export(body: ExportRequest):
        with svc.lock:
            try:
                path = Path(svc.project.export(body.format))
            except Exception as exc:
                log.exception("export failed")
                raise HTTPException(500, f"내보내기 실패 · {type(exc).__name__}: {exc}") from None
            svc.exports[body.format] = path
            svc.reload_draft()
            st = svc.state()
            chk = st["export_check"]
            return {"path": str(path), "name": path.name, "format": body.format,
                    "url": f"api/export/{body.format}", "cells": len(st["draft"].get("cells", [])),
                    "warnings": export_warnings(chk), "export_check": chk, "state": st}

    @app.get("/api/export/{fmt}")
    def download(fmt: Literal["srt", "vtt"]):
        with svc.lock:
            path = svc.exports.get(fmt)
            if path is None or not path.is_file():
                path = Path(svc.project.export(fmt))
                svc.exports[fmt] = path
                svc.reload_draft()
        media = "application/x-subrip" if fmt == "srt" else "text/vtt; charset=utf-8"
        return FileResponse(path, media_type=media, filename=path.name)

    # ---------------------------------------------------------------- media
    @app.get("/media/video", include_in_schema=False)
    def video():
        path = svc.project.meta.get("video")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "영상 파일이 없습니다")
        return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or "video/mp4")

    @app.get("/api/frame")
    def frame(t: float = Query(...), w: int = Query(640, ge=32, le=3840)):
        if not math.isfinite(t):
            raise HTTPException(400, "t must be a finite number")
        path = svc.project.meta.get("video")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "영상 파일이 없습니다")
        t0, t1, fps = svc.window()
        t = min(max(t, t0), max(t0, t1 - 1.0 / fps))        # never decode outside the project window
        try:
            data = svc.grabber(path).jpeg(t, w)
        except Exception as exc:
            raise HTTPException(500, f"프레임을 읽지 못했습니다 · {exc}") from None
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    return app
