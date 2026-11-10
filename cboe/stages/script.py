# -*- coding: utf-8 -*-
"""대본 읽기: the script file of the project -> ``script.json`` (``cboe.script``, version 2).

The reading remembers the file it was made from (path, SHA-1). When that file has changed —
edited, or another file given with ``cboe init`` — the script is read again and the results
made from the old reading (대본 정렬 and what follows it) are removed so that they are made
again. The audio, the sound candidates and the transcription (받아쓰기) do not come from the
script and stay.

A work folder from before this stage existed has a ``script.json`` without a version. It is
used as it is unless the script file is newer than that reading; then the file is read in
the new format, and only when its dialogue lines differ from the old reading are the results
below removed. ``--force script`` reads it again in any case.
"""
from __future__ import annotations

from pathlib import Path

from .. import script as script_mod
from ..util import log, read_json, write_json

OUT = "script.json"


def _info(doc: dict, **extra) -> dict:
    return {"lines": len(doc.get("lines") or []), "characters": len(doc.get("characters") or []),
            "scenes": len(doc.get("scenes") or []), "format": doc.get("format") or "lines", **extra}


def _dialogue(doc: dict) -> list[tuple]:
    return [(ln.get("id"), ln.get("speaker"), ln.get("text"), ln.get("start"), ln.get("end"))
            for ln in doc.get("lines") or []]


def changed(old: dict, path: Path, read_at: float) -> str | None:
    """Why the reading ``old`` (written at ``read_at``) no longer stands for the file, or None."""
    if old.get("version") != script_mod.VERSION:          # read by an older CBoE: no checksum to compare
        return "대본 파일이 읽어 둔 것보다 새것입니다" if path.stat().st_mtime > read_at else None
    src = old.get("source")
    if not isinstance(src, dict) or src.get("sha1") != script_mod.file_signature(path)["sha1"]:
        return "대본 파일이 바뀌었습니다"
    return None


def clear_below(p) -> list[str]:
    """Remove the results made from the script (alignment and the stages that use it); the
    names of the stages that lost a result."""
    from .. import pipeline                               # pipeline imports the stages by name
    removed = []
    for name in pipeline.dependents(["align"]):
        hit = False
        for rel in pipeline.OUTPUTS.get(name, []):
            f = p.path(rel)
            if f.exists():
                f.unlink()
                hit = True
        if hit:
            removed.append(name)
    return removed


def run(p, cfg: dict) -> dict:
    src = p.meta.get("script")
    out = p.path(OUT)
    old = read_json(out) if out.exists() else None
    if not src:                                           # no script: the transcription becomes the dialogue
        return {"skipped": True, **(_info(old) if old else {"lines": 0})}
    path = Path(src)
    if not path.is_file():
        if old is None:
            raise FileNotFoundError(f"대본 파일이 없습니다: {path}")
        log.warning("script: 대본 파일이 없습니다 (%s). 앞서 읽어 둔 대본을 그대로 씁니다", path)
        return {"skipped": True, **_info(old), "missing": True}
    why = "처음 읽습니다"
    if old is not None:
        why = changed(old, path, out.stat().st_mtime)
        if why is None:
            return {"skipped": True, **_info(old)}
    doc = script_mod.read(path)
    if old is not None and old.get("version") != script_mod.VERSION and _dialogue(old) == _dialogue(doc):
        why, cleared = "예전 형식의 대본을 새 형식으로 옮겼습니다 (대사 줄은 그대로)", []
    else:
        cleared = clear_below(p)                          # also when a script is given to a finished run
    write_json(out, doc)
    summary = script_mod.summary(doc)
    log.info("script (%s): %s, 장면 %d, 대사 %d줄 (화자 있는 줄 %d), 인물 %d, 지문 %d", why, doc["format"],
             summary["scenes"], summary["lines"], summary["named"], summary["characters"], summary["actions"])
    for msg in ([summary["error"]] if summary["error"] else []) + summary["warnings"]:
        log.warning("script: %s", msg)
    if cleared:
        log.info("script: %s 대본으로 만든 결과를 다시 만듭니다. cleared outputs of: %s", why, ", ".join(cleared))
    return _info(doc, timed=doc["timed"], changed=old is not None, cleared=cleared)
