@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo  face-attendance  ->  Gitee push (safe)
echo  只推送，不改写任何 commit 历史
echo ============================================
echo.

set /p REPONAME=1) Target repo name [default: face-attendance]:

if "%REPONAME%"=="" set REPONAME=face-attendance

echo.
echo [1/5] local identity (used for NEW commits only)
REM ---------------------------------------------------------------
REM 重要：Gitee 绿点按 commit 的【作者邮箱】统计，该邮箱必须是
REM 账号在「设置 - 多邮箱管理」里已验证/绑定的地址。
REM 注意 Gitee 官方隐私邮箱是【单数】user.noreply.gitee.com 且带
REM +用户名 前缀，例如 12345+flowerjan10@user.noreply.gitee.com
REM 下面让你自己填，避免写死一个绑不上的地址。
REM ---------------------------------------------------------------
set /p GITMAIL=   your Gitee commit email (from https://gitee.com/profile/emails) [keep current]:
if "%GITMAIL%"=="" (
  for /f "delims=" %%e in ('git config user.email') do set GITMAIL=%%e
  echo   keeping current: %GITMAIL%
) else (
  git config user.email "%GITMAIL%"
)
git config user.name "FlowerJan10"
echo   user.name  = FlowerJan10
echo   user.email = %GITMAIL%
echo   ^> 打开 https://gitee.com/profile/emails 确认这个地址已验证，否则绿点不会亮
echo   ^> 跑 python scripts\doctor.py 可自查
echo.

echo [2/5] remote
git remote remove origin 2>nul
git remote add origin https://gitee.com/flowerjan10/%REPONAME%.git

echo [3/5] branch
git branch -M master

echo [4/5] pre-push check (ahead/behind vs remote)
git fetch origin
for /f %%i in ('git rev-list --left-right --count origin/master...master') do set DIVERGE=%%i
echo   behind / ahead  = %DIVERGE%
echo   (behind should be 0. If it is not 0, the remote has commits you do not have -)
echo    STOP and do NOT use force push. Run: git log --oneline origin/master..master)
if not "%DIVERGE:~0,1%"=="0" (
    echo.
    echo [STOP] local branch is BEHIND the remote. Resolve this before pushing.
    pause
    exit /b 1
)

echo.
echo   --- commits to be pushed ---
git log --oneline origin/master..master
echo   --- author emails (must be your Gitee-verified email for green dots) ---
git log --format="%%h  %%ad  %%ae  %%s" --date=short origin/master..master

echo.
echo [5/5] pushing (enter Gitee username + personal access token)
echo       NOTE: username is NOT your email; token is NOT your login password.
echo       Get a token: Gitee - Settings - Personal Access Tokens - projects (write)
echo.
echo   credential.helper is disabled for this push, so a stale cached token
echo   cannot cause "403 token username invalid". Your input is used directly.
echo.
git -c credential.helper= push -u origin master
if errorlevel 1 (
    echo.
    echo [FAILED] push failed. Common causes:
    echo   - token expired or wrong scope (needs projects write)
    echo   - wrong username / wrong personal access token
    echo   - repo name does not exist under your account
    echo   Nothing in your local repo was changed. Fix the token and re-run this script.
    pause
    exit /b 1
)

echo.
echo --- current head ---
git log --format="%%h  %%an <%%ae>  %%s" -n 10
echo.
echo Done. Open https://gitee.com/flowerjan10/%REPONAME% to verify.
pause
