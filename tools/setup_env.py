# -*- coding: utf-8 -*-
"""Set CBoE up on this PC in one go: .venv, the PyTorch build for its GPU, the packages,
the third-party code and the model weights (checksums checked).

    setup.cmd                        (= uv run --no-project --python 3.11 tools\\setup_env.py)
    setup.cmd --torch xpu            force a build: cu128 (NVIDIA) | xpu (Intel Arc / Core Ultra) | cpu
    setup.cmd --no-weights           code and packages only
    setup.cmd --shortcut             also put the CBoE shortcut on the desktop

The GPU is detected as: nvidia-smi works -> cu128; an Intel Arc GPU (Arc A/B cards, Core Ultra
'Arc Graphics', Arc 130V/140V) -> xpu; otherwise cpu (works, much slower). The model stages
then pick the same device by themselves (cboe/util.py device()). Needs uv, git and ffmpeg;
setup.cmd installs uv, and this script git and ffmpeg, with winget when they are missing.
Only the standard library is used here, so any Python runs it.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cboe import paths  # noqa: E402  (standard library only)
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TORCH = {"torch": "2.11.0", "torchaudio": "2.11.0", "torchvision": "0.26.0"}
PYPI = "https://pypi.org/simple"
# Intel GPU only: Whisper through OpenVINO (CTranslate2 has no Intel GPU), YuNet on the GPU, and int8
# loading of the sound description model on an 8 GB GPU
EXTRA = {"xpu": ["openvino-genai==2026.4.1.0", "openvino==2026.4.1", "openvino-tokenizers==2026.4.1.0",
                 "torchao==0.18.0"]}

# (path under the repo, url, checksum)
WEIGHTS = [
    ("weights/bandit_v2/checkpoint-multi.ckpt",              # Bandit v2 multilingual, CC BY-SA 4.0
     "https://zenodo.org/api/records/12701995/files/checkpoint-multi.ckpt/content",
     "md5:fea2868787551b0cff36cfcf7c3622a3"),
    ("weights/pretrainedsed/BEATs_strong_1.pt",               # PretrainedSED, MIT
     "https://github.com/fschmid56/PretrainedSED/releases/download/v0.0.1/BEATs_strong_1.pt",
     "sha256:db13a79ae90a0cfd0f9911a6a1d8cdb89324322bee642dcfe32de022123b8b54"),
    # TransNetV2 official weights (MIT) in the PyTorch port's format. This copy has the same 90 tensors as
    # inference-pytorch/convert_weights.py makes from the official TF weights (checked 2026-11-03), which
    # spares the TensorFlow install the conversion needs.
    ("weights/transnetv2/transnetv2-pytorch-weights.pth",
     "https://huggingface.co/Sn4kehead/TransNetV2/resolve/db6ceabeb692ec71ecc6beb6d00db67ad1412d7f/"
     "transnetv2-pytorch-weights.pth",
     "sha256:834b10f25ae9e1b4e4f2652fe2843bd2b1388057a435d68b7c52635578fcc04d"),
]
# Hugging Face snapshots: (repo, revision, local dir or None for the HF cache, file patterns or None)
QWEN = ("Qwen/Qwen2.5-Omni-3B", "f75b40e3da2003cdd6e1829b1f420ca70797c34e", "weights/qwen25omni3b", None)
AST = ("MIT/ast-finetuned-audioset-10-10-0.4593", None, None, None)
WHISPER = {  # the ASR backend each build uses (cboe/stages/asr.py load_model)
    "cu128": ("Systran/faster-whisper-large-v3", None, None, None),
    "cpu": ("Systran/faster-whisper-large-v3", None, None, None),
    "xpu": (paths.WHISPER_OV_MODEL, paths.WHISPER_OV_REVISION, None, None),
}


def sh(cmd: list, **kw) -> None:
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT, **kw)


def detect() -> str:
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True)
        if r.returncode == 0 and "GPU" in r.stdout:
            return "cu128"
    if os.name == "nt":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                           capture_output=True, text=True)
        if re.search(r"Intel.*\bArc\b", r.stdout):
            return "xpu"
    return "cpu"


def install(build: str) -> None:
    if not PY.exists():
        sh(["uv", "venv", "--python", "3.11", VENV])
    # the tested lockfile with the torch lines switched to this PC's build
    lines = []
    for ln in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        name = ln.split("==")[0].strip().lower()
        if ln.startswith("--") or name in TORCH:
            continue
        lines.append(ln)
    lines += [f"{k}=={v}+{build}" for k, v in TORCH.items()] + EXTRA.get(build, [])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        sh(["uv", "pip", "install", "--python", PY, "-r", f.name,
            "--index-url", f"https://download.pytorch.org/whl/{build}", "--extra-index-url", PYPI,
            "--index-strategy", "unsafe-best-match"])
    finally:
        os.unlink(f.name)


def _hash(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(rel: str, url: str, checksum: str) -> None:
    dest = ROOT / rel
    algo, want = checksum.split(":")
    if dest.exists():
        if _hash(dest, algo) == want:
            print(f"= {rel}")
            return
        print(f"! {rel}: checksum differs, downloading again")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    print(f"+ {rel} <- {url}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        for b in iter(lambda: r.read(1 << 20), b""):
            f.write(b)
            done += len(b)
            if total:
                print(f"\r  {done / 2**20:.0f} / {total / 2**20:.0f} MB", end="", flush=True)
    print()
    got = _hash(part, algo)
    if got != want:
        part.unlink()
        raise SystemExit(f"{rel}: {algo} {got} != {want}")
    os.replace(part, dest)


def snapshot(repo: str, rev: str | None, local: str | None, patterns: list | None) -> None:
    code = ("import sys; from huggingface_hub import snapshot_download as s; "
            "a = sys.argv[1:]; print(s(a[0], revision=a[1] or None, local_dir=a[2] or None, "
            "allow_patterns=a[3].split(',') if a[3] else None))")
    sh([PY, "-c", code, repo, rev or "", str(ROOT / local) if local else "", ",".join(patterns or [])])


# what this script installs with winget when it is missing, and where winget puts it (winget changes
# PATH only for windows opened afterwards, so this run adds those folders itself)
WINGET = {
    "git": ("Git.Git", [r"%ProgramFiles%\Git\cmd"]),
    "ffmpeg": ("Gyan.FFmpeg", [r"%LOCALAPPDATA%\Microsoft\WinGet\Links",
                               r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\*\bin"]),
}


def ensure_tool(tool: str) -> bool:
    if shutil.which(tool):
        return True
    if os.name != "nt" or not shutil.which("winget"):
        return False
    pkg, dirs = WINGET[tool]
    print(f"+ {tool} not found: winget install {pkg}", flush=True)
    subprocess.run(["winget", "install", "--id", pkg, "-e", "--accept-source-agreements",
                    "--accept-package-agreements"])
    import glob
    for d in dirs:
        for hit in glob.glob(os.path.expandvars(d)):
            os.environ["PATH"] = hit + os.pathsep + os.environ.get("PATH", "")
    return bool(shutil.which(tool))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--torch", choices=["cu128", "xpu", "cpu"], help="PyTorch build (default: detect the GPU)")
    ap.add_argument("--no-weights", action="store_true")
    ap.add_argument("--shortcut", action="store_true")
    args = ap.parse_args()
    if not shutil.which("uv"):
        raise SystemExit("uv not found on PATH (winget install astral-sh.uv); setup.cmd installs it")
    if not ensure_tool("git"):
        raise SystemExit("git not found and could not be installed: winget install Git.Git")
    new_ffmpeg = not shutil.which("ffmpeg")
    if not (ensure_tool("ffmpeg") and shutil.which("ffprobe")):
        print("! ffmpeg/ffprobe not on PATH: winget install Gyan.FFmpeg (needed to analyse videos)")
    build = args.torch or detect()
    print(f"PyTorch build: {build}", flush=True)
    install(build)
    subprocess.run([str(PY), "tools/setup_third_party.py"], cwd=ROOT,
                   env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})       # exit 1 = weights still missing
    if not args.no_weights:
        for rel, url, checksum in WEIGHTS:
            fetch(rel, url, checksum)
        for snap in (QWEN, AST, WHISPER[build]):
            snapshot(*snap)
    if args.shortcut and os.name == "nt":
        sh(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/make_shortcut.ps1"])
    sh([PY, "-m", "cboe", "doctor"])
    if new_ffmpeg:
        print("ffmpeg was just installed: start CBoE from a new window (cboe-gui.cmd) so that it is on PATH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
