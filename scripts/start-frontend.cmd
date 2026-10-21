@echo off
REM Start frontend (Vite) on port 5173
cd /d "%~dp0frontend"
echo [frontend] starting vite on http://127.0.0.1:5173 ...
REM 不再硬编码本机 node 路径（换机器必挂）：先检查 node 是否可用
where node >nul 2>nul
if errorlevel 1 (
  echo [frontend] ERROR: no node found in PATH.
  echo           Install Node.js or add its directory to PATH, then retry.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('node -v') do echo [frontend] node %%i
call npm run dev
pause
