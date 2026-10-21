#!/usr/bin/env bash
# Gitee 绿点 / 贡献归属巡检
#
# 用途：确认提交作者邮箱已正确归属到 Gitee 账号，且没有意外的未验证邮箱
#       在悄悄「吃掉」贡献点。
#
# 原理：Gitee 的 contributions 接口按 commit 的作者邮箱聚合，并返回该邮箱
#       归属到的 name。若某个邮箱的 name 不是你的账号名，说明它没被验证，
#       那些提交的贡献点不会计入你的绿点。
#
# 用法：bash check-gitee-contrib.sh
# 需要 .gitee-token 中的令牌（与推送脚本共用同一个文件）。

set -uo pipefail

cd "$(dirname "$0")" || exit 1

TOKEN_FILE=".gitee-token"
REPO="flowerjan10/face-attendance"
EXPECT_NAME="FlowerJan10"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "[FAIL] $TOKEN_FILE 不存在。"
  echo "       需要 Gitee 私人令牌（设置 -> 私人令牌 -> projects 权限）。"
  exit 2
fi

TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE" | head -n1)"
if [ -z "$TOKEN" ]; then
  echo "[FAIL] $TOKEN_FILE 为空。"
  exit 2
fi

echo "=== 1. 远端提交作者邮箱分布 ==="
CONTRIB=$(curl -s "https://gitee.com/api/v5/repos/$REPO/contributors?access_token=$TOKEN")
echo "$CONTRIB" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    print('  [FAIL] 无法解析 API 响应（令牌可能无效）')
    sys.exit(1)
if not isinstance(data, list) or not data:
    print('  [FAIL] 响应为空或格式异常:', str(data)[:200])
    sys.exit(1)
total = 0
for c in data:
    total += int(c.get('contributions') or 0)
    print(f\"  {c.get('email'):<42} {c.get('name'):<16} {c.get('contributions')} 条\")
print(f'  合计 {total} 条提交')
" || exit 1

echo ""
echo "=== 2. 归属检查（name 必须是 $EXPECT_NAME）==="
echo "$CONTRIB" | python -c "
import sys, json
EXPECT = '$EXPECT_NAME'
data = json.load(sys.stdin)
bad = [c for c in data if c.get('name') != EXPECT]
if not bad:
    print('  ✅ 全部邮箱均已正确归属到本账号')
    print('     → 贡献图（绿点）统计不受影响')
    sys.exit(0)
for c in bad:
    print(f\"  ⚠️  {c.get('email')} 的 name 是 '{c.get('name')}'，非 {EXPECT}\")
    print(f\"     {c.get('contributions')} 条提交可能不计入你的绿点\")
    print('     修复：Gitee 设置 -> 多邮箱管理 -> 添加并验证该邮箱')
sys.exit(1)
" || exit 1

echo ""
echo "=== 3. 本地提交邮箱（应与远端一致）==="
git log --format="  %ad | %ae" --date=format:"%Y-%m-%d" | tac | uniq -c | sed 's/^/  /'

echo ""
echo "=== 4. 提交日期分布（绿点按天统计）==="
git log --format="%ad" --date=format:"%Y-%m-%d" | sort | uniq -c | awk '{printf "  %s  %d 条\n", $2, $1}'

echo ""
echo "巡检完成。"
