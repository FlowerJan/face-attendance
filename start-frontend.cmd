@echo off
REM Start frontend (Vite) on port 5173
cd /d "%~dp0frontend"
echo [frontend] starting vite on http://127.0.0.1:5173 ...
set PATH=C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3;%PATH%
call npm run dev
pause
