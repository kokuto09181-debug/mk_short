@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set LIMIT=%1
if "%LIMIT%"=="" set LIMIT=3

echo [upload] 長編動画アップロード (limit=%LIMIT%)
python scripts/upload_longform.py --limit %LIMIT%
