@echo off
REM Start backend (FastAPI) on port 8000
cd /d "%~dp0backend"
REM 默认只听本机；需要局域网访问时先设置 APP_HOST=0.0.0.0（注意当前版本无鉴权）
if "%APP_HOST%"=="" set APP_HOST=127.0.0.1
echo [backend] starting uvicorn on http://%APP_HOST%:8000 ...
.venv\Scripts\python.exe -m uvicorn main:app --host %APP_HOST% --port 8000
pause
