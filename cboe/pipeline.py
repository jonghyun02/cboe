# -*- coding: utf-8 -*-
"""Runs the stages of deck 5.1 in order, skipping stages whose output exists.

① 후보 검출 · 근거 수집: ingest → script → separate → cuts → sed → asr → align → cues → sfx
                         → music → speaker → screen → dialog_ev   (align uses SED voice activity;
                         cues = the sounds the script writes down, with their time windows)
② 추천 · 에이전트 재확인: recommend → agent
③ 초안 배치: placement (state.Project.recompute)

``ingest`` extracts the audio, ``script`` reads the script file. The script stage looks at
the file every run: when it has changed it reads it again and removes the alignment and the
results below it (its ``cleared`` result), which this run then makes again. The audio stages
and the transcription do not depend on the script.

``run_log.json`` holds, per stage, the result of the run that did the work — a later run that
skips the stage does not overwrite it. Every run is also appended to ``run_history.json``.
"""
from __future__ import annotations

import datetime as dt
import importlib
import time
from pathlib import Path

from filelock import FileLock, Timeout

from . import guard
from .state import Project
from .util import log, read_json, write_json

STAGES = ["ingest", "script", "separate", "cuts", "sed", "asr", "align", "cues", "sfx", "music", "speaker", "screen",
          "dialog_ev", "recommend", "agent"]

# output file(s) per stage; --force <stage> deletes them first
OUTPUTS = {
    "ingest": ["audio/mix_48k.wav", "audio/mix_16k.wav"],
    "script": ["script.json"],
    "separate": [f"audio/{s}_{r}.wav" for s in ("speech", "music", "effects") for r in ("48k", "16k")],
    "cuts": ["cuts.json"],
    "asr": ["asr.json"],
    "align": ["dialogue.json", "scenes.json"],
    "cues": ["cues.json"],                  # made again every run (cheap, pure): not skipped when present
    "sed": ["sed_effects.npz", "sed_mix.npz", "sed_music.npz", "env.npz"],
    "sfx": ["sfx.json"],
    "music": ["music.json"],
    "speaker": ["speaker.json", "speaker.part.json"],   # the part file: a stopped run goes on from it
    "screen": ["screen.json"],
    "dialog_ev": ["dialog_ev.json"],
    "recommend": ["items.json"],
    "agent": ["agent_done.json", "agent_results.json"],   # not skipped when present: continues the queue
}

# stage -> stages that read its output. Forcing a stage also clears everything that depends on
# it: candidate ids are sequence numbers, so results keyed by an old id must not survive.
USED_BY = {
    "ingest": ["separate", "cuts", "sed", "asr"],
    # the transcription only takes names from the script as hints: a changed script does not redo it
    "script": ["align"],
    "separate": ["sed", "asr"],
    "cuts": ["speaker", "screen", "recommend"],
    "sed": ["sfx", "music", "align"],
    "asr": ["align"],
    # cues is not listed: it is made again on every run, and recommend reads the cues afresh
    "align": ["speaker", "dialog_ev", "recommend"],
    "cues": ["recommend"],
    "sfx": ["screen", "dialog_ev", "recommend"],
    "music": ["dialog_ev", "recommend"],
    "speaker": ["recommend"],
    "screen": ["recommend"],
    "dialog_ev": ["recommend"],
    # agent results are matched to candidates by fingerprint (kind@onset), so re-deciding or
    # re-detecting keeps the finished re-checks of unchanged candidates; only --force agent clears them
    "recommend": [],
    "agent": [],
}


def dependents(names) -> list[str]:
    """The given stages and every stage that depends on them, in pipeline order."""
    todo, seen = list(names), set()
    while todo:
        s = todo.pop()
        if s in seen:
            continue
        seen.add(s)
        todo.extend(USED_BY.get(s, []))
    return [s for s in STAGES if s in seen]


RUN_LOCK = ".run.lock"
RUN_HISTORY = "run_history.json"      # every run: when, which stages ran or were skipped, how long
RUN_HISTORY_MAX = 200


def busy(work) -> bool:
    """True while some process runs the pipeline on this work folder."""
    lock = FileLock(str(Path(work) / RUN_LOCK))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return True
    lock.release()
    return False


def run(work, stages: list[str] | None = None, force: list[str] | tuple = (), cfg: dict | None = None) -> dict:
    p = Project(work)
    meta = p.meta
    guard.check_window(meta.get("video", ""), float(meta.get("t0") or 0.0), float(meta.get("t1") or 0.0),
                       meta.get("duration"))
    # one run per work folder: a second GUI, a CLI run or a leftover process would overwrite the same files
    lock = FileLock(str(p.path(RUN_LOCK)))
    try:
        lock.acquire(timeout=2)
    except Timeout:
        raise RuntimeError(f"{p.work}: 다른 곳에서 이 작업을 분석하는 중입니다") from None
    try:
        return _run(p, stages, force, cfg)
    finally:
        lock.release()


def _run(p: Project, stages: list[str] | None, force, cfg: dict | None) -> dict:
    cfg = dict(cfg or {})
    todo = stages or STAGES
    for name in list(todo) + [f for f in force if f != "all"]:
        if name not in STAGES:
            raise ValueError(f"unknown stage {name}")
    clear = STAGES if "all" in force else dependents(force)
    for name in clear:
        for rel in OUTPUTS.get(name, []):
            f = p.path(rel)
            if f.exists():
                f.unlink()
    if clear:
        log.info("cleared outputs of: %s", ", ".join(clear))
        # a cleared stage must run again even when --stages names only some of them
        todo = [s for s in STAGES if s in set(todo) | set(clear)] if stages else STAGES
    report = read_json(p.path("run_log.json"), {}) or {}
    history = read_json(p.path(RUN_HISTORY), []) or []
    entry = {"started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None,
             "stages_asked": list(stages or []), "force": list(force), "stages": []}
    history.append(entry)
    t_run = time.time()

    def record(name: str, res: dict) -> None:
        old = report.get(name)
        if not (res.get("skipped") and isinstance(old, dict) and not old.get("skipped")):
            report[name] = res                          # a skipped stage keeps the record of the run that did the work
        write_json(p.path("run_log.json"), report)
        entry["stages"].append({"name": name, **{k: v for k, v in res.items() if k in ("seconds", "skipped", "cleared")}})
        entry["seconds"] = round(time.time() - t_run, 1)
        write_json(p.path(RUN_HISTORY), history[-RUN_HISTORY_MAX:])

    pending = list(todo)
    while pending:
        name = pending.pop(0)
        mod = importlib.import_module(f".stages.{name}", __package__)
        t = time.time()
        log.info("== stage %s", name)
        res = mod.run(p, cfg) or {}
        res["seconds"] = round(time.time() - t, 1)
        # a stage that removed results further down (the script changed) has them made again in this run
        again = [s for s in res.get("cleared") or [] if s in STAGES]
        if again:
            at = STAGES.index(name)
            pending = [s for s in pending if STAGES.index(s) <= at] + \
                [s for s in STAGES[at + 1:] if s in set(pending) | set(again)]
        record(name, res)
        log.info("== stage %s: %s", name, {k: v for k, v in res.items() if not isinstance(v, (list, dict))})
    if p.path("items.json").exists() and p.path("dialogue.json").exists():
        t = time.time()
        log.info("== stage placement")
        draft = p.recompute()
        record("placement", {**draft.get("summary", {}), "seconds": round(time.time() - t, 1)})
        log.info("== stage placement: %s", report["placement"])
    entry["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    entry["seconds"] = round(time.time() - t_run, 1)
    write_json(p.path(RUN_HISTORY), history[-RUN_HISTORY_MAX:])
    return report
