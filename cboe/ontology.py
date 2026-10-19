# -*- coding: utf-8 -*-
"""AudioSet class hierarchy restricted to the 447 PretrainedSED classes (덱 5.3 ② 계층).

``parents(c)`` are the nearest ancestors of ``c`` that are themselves SED classes
(walking up through ontology nodes the model does not predict). A class may have
several parents (자동차 경적 < 자동차, 경보음); when climbing, the caller picks the
parent with the higher score (10-13 메모 1.2).
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import paths

# AudioSet-strong classes added after ontology.json (2017): nearest sensible SED parent
EXTRA_PARENTS = {
    "Alert": ["Alarm"], "Error signal": ["Alarm"], "Carbon monoxide detector, CO detector": ["Alarm"],
    "Keypress tone": ["Beep, bleep"], "Mechanical bell": ["Bell"], "Dong, bong": ["Bell"],
    "Crockery breaking and smashing": ["Breaking"], "Ringing tone, ringback tone": ["Telephone"],
    "Paper rustling": ["Rustle"], "Lock": ["Door"], "Stairs": ["Walk, footsteps"], "Stomp, stamp": ["Human locomotion"],
    "Tap dance": ["Human locomotion"], "Shower": ["Water"], "Bathroom sounds": ["Water"],
    "Kitchen and dining room sounds": ["Dishes, pots, and pans"], "Knife": ["Cutlery, silverware"],
    "Power saw, circular saw, table saw": ["Power tool"], "Electric rotor drone, quadcopter": ["Aircraft"],
    "Windscreen wiper, windshield wiper": ["Car"], "Cart": ["Non-motorized land vehicle"],
}


class Ontology:
    def __init__(self, classes: list[str]):
        import csv
        data = json.loads(paths.ONTOLOGY.read_text(encoding="utf-8"))
        self.classes = list(classes)
        self.cset = set(classes)
        by_id = {d["id"]: d for d in data}
        # SED display names differ from ontology names for ~40 classes ('…, toot'); join on the mid
        with open(paths.PSED_CODE / "hf_dataset_gen" / "metadata" / "class_labels_indices_strong.csv",
                  encoding="utf-8", newline="") as f:
            mid_of = {row[1]: row[0] for row in csv.reader(f) if len(row) >= 2}
        id_of = {d["name"]: d["id"] for d in data}
        id_of.update({name: mid for name, mid in mid_of.items() if mid in by_id})
        self.name_of = {d["id"]: d["name"] for d in data}
        self.name_of.update({mid: name for name, mid in id_of.items() if name in self.cset})
        up: dict[str, set[str]] = {}
        for d in data:
            for ch in d.get("child_ids", []):
                up.setdefault(ch, set()).add(d["id"])
        self.missing = [c for c in classes if c not in id_of]
        self._parents: dict[str, list[str]] = {}
        for c in classes:
            if c not in id_of:
                self._parents[c] = [p for p in EXTRA_PARENTS.get(c, []) if p in self.cset]
                continue
            found, frontier, seen = [], list(up.get(id_of[c], ())), set()
            while frontier:
                nid = frontier.pop()
                if nid in seen:
                    continue
                seen.add(nid)
                name = self.name_of[nid]
                if name in self.cset:
                    found.append(name)
                else:
                    frontier.extend(up.get(nid, ()))
            self._parents[c] = sorted(set(found))
        self._children: dict[str, list[str]] = {c: [] for c in classes}
        for c, ps in self._parents.items():
            for p in ps:
                self._children[p].append(c)

    def parents(self, c: str) -> list[str]:
        return self._parents.get(c, [])

    def children(self, c: str) -> list[str]:
        return self._children.get(c, [])

    def ancestors(self, c: str) -> set[str]:
        out, stack = set(), list(self.parents(c))
        while stack:
            p = stack.pop()
            if p not in out:
                out.add(p)
                stack.extend(self.parents(p))
        return out

    def related(self, a: str, b: str) -> bool:
        return a == b or a in self.ancestors(b) or b in self.ancestors(a)

    def depth(self, c: str) -> int:
        ps = self.parents(c)
        return 0 if not ps else 1 + max(self.depth(p) for p in ps)


@lru_cache(maxsize=1)
def get() -> Ontology:
    from .stages.sed import class_names
    return Ontology(class_names())
