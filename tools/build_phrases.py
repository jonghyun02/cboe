# -*- coding: utf-8 -*-
"""Build cboe/data/sfx_classes.tsv: Korean phrases and a policy category per SED class.

Sources (all made earlier for this research, read as data, not imported):
- labels_ko.tsv (sdh-toolkit): Korean label, family, min_on_s, merge_gap_s per class
- demo/sdhdemo/naming.py SDH dict: hand-written SDH wording '쾅, 문 닫히는 소리'
- demo/sdhdemo/export.py NETFLIX dict: Netflix II.9 style '쾅 - 문' (onomatopoeia - source)

Output columns: class, desc (설명 문구, e.g. '자동차 경적 소리'), ono (의성어, e.g. '빵빵'),
family, category (event | ambient | minor | vocal | speech | music | background), min_on_s, merge_gap_s.
The TSV is then hand-checked; rerunning overwrites it.
"""
from __future__ import annotations

import ast
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BF = ROOT.parent
sys.path.insert(0, str(ROOT))

EVENT = {"door", "doorbell", "phone", "alarm", "siren", "gun", "explosion", "break", "glass", "impact", "hit", "horn",
         "thunder", "bell", "camera", "crowd_reaction", "whoosh", "footsteps", "clock", "whistle", "creak", "keys"}
AMBIENT = {"rain", "wind", "sea", "stream", "bird", "insect", "crowd", "car", "train", "aircraft", "boat", "fire", "frog",
           "rumble", "livestock", "poultry", "dog", "cat", "big_cat", "rodent", "snake", "whale", "water", "bicycle"}
MINOR = {"paper", "typing", "kitchen", "tools", "appliance", "scrape", "click", "eating", "body", "clothes", "media",
         "hiss", "buzz", "hum", "misc_object"}
VOCAL = {"laughter", "crying", "breath", "cough", "sneeze", "snore", "scream", "moan", "hum_voice", "whistle_voice",
         "baby", "whisper"}
SPEECH = {"Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
          "Conversation", "Narration, monologue", "Speech synthesizer", "Human voice", "Whispering", "Babbling"}
BACKGROUND = {"Background noise", "Noise", "Environmental noise", "Static", "Mains hum", "Distortion", "Cacophony",
              "White noise, pink noise", "Tape hiss", "Wind noise (microphone)", "Inside, small room",
              "Inside, large room or hall", "Inside, public space", "Outside, urban or manmade",
              "Outside, rural or natural", "Reverberation", "Echo", "Chorus effect", "Effects unit", "Microphone",
              "Sound equipment", "Sound reproduction", "Pulse", "Sine wave", "Audio logo", "Electronic tuner",
              "Unknown sound", "Other sourceless", "Channel, environment and background", "Sound effect",
              "Human sounds", "Respiratory sounds", "Digestive"}
PARENT_FAMILY = {"Animal": "dog", "Domestic animals, pets": "dog", "Wild animals": "bird",
                 "Livestock, farm animals, working animals": "livestock", "Generic impact sounds": "impact",
                 "Surface contact": "impact", "Deformable shell": "paper", "Hands": "hit",
                 "Human locomotion": "footsteps", "Human group actions": "crowd", "Liquid": "water", "Water": "water",
                 "Mechanisms": "misc_object", "Tools": "tools", "Vehicle": "car", "Non-motorized land vehicle": "bicycle",
                 "Breathing": "breath"}


def dict_literal(path: Path, name: str) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return ast.literal_eval(node.value)
    raise KeyError(name)


def main():
    from cboe.stages.sed import class_names
    classes = class_names()
    rows = {}
    with open(BF / "labels_ko.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows[r["name"]] = r
    sdh = dict_literal(BF / "demo" / "sdhdemo" / "naming.py", "SDH")
    nf = dict_literal(BF / "demo" / "sdhdemo" / "export.py", "NETFLIX")
    out = []
    for c in classes:
        r = rows.get(c, {})
        fam = r.get("family") or PARENT_FAMILY.get(c, "")
        ono, desc = "", ""
        if c in sdh:
            s = sdh[c]
            if ", " in s:
                ono, desc = s.split(", ", 1)
            else:
                desc = s
        if c in nf:
            n = nf[c]
            o2, _, src = n.partition(" - ")
            ono = ono or (o2 if src or len(o2) <= 6 else "")
            if not desc and src:
                desc = f"{src} 소리"
        if not desc and r.get("label_ko"):
            desc = r["label_ko"]
            first = desc.split(" ")[0]
            if not ono and len(desc.split(" ")) > 2 and first and first == nf.get(c, "").split(" - ")[0]:
                ono, desc = first, desc[len(first):].strip()
        if c in SPEECH:
            cat = "speech"
        elif c in BACKGROUND:
            cat = "background"
        elif fam == "music" or c in ("Music",):
            cat = "music"
        elif fam in VOCAL:
            cat = "vocal"
        elif fam in EVENT:
            cat = "event"
        elif fam in AMBIENT:
            cat = "ambient"
        elif fam in MINOR:
            cat = "minor"
        else:
            cat = "minor" if desc else "background"
        out.append({"class": c, "desc": desc, "ono": ono, "family": fam, "category": cat,
                    "min_on_s": r.get("min_on_s") or "0.2", "merge_gap_s": r.get("merge_gap_s") or "0.5"})
    dst = ROOT / "cboe" / "data" / "sfx_classes.tsv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter="\t")
        w.writeheader()
        w.writerows(out)
    cats = {}
    for o in out:
        cats[o["category"]] = cats.get(o["category"], 0) + 1
    print(dst, len(out), cats, "no desc:", [o["class"] for o in out if not o["desc"] and o["category"] not in
                                            ("speech", "background", "music")])


if __name__ == "__main__":
    main()
