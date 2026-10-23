@echo off
REM Start backend (FastAPI) on port 8000
REM %~dp0 是本脚本所在目录（scripts\），backend 在项目根目录下，故需 ..\ 回一级
cd /d "%~dp0..\backend"
REM 默认只听本机；需要局域网访问时先设置 APP_HOST=0.0.0.0（注意当前版本无鉴权）
if "%APP_HOST%"=="" set APP_HOST=127.0.0.1
echo [backend] starting uvicorn on http://%APP_HOST%:8000 ...
.venv\Scripts\python.exe -m uvicorn main:app --host %APP_HOST% --port 8000
pause
