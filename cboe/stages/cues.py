# -*- coding: utf-8 -*-
"""대본 단서: the sounds, music, silences and speakers' own sounds the script writes down
(``cboe.cues``), each with the time window it can fall in -> ``cues.json``.

Runs after the alignment (it needs the aligned dialogue lines and, from the scene-first
alignment, ``scenes.json``) and before the recommendation, which attaches the cues to the
detected candidates or makes 확인 필요 candidates of the ones the detectors did not find
(``recommend.build``). A cue without a window (its scene is not in the video, it lies past
lines out of the processed stretch, or no time is known around it) stays in cues.json only.
A window may reach the lines of the scene next to it or the edge of the processed stretch
(project ``t0``/``t1``, ``span``) when its own scene has no timed line on that side.

The stage is cheap and pure, so it is made again on every run instead of being cleared with
the alignment; the recommendation reads the same cues through ``load`` and never a stale file,
and writes cues.json again itself (a run of some stages only, such as the script read again
by itself, reruns align … recommend but not this stage).
Without a script (or with one that has no action lines and no speakers' own sounds) the file
holds no cues. Output: {"version": 1, "cues": [...], "counts": {...}}.
"""
from __future__ import annotations

from .. import cues as cue_mod
from ..util import log, read_json, write_json

OUT = "cues.json"
VERSION = 1


def span(p) -> tuple[float, float | None]:
    """The processed stretch (project t0, t1; t1 missing: the video's end, if known)."""
    meta = getattr(p, "meta", None) or {}
    t1 = meta.get("t1") if meta.get("t1") is not None else meta.get("duration")
    return float(meta.get("t0") or 0.0), (float(t1) if t1 is not None else None)


def load(p) -> list[dict]:
    """The cues of a work folder, from script.json, dialogue.json and scenes.json (made now)."""
    doc = read_json(p.path("script.json"), {}) or {}
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    scenes = (read_json(p.path("scenes.json"), {}) or {}).get("scenes")
    return cue_mod.build(doc, lines, scenes, span(p))


def run(p, cfg: dict) -> dict:
    found = load(p)
    cnt = cue_mod.counts(found)
    write_json(p.path(OUT), {"version": VERSION, "cues": found, "counts": cnt})
    log.info("cues: %s", cnt)
    return cnt
