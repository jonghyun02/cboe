# -*- coding: utf-8 -*-
"""Start the review screen for one work folder.

    python -m cboe.server <work_dir> [--port 8765] [--host 127.0.0.1] [--no-browser]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import webbrowser
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cboe.server", description="CBoE SDH 자막 검수 화면")
    ap.add_argument("work", help="작업 폴더 (project.json, items.json, dialogue.json ...)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않는다")
    ap.add_argument("--log-level", default="warning", choices=["debug", "info", "warning", "error"])
    args = ap.parse_args(argv)

    work = Path(args.work).resolve()
    if not (work / "project.json").is_file():
        ap.error(f"{work}: project.json 이 없습니다")

    import uvicorn

    from ..util import setup_logging
    from .app import LocalOnly, create_app

    setup_logging()
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    # the page must be reachable at the address printed below (other Host headers are refused)
    app = LocalOnly(create_app(work), extra_hosts=[host.lower()])
    url = f"http://{host}:{args.port}/"
    print(f"CBoE 검수 화면: {url}  (작업 폴더 {work})", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level))

    async def serve():
        # The browser drops video range requests whenever it seeks; on Windows the proactor
        # loop logs each one as a ConnectionResetError traceback. They are not errors here.
        loop = asyncio.get_running_loop()

        def handler(lp, ctx):
            if not isinstance(ctx.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
                lp.default_exception_handler(ctx)

        loop.set_exception_handler(handler)
        await server.serve()

    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
