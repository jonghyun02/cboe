@echo off
rem CBoE desktop window (project list, analysis, review). Add --browser to use the default browser.
cd /d "%~dp0"
if /i "%~1"=="--browser" (
  "%~dp0.venv\Scripts\python.exe" -m cboe gui %*
) else (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m cboe gui %*
)
