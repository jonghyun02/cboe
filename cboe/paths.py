# -*- coding: utf-8 -*-
"""Where code and weights of the third-party models live on this PC.

Most models were already set up for the earlier demo (C:\\projects\\barrier_free\\demo);
CBoE reuses them in place instead of copying gigabytes. Override any path with an
environment variable of the same name (e.g. ``CBOE_BANDIT_CKPT``).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                 # ...\barrier_free\cboe
BF = ROOT.parent                                           # ...\barrier_free
DEMO = BF / "demo"
TP = ROOT / "third_party"                                  # tools/setup_third_party.py clones code here
W = ROOT / "weights"


def _p(name: str, *candidates: Path) -> Path:
    """``CBOE_<NAME>`` if set, else the first candidate that exists, else the last one
    (this PC keeps most models in the demo folder; a fresh clone uses third_party/ and weights/)."""
    env = os.environ.get(f"CBOE_{name}")
    if env:
        return Path(env)
    return next((c for c in candidates if c.exists()), candidates[-1])


# 음원 분리: Bandit v2 multilingual (DnR v3), weights CC BY-SA 4.0, code Apache-2.0
BANDIT_CODE = _p("BANDIT_CODE", DEMO / "third_party" / "bandit-v2", TP / "bandit-v2")
BANDIT_CKPT = _p("BANDIT_CKPT", DEMO / "weights" / "bandit_v2" / "checkpoint-multi.ckpt",
                 W / "bandit_v2" / "checkpoint-multi.ckpt")

# 효과음 검출: PretrainedSED (Schmid et al., ICASSP 2025), BEATs student, AudioSet-strong 447 classes
PSED_CODE = _p("PSED_CODE", BF / "vendor" / "PretrainedSED", TP / "PretrainedSED")
PSED_CKPT = _p("PSED_CKPT", DEMO / "weights" / "pretrainedsed" / "BEATs_strong_1.pt",
               W / "pretrainedsed" / "BEATs_strong_1.pt")
ONTOLOGY = _p("ONTOLOGY", W / "audioset_ontology.json")

# 장면 나누기: TransNetV2 official PyTorch port + official weights
TNV2_CODE = _p("TNV2_CODE", DEMO / "third_party" / "TransNetV2" / "inference-pytorch",
               TP / "TransNetV2" / "inference-pytorch")
TNV2_WEIGHTS = _p("TNV2_WEIGHTS", DEMO / "weights" / "transnetv2" / "transnetv2-pytorch-weights.pth",
                  W / "transnetv2" / "transnetv2-pytorch-weights.pth")

# 말하는 얼굴: YuNet (OpenCV zoo, MIT) + LR-ASD (IJCV 2025, MIT)
YUNET = _p("YUNET", W / "face_detection_yunet_2023mar.onnx")
LRASD_CODE = _p("LRASD_CODE", TP / "LR-ASD")
LRASD_WEIGHTS = _p("LRASD_WEIGHTS", TP / "LR-ASD" / "weight" / "finetuning_TalkSet.model")

# 음악 분위기·악기: AST fine-tuned on AudioSet (527 classes), BSD-3-Clause
AST_MODEL = os.environ.get("CBOE_AST_MODEL", "MIT/ast-finetuned-audioset-10-10-0.4593")

# 받아쓰기: faster-whisper large-v3 (MIT); on an Intel GPU the same weights in OpenVINO form
# (stages/asr.py OVWhisper). Either is a Hugging Face repo (downloaded on first use) or a folder.
WHISPER_MODEL = os.environ.get("CBOE_WHISPER_MODEL", "large-v3")
WHISPER_OV_MODEL = os.environ.get("CBOE_WHISPER_OV_MODEL", "OpenVINO/whisper-large-v3-fp16-ov")
WHISPER_OV_REVISION = os.environ.get("CBOE_WHISPER_OV_REVISION", "220761e60602a5ca694c409d5f424563b75d6820")

# 소리 설명 모델 (에이전트 도구): Qwen2.5-Omni-3B (Qwen research license, 비상업)
QWEN_OMNI = _p("QWEN_OMNI", BF / "weights" / "qwen25omni3b", W / "qwen25omni3b")

# 넷플릭스 지침 원문 (참고 문서, 코드는 읽지 않음)
NETFLIX = _p("NETFLIX", DEMO / "third_party" / "netflix", TP / "netflix")


def check() -> dict:
    """Which model files are present (for `python -m cboe doctor`)."""
    skip = ("ROOT", "BF", "DEMO", "TP", "W", "NETFLIX")
    items = {k: v for k, v in globals().items() if isinstance(v, Path) and k.isupper() and k not in skip}
    return {k: v.exists() for k, v in items.items()}
