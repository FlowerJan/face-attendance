#!/usr/bin/env bash
# 无人值守推送：读取 .gitee-token 完成 push，全程无交互。
# 供每日自动任务调用。失败时以非 0 退出码结束并打印原因。
set -uo pipefail

cd "$(dirname "$0")" || exit 1
REPO_ROOT="$(cd .. && pwd)"

REMOTE="https://gitee.com/flowerjan10/face-attendance.git"
BRANCH="master"

# 令牌来源优先级：环境变量 GITEE_TOKEN > 仓库根目录 .gitee-token
# 只在根目录保留一份明文，避免轮换时漏改 / 泄露面扩大。
TOKEN="${GITEE_TOKEN:-}"
TOKEN_FILE=""
if [ -z "$TOKEN" ]; then
  TOKEN_FILE="$REPO_ROOT/.gitee-token"
  if [ ! -f "$TOKEN_FILE" ]; then
    echo "[FAIL] 未找到 Gitee 令牌。"
    echo "       请提供 Gitee 私人令牌（设置 -> 私人令牌 -> projects 权限），二选一："
    echo "         1) 写入仓库根目录的 .gitee-token（首行纯令牌文本）"
    echo "         2) 设置环境变量 GITEE_TOKEN"
    exit 2
  fi
  TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE" | head -n1)"
fi
if [ -z "$TOKEN" ]; then
  echo "[FAIL] Gitee 令牌为空。"
  exit 2
fi

echo "[1/3] fetch origin"
git fetch origin || { echo "[FAIL] fetch 失败（网络或远端地址问题）。"; exit 1; }

BEHIND=$(git rev-list --left-right --count "origin/$BRANCH...$BRANCH" | awk '{print $1}')
AHEAD=$(git rev-list --left-right --count "origin/$BRANCH...$BRANCH" | awk '{print $2}')
echo "      behind=$BEHIND ahead=$AHEAD"

if [ "$BEHIND" != "0" ]; then
  echo "[FAIL] 本地落后远端 $BEHIND 个提交，放弃推送（不要 force push）。"
  echo "       查看差异: git log --oneline origin/$BRANCH..$BRANCH"
  exit 1
fi

if [ "$AHEAD" = "0" ]; then
  echo "[SKIP] 本地与远端已一致，无需推送。"
  exit 0
fi

echo "[2/3] commits to be pushed:"
git log --oneline "origin/$BRANCH..$BRANCH"

echo "[3/3] push (credential.helper disabled to avoid stale cached token)"
# 令牌只在这一条命令的临时 URL 中出现，不写入 .git/config
git -c credential.helper= push -u "https://flowerjan10:${TOKEN}@gitee.com/flowerjan10/face-attendance.git" "$BRANCH" 2>&1 | sed -E 's#(https://[^:/]+:)[^@]+@#\1***@#g'
STATUS=${PIPESTATUS[0]}

# 恢复干净的远端地址与 upstream，避免令牌残留在 .git/config
git remote set-url origin "$REMOTE" >/dev/null 2>&1
git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null 2>&1
git fetch origin >/dev/null 2>&1

if [ "$STATUS" != "0" ]; then
  echo "[FAIL] 推送失败（退出码 $STATUS）。"
  echo "       常见原因：令牌过期 / 权限不含 projects / 仓库名有误。"
  echo "       远端地址与 upstream 已恢复干净状态，本地仓库未受影响。"
  exit 1
fi

SYNC=$(git rev-list --left-right --count "origin/$BRANCH...$BRANCH" | awk '{print $1"/"$2}')
echo "[OK] 推送成功。同步状态 behind/ahead = $SYNC"
exit 0
