# -*- coding: utf-8 -*-
"""CBoE command line.

    python -m cboe init <work> --video V [--script S] [--start 0] [--end 290] [--title T]
    python -m cboe run <work> [--stages a,b] [--force a,b|all] [--no-llm] [--agent-limit N]
    python -m cboe serve <work> [--port 8765]
    python -m cboe export <work> [--format srt|vtt]
    python -m cboe doctor
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None):
    from .util import setup_logging
    if (argv if argv is not None else sys.argv[1:])[:1] != ["gui"]:   # the GUI logs to ~/.cboe/gui.log
        setup_logging()
    ap = argparse.ArgumentParser(prog="cboe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init")
    a.add_argument("work")
    a.add_argument("--video", required=True)
    a.add_argument("--script")
    a.add_argument("--start", type=float, default=0.0)
    a.add_argument("--end", type=float)
    a.add_argument("--title")
    r = sub.add_parser("run")
    r.add_argument("work")
    r.add_argument("--stages", default="")
    r.add_argument("--force", default="")
    r.add_argument("--no-llm", action="store_true", help="화면·대사 근거와 에이전트를 LLM 없이 규칙으로만")
    r.add_argument("--agent-limit", type=int, default=40, help="에이전트로 다시 볼 후보 수 한도")
    r.add_argument("--llm-model", default="sonnet")
    r.add_argument("--jobs", type=int, default=3, help="동시에 돌릴 LLM 호출 수")
    s = sub.add_parser("serve")
    s.add_argument("work")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("work")
    e.add_argument("--format", default="srt", choices=["srt", "vtt"])
    sub.add_parser("doctor")
    g = sub.add_parser("gui", help="데스크톱 창 (작업 목록 · 분석 · 검수)")
    g.add_argument("--workspace")
    g.add_argument("--browser", action="store_true")
    g.add_argument("--port", type=int)
    g.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "gui":
        from .gui import main as gui_main
        return gui_main((["--workspace", args.workspace] if args.workspace else []) +
                        (["--browser"] if args.browser else []) + (["--port", str(args.port)] if args.port else []) +
                        (["--no-open"] if args.no_open else []))

    if args.cmd == "init":
        from .stages.ingest import init
        meta = init(args.work, args.video, args.script, args.start, args.end, args.title)
        print(json.dumps(meta, ensure_ascii=False, indent=1))
    elif args.cmd == "run":
        from . import pipeline
        cfg = {"llm": not args.no_llm, "agent_limit": args.agent_limit, "llm_model": args.llm_model,
               "jobs": args.jobs}
        stages = [x for x in args.stages.split(",") if x] or None
        force = [x for x in args.force.split(",") if x]
        rep = pipeline.run(args.work, stages, force, cfg)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    elif args.cmd == "serve":
        from .server.__main__ import main as serve_main
        serve_main([args.work, "--port", str(args.port)] + (["--no-browser"] if args.no_browser else []))
    elif args.cmd == "export":
        from .state import Project
        print(Project(args.work).export(args.format))
    elif args.cmd == "doctor":
        from . import paths
        for k, v in paths.check().items():
            print(f"{'OK ' if v else 'MISSING'} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
