# -*- coding: utf-8 -*-
"""Work-folder state shared by the CLI and the review server.

The pipeline writes ``items.json`` (candidates with evidence and a recommendation)
and ``dialogue.json`` once. The writer's choices live in ``review.json``. Every
time a choice changes, ``Project.recompute()`` merges both, runs placement
(끼워 넣기) and saves ``draft.json`` — "고칠 때마다 배치를 다시 계산".
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .util import read_json, write_json

FINALS = ("include", "exclude", "check")


class Project:
    def __init__(self, work):
        self.work = Path(work)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ files
    def path(self, name: str) -> Path:
        return self.work / name

    @property
    def meta(self) -> dict:
        return read_json(self.path("project.json"), {}) or {}

    def dialogue(self) -> list[dict]:
        return (read_json(self.path("dialogue.json"), {}) or {}).get("lines", [])

    def items(self) -> list[dict]:
        return (read_json(self.path("items.json"), {}) or {}).get("items", [])

    def cuts(self) -> list[float]:
        return (read_json(self.path("cuts.json"), {}) or {}).get("cuts", [])

    def characters(self) -> list[str]:
        script = read_json(self.path("script.json"), {}) or {}
        names = list(script.get("characters", []))
        for line in self.dialogue():
            if line.get("speaker") and line["speaker"] not in names:
                names.append(line["speaker"])
        for d in self.review().get("items", {}).values():
            if d.get("name") and d["name"] not in names:
                names.append(d["name"])
        return names

    def review(self) -> dict:
        r = read_json(self.path("review.json"), {}) or {}
        r.setdefault("items", {})
        r.setdefault("placement", {})
        return r

    def save_review(self, review: dict) -> None:
        write_json(self.path("review.json"), review)

    # ------------------------------------------------------------- decisions
    @staticmethod
    def fingerprint(it: dict) -> str:
        """What a decision was made about: kind and onset of a sound, line id and text of a
        speaker line. Item ids are sequence numbers and change when a stage is re-run, so a
        stored decision only applies to the item with the same fingerprint."""
        if it.get("kind") == "speaker":
            sp = it.get("speaker") or {}
            return f"speaker@{sp.get('line_id')}:{it.get('phrase', '')}"
        return f"{it.get('kind')}@{float(it.get('onset', 0.0)):.2f}"

    def _decisions(self, review: dict, items: list[dict], section: str) -> dict[str, dict]:
        """Decisions of ``review[section]`` keyed by the current item id (fingerprint-checked)."""
        stored = review.get(section, {})
        by_fp = {d["fp"]: d for d in stored.values() if isinstance(d, dict) and d.get("fp")}
        out = {}
        for it in items:
            fp = self.fingerprint(it)
            d = stored.get(it["id"])
            if d and d.get("fp") and d["fp"] != fp:
                d = None                      # the id now names another candidate
            d = d or by_fp.get(fp)
            if d:
                out[it["id"]] = d
        return out

    def effective_items(self, review: dict | None = None) -> list[dict]:
        """Items with the writer's choices merged in.

        Adds ``final`` (include / exclude / check), ``decided`` (bool), and for the
        current text ``phrase``/``short``; speaker items get ``name``.
        """
        review = review or self.review()
        lines = {ln["id"]: ln for ln in self.dialogue()}
        items = self.items()
        dec = self._decisions(review, items, "items")
        plc = self._decisions(review, items, "placement")
        out = []
        for it in items:
            it = dict(it)
            d = dec.get(it["id"], {})
            it["decided"] = d.get("decision") in ("include", "exclude")
            it["final"] = d.get("decision") if it["decided"] else it.get("rec", "check")
            if d.get("phrase"):
                it["phrase"] = d["phrase"]
                it["short"] = d.get("short") or d["phrase"]
            if it["kind"] == "speaker":
                sp = dict(it.get("speaker") or {})
                line = lines.get(sp.get("line_id"), {})
                sp["name"] = d.get("name") or sp.get("name") or line.get("speaker")
                it["speaker"] = sp
            pl = plc.get(it["id"])
            if pl:
                it["placement"] = pl
            out.append(it)
        return out

    def set_decision(self, item_id: str, **fields) -> dict:
        """Store a writer decision. ``fields``: decision, phrase, short, name, scope.

        ``scope`` for speaker names: ``line`` (default), ``speaker`` (every line that is
        shown under the same name as this one right now — lines the writer renamed to
        someone else are left alone) or ``voice`` (same voice cluster, when lines carry
        ``voice``).
        """
        with self._lock:
            review = self.review()
            eff = {it["id"]: it for it in self.effective_items(review)}
            if item_id not in eff:
                raise KeyError(item_id)
            targets = [item_id]
            scope = fields.pop("scope", "line") or "line"
            it = eff[item_id]
            if it["kind"] == "speaker" and scope in ("speaker", "voice") and "name" in fields:
                if scope == "speaker":
                    ref = (it.get("speaker") or {}).get("name")
                    if ref:
                        targets = [o["id"] for o in eff.values() if o["kind"] == "speaker"
                                   and (o.get("speaker") or {}).get("name") == ref]
                else:
                    lines = {ln["id"]: ln for ln in self.dialogue()}
                    ref = lines.get((it.get("speaker") or {}).get("line_id"), {}).get("voice")
                    if ref is not None:
                        targets = [o["id"] for o in eff.values() if o["kind"] == "speaker"
                                   and lines.get((o.get("speaker") or {}).get("line_id"), {}).get("voice") == ref]
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._normalise(review, "items")              # decisions under today's ids first
            for tid in targets:
                fp = self.fingerprint(eff[tid])
                cur = dict(review["items"].get(tid, {}))
                for k, v in fields.items():
                    if tid != item_id and k != "name":
                        continue          # scope only spreads the name
                    if v is None:
                        cur.pop(k, None)
                    else:
                        cur[k] = v
                cur["at"] = stamp
                cur["fp"] = fp
                review["items"][tid] = cur
            self.save_review(review)
            return {"changed": targets}

    def _normalise(self, review: dict, section: str) -> None:
        """Re-key ``review[section]`` by today's item ids, matched by fingerprint, so that a write
        under an id never lands on the decision of another candidate that used to have it.
        Decisions whose candidate is gone are kept under ``fp:<fingerprint>`` and come back
        when the candidate does."""
        items = self.items()
        stored = review.get(section, {})
        current = self._decisions(review, items, section)
        new, used = {}, set()
        for it in items:
            d = current.get(it["id"])
            if d:
                d = dict(d)
                d["fp"] = self.fingerprint(it)
                new[it["id"]] = d
                used.add(d["fp"])
        for d in stored.values():
            if isinstance(d, dict) and d.get("fp") and d["fp"] not in used:
                new[f"fp:{d['fp']}"] = d
        review[section] = new

    def set_placement(self, item_id: str, action: str | None, t: float | None = None) -> None:
        """Writer's fix for a '배치 확인 필요' item: move (to time t) or clear."""
        with self._lock:
            review = self.review()
            it = next((x for x in self.items() if x["id"] == item_id), None)
            self._normalise(review, "placement")
            review["placement"].pop(item_id, None)
            if action not in (None, "clear"):
                review["placement"][item_id] = {"action": action, "t": t,
                                                "fp": self.fingerprint(it) if it else None}
            self.save_review(review)

    # -------------------------------------------------------------- placement
    def placement_inputs(self, review: dict | None = None) -> tuple[list[dict], list[dict]]:
        eff = self.effective_items(review)
        by_line = {}
        for it in eff:
            if it["kind"] == "speaker" and it.get("speaker", {}).get("line_id"):
                by_line[it["speaker"]["line_id"]] = it
        lines = []
        for ln in self.dialogue():
            ln = dict(ln)
            sp = by_line.get(ln["id"])
            ln["show_name"] = bool(sp and sp["final"] == "include" and sp["speaker"].get("name"))
            ln["name"] = (sp["speaker"].get("name") if sp else None) or ln.get("speaker")
            ln["speaker_item"] = sp["id"] if sp else None
            lines.append(ln)
        sounds = []
        for it in eff:
            if it["kind"] in ("sfx", "music") and it["final"] == "include":
                pl = it.get("placement") or {}
                if pl.get("action") == "exclude":
                    continue
                sounds.append({"id": it["id"], "kind": it["kind"], "onset": it["onset"],
                               "t1": it.get("t1", it["onset"]), "phrase": it.get("phrase"),
                               "short": it.get("short") or it.get("phrase"),
                               "source_line": it.get("source_line"),
                               "move_to": pl.get("t") if pl.get("action") == "move" else None})
        return lines, sounds

    def recompute(self) -> dict:
        from . import placement, style
        with self._lock:
            lines, sounds = self.placement_inputs()
            meta = self.meta
            # the limits of this work folder's profile (project.json "profile": adult | children)
            draft = placement.place(lines, sounds, self.cuts(), float(meta.get("fps") or 25.0),
                                    window=(meta.get("t0", 0.0), meta.get("t1")),
                                    settings=style.for_project(meta).settings())
            write_json(self.path("draft.json"), draft)
            return draft

    def draft(self) -> dict:
        d = read_json(self.path("draft.json"))
        return d if d is not None else self.recompute()

    # ----------------------------------------------------------------- export
    def export_check(self) -> dict:
        """What is left before exporting. ``guide`` is the result of the independent guide check
        (``cboe.check.run``: clause by clause, must / should) on the current draft, with the
        draft's frame rate, the shot changes and the profile of this work folder; ``flags`` lists
        the cells placement marked (timing_estimated, overlap, flash)."""
        from . import check, style
        eff = self.effective_items()
        draft = self.draft()
        profile = style.for_project(self.meta).profile
        if draft.get("notation") != style.NOTATION or draft.get("profile") != profile:
            draft = self.recompute()        # written with an older notation ('(이름)') or another profile
        guide = check.run(draft, cuts=self.cuts(), profile=profile)
        flags: dict = {}
        for c in draft.get("cells", []):
            for name in c.get("flags") or []:
                flags.setdefault(name, []).append(c["id"])
        sounds = [it for it in eff if it["kind"] in ("sfx", "music")]
        # sounds left at 확인 필요 are not captioned; speaker lines left at 확인 필요 are still
        # exported, only without a name, so they are reported apart
        pending = [it["id"] for it in sounds if it["final"] == "check"]
        check_speaker = [it["id"] for it in eff if it["kind"] == "speaker" and it["final"] == "check"]
        undecided_ex = [it["id"] for it in sounds if it["final"] == "exclude" and not it["decided"]]
        viol = [c for c in draft.get("cells", []) if (c.get("check") or {}).get("violations")]
        # an included sound whose caption is still the placeholder '[소리]' must be named first
        unnamed = [it["id"] for it in eff if it["kind"] == "sfx" and it["final"] == "include"
                   and (it.get("phrase") or "").strip() in ("[소리]", "")]
        return {"check": pending, "check_speaker": check_speaker, "no_room": draft.get("no_room", []),
                "exclude_recommended": undecided_ex, "unnamed": unnamed,
                "violations": [{"cell": c["id"], "violations": c["check"]["violations"]} for c in viol],
                "guide": guide, "flags": flags, "profile": profile}

    def export(self, fmt: str = "srt", report: bool = False):
        """Write the draft as ``fmt`` (srt | vtt | ttml) into the work folder's ``export``.
        Returns the path, or with ``report=True`` the whole result of ``export.write``
        (``{"path", "format", "cells", "check"}``: the guide check of what was written)."""
        import re

        from . import export as ex
        draft = self.recompute()
        fmt = str(fmt or "srt").lower().lstrip(".")
        title = re.sub(r'[\\/:*?"<>|]+', "_", self.meta.get("title") or self.work.name).strip() or "export"
        out = self.path("export") / f"{title}.{fmt}"
        res = ex.write(draft["cells"], out, fmt, fps=draft.get("fps"), cuts=self.cuts(),
                       profile=draft.get("profile"), title=self.meta.get("title") or self.work.name)
        return res if report else res["path"]
