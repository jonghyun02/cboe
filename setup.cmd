@echo off
rem CBoE setup on any Windows PC: .venv, the PyTorch build for this PC's GPU (NVIDIA cu128 / Intel Arc xpu / cpu),
rem packages, third-party code, model weights. Installs uv, git and ffmpeg with winget when they are missing.
rem Options: --torch cu128^|xpu^|cpu  --no-weights  --shortcut
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
where uv >nul 2>nul && goto run
where winget >nul 2>nul || goto nouv
echo uv not found: winget install astral-sh.uv
winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
rem winget puts uv on PATH for new windows only
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul && goto run
echo uv was installed but this window cannot see it yet. Open a new terminal and run setup.cmd again.
exit /b 1
:nouv
echo uv not found and winget is not available. Install uv first: https://docs.astral.sh/uv/getting-started/installation/
exit /b 1
:run
uv run --no-project --python 3.11 tools\setup_env.py %*
