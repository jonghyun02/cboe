# -*- coding: utf-8 -*-
"""CBoE command line.

    python -m cboe init <work> --video V [--script S] [--start 0] [--end 290] [--title T]
                               [--children | --profile adult|children]
    python -m cboe run <work> [--stages a,b] [--force a,b|all] [--no-llm] [--agent-limit N]
                              [--llm-model M] [--jobs N] [--llm-scope all|useful] [--asr-detect-language]
    python -m cboe serve <work> [--port 8765] [--no-browser]
    python -m cboe export <work> [--format srt|vtt|ttml]
    python -m cboe check <file.srt|.vtt|.ttml|draft.json> [--fps F] [--cuts cuts.json] [--children] [--json] [--all]
    python -m cboe doctor
    python -m cboe gui [--workspace W] [--browser] [--port N] [--no-open]

``init --children`` marks the work folder as a children's programme (project.json "profile":
"children", 11 characters a second instead of 14). ``run --stages`` / ``--force`` take the names of
``pipeline.STAGES``; the placement is no stage of its own: it runs at the end of every run that
has candidates and dialogue. A wrong name ends with one line, before anything is cleared.
``check`` runs the independent guide check (cboe.check) on a finished file: clause by clause,
obligations and recommendations apart; the exit code is 1 when an obligation is broken.
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
    a.add_argument("--children", action="store_true", help="어린이 프로그램 (읽기 속도 초당 11자)")
    a.add_argument("--profile", choices=["adult", "children"], help="프로그램 종류 (적지 않으면 그대로, 처음엔 adult)")
    from .pipeline import STAGES
    r = sub.add_parser("run")
    r.add_argument("work")
    r.add_argument("--stages", default="",
                   help="돌릴 단계, 쉼표로 (적지 않으면 전부): " + ", ".join(STAGES) +
                   ". 끼워 넣기(placement)는 실행 끝에 늘 다시 한다")
    r.add_argument("--force", default="", help="결과를 지우고 다시 할 단계, 쉼표로 (뒤 단계도 다시): " +
                   ", ".join(STAGES) + " 또는 all")
    r.add_argument("--no-llm", action="store_true", help="화면·대사 근거와 에이전트를 LLM 없이 규칙으로만")
    r.add_argument("--agent-limit", type=int, default=40, help="에이전트로 다시 볼 후보 수 한도")
    r.add_argument("--llm-model", default="sonnet")
    r.add_argument("--jobs", type=int, default=3, help="동시에 돌릴 LLM 호출 수")
    r.add_argument("--llm-scope", choices=["all", "useful"], default="all",
                   help="LLM 에 물을 후보: all 은 전부, useful 은 답이 추천을 바꿀 수 있는 것만")
    r.add_argument("--asr-detect-language", action="store_true",
                   help="받아쓰기에서 구간마다 언어를 알아낸다 (외국어 대사 표시용, 기본은 한 언어로)")
    s = sub.add_parser("serve")
    s.add_argument("work")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("work")
    e.add_argument("--format", default="srt", choices=["srt", "vtt", "ttml"])
    c = sub.add_parser("check", help="자막 파일을 넷플릭스 한국어 SDH 지침에 대어 본다")
    c.add_argument("file", help="srt · vtt · ttml 자막 파일이나 draft.json")
    c.add_argument("--fps", type=float, help="영상의 초당 프레임 수 (draft.json·ttml 은 파일 안의 값을 쓴다)")
    c.add_argument("--cuts", help="장면 전환 시각 파일 (cuts.json): 주면 전환 규칙도 본다")
    c.add_argument("--children", action="store_true", help="어린이 프로그램 (읽기 속도 초당 11자)")
    c.add_argument("--json", action="store_true", help="결과를 JSON 으로")
    c.add_argument("--all", action="store_true", help="조항마다 걸린 칸을 모두 적는다")
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
        from pathlib import Path

        from . import style
        from .stages.ingest import init
        from .util import write_json
        meta = init(args.work, args.video, args.script, args.start, args.end, args.title)
        # the profile is this command's own entry in project.json: init() keeps what was there
        profile = "children" if args.children else args.profile
        meta["profile"] = style.profile_name(profile if profile else meta.get("profile"))
        write_json(Path(args.work) / "project.json", meta)
        print(json.dumps(meta, ensure_ascii=False, indent=1))
    elif args.cmd == "run":
        from . import pipeline
        cfg = {"llm": not args.no_llm, "agent_limit": args.agent_limit, "llm_model": args.llm_model,
               "jobs": args.jobs, "llm_scope": args.llm_scope, "asr_detect_language": args.asr_detect_language}
        stages = [x.strip() for x in args.stages.split(",") if x.strip()] or None
        force = [x.strip() for x in args.force.split(",") if x.strip()]
        wrong = [x for x in stages or [] if x not in STAGES] + [x for x in force if x not in STAGES and x != "all"]
        if wrong:
            hint = " · 끼워 넣기(placement)는 단계가 아니라 실행 끝에 늘 다시 한다" if "placement" in wrong else ""
            print(f"cboe run: 없는 단계 {', '.join(wrong)} — 고를 수 있는 단계: {', '.join(STAGES)}"
                  f"{' (--force 는 all 도)' if force else ''}{hint}", file=sys.stderr)
            return 2
        rep = pipeline.run(args.work, stages, force, cfg)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    elif args.cmd == "serve":
        from .server.__main__ import main as serve_main
        serve_main([args.work, "--port", str(args.port)] + (["--no-browser"] if args.no_browser else []))
    elif args.cmd == "export":
        from .state import Project
        res = Project(args.work).export(args.format, report=True)
        print(res["path"])
        sm = res["check"]["summary"]
        print(f"지침 검사: 의무 {sm['must']}건 ({sm['cells_must']}칸) · 권고 {sm['should']}건 ({sm['cells_should']}칸)"
              f" · 자세히: python -m cboe check \"{res['path']}\" --fps {res['check']['fps']:g}", file=sys.stderr)
    elif args.cmd == "check":
        from . import check
        return check.main(args.file, fps=args.fps, cuts=args.cuts, profile="children" if args.children else None,
                          as_json=args.json, show_all=args.all)
    elif args.cmd == "doctor":
        from . import paths
        from .util import device, device_name
        dev = device()
        print(f"device  {dev}" + (f" ({device_name()})" if device_name() else " (no CUDA/XPU GPU: slow)"))
        for k, v in paths.check().items():
            print(f"{'OK ' if v else 'MISSING'} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
