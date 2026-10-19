# -*- coding: utf-8 -*-
"""Fetch the third-party code CBoE runs on into third_party/ and say which weights are missing.

    .venv\\Scripts\\python.exe tools\\setup_third_party.py

Code is cloned at the commits this tool was built and tested with. Weights are not
downloaded here (licences and sizes differ); `python -m cboe doctor` and the GUI's
환경 점검 list what is still missing, and each repository's README says where its weights
come from. Paths can also point elsewhere with CBOE_<NAME> environment variables (cboe/paths.py).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TP = ROOT / "third_party"

REPOS = {
    # name: (url, commit, licence)
    "LR-ASD": ("https://github.com/Junhua-Liao/LR-ASD.git", "1b6dcd2d8fc2895683de6508ec6294ec47d388ca", "MIT, weights included"),
    "bandit-v2": ("https://github.com/kwatcharasupat/bandit-v2.git", "d5563d9031e95fdaa3e5a73d5020b9a0df61adb6", "Apache-2.0"),
    "PretrainedSED": ("https://github.com/fschmid56/PretrainedSED.git", "1aa47e482f7e89904cba2338999345025d8b4e36", "MIT"),
    "TransNetV2": ("https://github.com/soCzech/TransNetV2.git", "85cef72af9a916bdfd7cc94a670c9cdfbf12d1ed", "MIT"),
}

WEIGHTS = [
    ("BANDIT_CKPT", "weights/bandit_v2/checkpoint-multi.ckpt", "Bandit v2 multilingual checkpoint (bandit-v2 README)"),
    ("PSED_CKPT", "weights/pretrainedsed/BEATs_strong_1.pt", "PretrainedSED BEATs strong checkpoint (PretrainedSED README)"),
    ("TNV2_WEIGHTS", "weights/transnetv2/transnetv2-pytorch-weights.pth", "TransNetV2 PyTorch weights (inference-pytorch/convert_weights.py)"),
    ("QWEN_OMNI", "weights/qwen25omni3b", "Hugging Face Qwen/Qwen2.5-Omni-3B (research licence)"),
]


def clone(name: str, url: str, commit: str) -> None:
    dest = TP / name
    if dest.exists():
        print(f"= {name}: already there")
        return
    TP.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", commit], check=True)
    print(f"+ {name}: cloned at {commit[:10]}")


def main() -> int:
    for name, (url, commit, lic) in REPOS.items():
        clone(name, url, commit)
    sys.path.insert(0, str(ROOT))
    from cboe import paths
    missing = [k for k, ok in paths.check().items() if not ok]
    if not missing:
        print("all model files found")
        return 0
    print("\nstill missing:")
    hints = {k: (rel, what) for k, rel, what in WEIGHTS}
    for k in missing:
        rel, what = hints.get(k, (str(getattr(paths, k)), ""))
        print(f"  {k:13s} -> {ROOT / rel}  {what}")
    print("Whisper large-v3 and AST download themselves from Hugging Face on first use.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
