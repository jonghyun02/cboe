# -*- coding: utf-8 -*-
"""CBoE 데스크톱 창: 시작 화면 → 분석 → 검수 → 내보내기를 한 창에서.

    pythonw -m cboe gui                 # 창 (pywebview + WebView2), 콘솔 창 없음
    python -m cboe gui --browser        # 같은 화면을 기본 브라우저로

The studio server runs in a thread on a free localhost port. In the window, file dialogs
are the window's own (``window.pywebview.api.pick``); in a browser the page asks the
server (``/api/pick``). Closing the window during an analysis asks first and then stops it
(the next run continues from the finished stages).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

LOG_DIR = Path.home() / ".cboe"
ICON = Path(__file__).resolve().parent / "server" / "static" / "cboe.ico"
APP_ID = "CBoE.SDH.Studio"            # own taskbar button and icon instead of Python's


def _logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_DIR / "gui.log", encoding="utf-8")]
    if sys.stderr is not None:                      # pythonw has no stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def _serve(app, port: int | None = None):
    import asyncio

    import uvicorn

    from .agent_tools import free_port
    port = port or free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           log_config=None))

    def run():
        async def serve():
            loop = asyncio.get_running_loop()

            def handler(lp, ctx):            # video range requests dropped by the player on seek
                if not isinstance(ctx.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
                    lp.default_exception_handler(ctx)

            loop.set_exception_handler(handler)
            await server.serve()

        asyncio.run(serve())

    th = threading.Thread(target=run, daemon=True, name="cboe-server")
    th.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("서버를 시작하지 못했습니다")
    return f"http://127.0.0.1:{port}/", server, th


class WindowApi:
    """Functions the page can call in the desktop window (window.pywebview.api.*)."""

    def __init__(self, workspace: Path):
        self._window = None          # underscores: pywebview does not walk these for the JS bridge
        self._workspace = workspace

    def pick(self, kind: str = "video"):
        import webview
        if kind == "folder":
            res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        else:
            types = (("영상 (*.mp4;*.mkv;*.mov;*.avi;*.webm;*.m4v;*.ts;*.wmv)", "모든 파일 (*.*)") if kind == "video"
                     else ("대본 (*.txt;*.srt;*.vtt;*.csv;*.tsv;*.json)", "모든 파일 (*.*)"))
            res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=types)
        if not res:
            return None
        return str(res[0] if isinstance(res, (list, tuple)) else res)

    def open_path(self, path: str):
        """Same rule as /api/open: only folders inside the workspace."""
        from .studio import open_folder
        return open_folder(path, self._workspace) if path else False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cboe gui", description="CBoE SDH 자막 검수 보조 도구")
    ap.add_argument("--workspace", help="작업 폴더들을 둘 곳 (기본: cboe\\work 또는 CBOE_WORKSPACE)")
    ap.add_argument("--browser", action="store_true", help="창 대신 기본 브라우저로 연다")
    ap.add_argument("--port", type=int, help="서버 포트 (기본: 빈 포트)")
    ap.add_argument("--no-open", action="store_true", help="--browser 와 함께: 브라우저를 열지 않는다 (시험용)")
    args = ap.parse_args(argv)
    _logging()
    from .server.studio_app import create_studio_app
    app = create_studio_app(Path(args.workspace) if args.workspace else None)
    jobs = app.home.state.jobs
    url, server, th = _serve(app, args.port)
    logging.getLogger("cboe").info("CBoE GUI at %s", url)
    if args.browser:
        print(f"CBoE: {url}  (Ctrl+C 로 끝내기)", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            jobs.cancel()
            jobs.wait(5.0)
        return 0

    import webview
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    webview.settings["ALLOW_DOWNLOADS"] = True          # '파일 받기' on the export dialog
    api = WindowApi(app.home.state.ws.root)
    window = webview.create_window("CBoE · 근거를 제시하는 SDH 자막 검수 보조 도구", url, js_api=api,
                                   width=1600, height=1000, min_size=(1100, 720), background_color="#F4F5FB",
                                   text_select=True)
    api._window = window

    def on_closing():
        if jobs.running():
            ok = window.create_confirmation_dialog(
                "분석 중", "분석이 아직 진행 중입니다. 창을 닫으면 분석을 멈춥니다.\n"
                         "(다음에 '이어서 분석'을 누르면 끝난 단계는 건너뜁니다) 닫을까요?")
            if not ok:
                return False
            jobs.cancel()
        return True

    window.events.closing += on_closing
    storage = LOG_DIR / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage),
                      icon=str(ICON) if ICON.is_file() else None)
    finally:
        # also when the window fails: no analysis is left running (the job object would end
        # it with this process anyway, this way gui_job.json records it as stopped)
        jobs.cancel()
        jobs.wait(5.0)
        server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
