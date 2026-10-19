@echo off
rem CBoE launcher: cboe init|run|serve|export|doctor ...
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" -m cboe %*
