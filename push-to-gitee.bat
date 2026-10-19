@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo  face-attendance  ->  Gitee push helper
echo ============================================
echo.

set /p GITNAME=1) Your commit author name   [e.g. FlowerJan10]:
set /p GITMAIL=2) Your Gitee verified email [must match Gitee profile]:
set /p REPONAME=3) Target repo name         [default: face-attendance]:

if "%GITNAME%"=="" set GITNAME=FlowerJan10
if "%REPONAME%"=="" set REPONAME=face-attendance

echo.
echo [1/4] setting local git identity
git config user.name "%GITNAME%"
git config user.email "%GITMAIL%"

echo [2/4] fixing the author of the first commit
git commit --amend --reset-author --no-edit

echo [3/4] setting remote
git remote remove origin 2>nul
git remote add origin https://gitee.com/flowerjan10/%REPONAME%.git

echo [4/4] pushing (you will be asked for Gitee username + password/personal access token)
git branch -M master
git push -u origin master

echo.
echo --- current head ---
git log --format="%%h  %%an <%%ae>  %%s" -n 1
echo.
echo Done. Open https://gitee.com/flowerjan10/%REPONAME% to verify.
pause
