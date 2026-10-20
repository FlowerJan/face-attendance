@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo  face-attendance  ->  Gitee push helper
echo ============================================
echo.
echo [提示] 本脚本已改为安全模式：只做 push，绝不改写 commit 历史。
echo.
echo   旧版本会对 HEAD 执行 git commit --amend --reset-author，
echo   也就是重写"最新提交"的作者信息（并非注释里写的首个提交）。
echo   一旦最新提交已经推送，重写它会改变线上历史，
echo   并且让 Gitee 贡献图（绿点）的归属日期发生偏移。
echo.
echo   实际推送请使用同目录下的 push-to-gitee-safe.bat
echo.
echo.

if not exist "push-to-gitee-safe.bat" (
    echo [ERROR] 找不到 push-to-gitee-safe.bat，无法继续。
    pause
    exit /b 1
)

call "push-to-gitee-safe.bat"
exit /b %errorlevel%
