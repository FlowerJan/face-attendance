#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""face-attendance 自主体检工具（无第三方依赖，Python 3.8+ 均可跑）

一次跑完，回答四个问题：
    1. 我的 git 身份配置对不对？（姓名 / 邮箱 / 在哪一层配置）
    2. 远端和本地同步吗？有没有漏推的提交？
    3. 仓库里有没有敏感信息残留？（令牌 / 真实邮箱 / 手机号）
    4. **绿点为什么不亮？**（Gitee 侧归属 + 贡献日历实测）

用法（在仓库根目录执行）：
    python scripts/doctor.py

只读脚本：不修改任何文件、不提交、不推送、不改 git 配置。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter

REPO_OWNER = "flowerjan10"
REPO_NAME = "face-attendance"
EXPECT_NAME = "FlowerJan10"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OK, WARN, BAD, INFO = "[ OK ]", "[WARN]", "[BAD ]", "[INFO]"


# ---------------------------------------------------------------- git 辅助
def git(*args, check=False):
    """执行 git 命令并返回 stdout 文本。"""
    try:
        r = subprocess.run(
            ("git",) + args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=os.getcwd(),
        )
        out = (r.stdout or "") + (r.stderr or "")
        if check and r.returncode != 0:
            return ""
        return out.strip()
    except FileNotFoundError:
        return ""


def git_lines(*args):
    out = git(*args)
    return [x for x in out.splitlines() if x.strip()] if out else []


def section(title):
    print("\n" + "=" * 68)
    print("  " + title)
    print("=" * 68)


def http_get(url, referer=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if referer:
        req.add_header("Referer", referer)
        # Gitee 的贡献日历是 AJAX 接口，缺这个头会返回 404
        req.add_header("X-Requested-With", "XMLHttpRequest")
        req.add_header("Accept", "*/*")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # 网络不通等
        return 0, str(e)


# ---------------------------------------------------------------- 1. 身份
def check_identity():
    section("1. Git 身份配置")
    problems = []

    name = git("config", "user.name")
    email = git("config", "user.email")
    scope = git("config", "--show-origin", "user.email")

    print(f"  user.name  = {name or '(未配置)'}")
    print(f"  user.email = {email or '(未配置)'}")

    if not email:
        problems.append("没有配置 user.email，提交后无法归属到任何账号")
    else:
        print(f"  配置来源   = {scope or '未知'}")

    # Gitee 的隐私邮箱域名是单数 user.noreply.gitee.com
    if email and "@users.noreply.gitee.com" in email:
        print(f"\n  {BAD} 邮箱域名是【复数 users】—— 那是 GitHub 的写法。")
        print("       Gitee 官方是【单数 user.noreply.gitee.com】，且带 +用户名 前缀，")
        print("       例如 12345+flowerjan10@user.noreply.gitee.com")
        print("       你现在这个地址 Gitee 从未生成过，账号里也不可能绑定，")
        print("       所以这批提交【不会计入贡献度绿点】。")
        print("       → 打开 https://gitee.com/profile/emails 抄下「提交邮箱」，")
        print("         然后 git config --global user.email \"<那个地址>\"")
        problems.append(f"邮箱域名错误：{email}（绿点不亮的根因）")
    elif email and "@user.noreply.gitee.com" in email:
        print(f"\n  {OK} 使用 Gitee 官方隐私邮箱域名（单数 user.noreply）")
        print("       仍需确认该地址在「多邮箱管理」里是【已验证】状态")
    elif email and "@" in email:
        print(f"\n  {INFO} 使用自定义邮箱 —— 必须已在 Gitee「多邮箱管理」中绑定并验证")
    return problems


# ---------------------------------------------------------------- 2. 同步
def check_sync():
    section("2. 远端同步状态")
    remotes = git("remote", "-v")
    print(remotes or "  (没有配置远端)")

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"  当前分支 = {branch or '(未知)'}")

    git("fetch", "origin")
    counts = git("rev-list", "--left-right", "--count", f"origin/{branch}...{branch}")
    behind = ahead = 0
    if counts:
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
    print(f"  远端领先(behind) = {behind}   本地领先(ahead) = {ahead}")

    problems = []
    if ahead:
        print(f"\n  {WARN} 有 {ahead} 个提交还没推送。执行：git push origin {branch}")
        problems.append(f"{ahead} 个提交待推送")
    else:
        print(f"\n  {OK} 本地与远端同步")

    pending = git_lines("log", "--format=%h  %ad  %ae  %s", "--date=short",
                        f"origin/{branch}..{branch}")
    if pending:
        print("\n  --- 待推送提交（注意作者邮箱）---")
        for line in pending:
            print("    " + line)
        emails = {l.split()[2] for l in pending if len(l.split()) > 2}
        bad = [e for e in emails if "@users.noreply.gitee.com" in e]
        if bad:
            print(f"\n  {WARN} 待推送提交里用了错误域名的邮箱：{bad}")
            print("       推上去也不会点亮绿点 —— 先修身份再提交。")
    return problems


# ---------------------------------------------------------------- 3. 敏感
def check_secrets():
    section("3. 敏感信息扫描（工作区 + 待推送内容）")
    problems = []

    tracked = git_lines("ls-files")
    print(f"  仓库跟踪文件数 = {len(tracked)}")

    # 3.1 令牌文件是否被忽略
    ignore = git("check-ignore", "-v", ".gitee-token")
    if ignore:
        print(f"  {OK} .gitee-token 已被 .gitignore 覆盖")
    else:
        print(f"  {WARN} .gitee-token 未被忽略 —— 确认它没被 git add 过")
        problems.append(".gitee-token 未被 gitignore")

    # 3.2 令牌是否泄露进提交历史
    token_hits = []
    for path in tracked:
        if not path.lower().endswith((".md", ".py", ".js", ".vue", ".sh", ".bat", ".cmd", ".txt", ".json")):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue
        for m in re.finditer(r"\b[0-9a-f]{32,}\b", content):
            token_hits.append((path, m.group()[:8] + "..."))
    if token_hits:
        print(f"  {BAD} 疑似令牌字面量出现在跟踪文件里：{token_hits[:5]}")
        problems.append("跟踪文件含疑似令牌")
    else:
        print(f"  {OK} 跟踪文件中无 32 位以上十六进制串（常见令牌形态）")

    # 3.3 邮箱
    email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    mail_hits = set()
    for path in tracked:
        if not path.lower().endswith((".md", ".py", ".js", ".vue", ".sh", ".bat", ".cmd", ".txt")):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for m in email_re.finditer(f.read()):
                    mail_hits.add(m.group())
        except OSError:
            continue
    real = [m for m in mail_hits if "example.com" not in m and "noreply" not in m]
    if real:
        print(f"  {WARN} 文档里出现的邮箱：{sorted(real)}")
        print("       若其中含真实 QQ 邮箱，确认已脱敏。")
    else:
        print(f"  {OK} 文档内无真实邮箱（仅 example.com / noreply）")

    # 3.4 手机号（1380000xxxx / 1390000xxxx 是本项目约定的假号段，不算泄露）
    FAKE_PREFIX = ("1380000", "1390000")
    phone_hits = set()
    for path in tracked:
        if not path.lower().endswith((".md", ".py", ".js", ".vue", ".sh", ".bat", ".cmd", ".txt", ".csv")):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for m in re.finditer(r"(?<!\d)1[3-9]\d{9}(?!\d)", f.read()):
                    if m.group().startswith(FAKE_PREFIX):
                        continue
                    phone_hits.add((path, m.group()))
        except OSError:
            continue
    if phone_hits:
        print(f"  {WARN} 文本中出现非假号段手机号：{sorted(phone_hits)[:5]}")
        print("       演示数据应使用 1380000xxxx / 1390000xxxx 假号段。")
    else:
        print(f"  {OK} 文本中无真实手机号（仅约定假号段）")
    return problems


# ---------------------------------------------------------------- 4. 绿点
def check_green_dots():
    section("4. 绿点（贡献日历）实测")
    problems = []

    # 4.1 仓库贡献者归属
    code, body = http_get(
        f"https://gitee.com/api/v5/repos/{REPO_OWNER}/{REPO_NAME}/contributors")
    print(f"  贡献者接口 HTTP {code}")
    if code == 200:
        try:
            data = json.loads(body)
            total = 0
            for c in data:
                total += int(c.get("contributions") or 0)
                flag = OK if c.get("name") == EXPECT_NAME else WARN
                print(f"    {flag} {c.get('email'):<44} {c.get('name'):<14} {c.get('contributions')} 条")
            print(f"  合计 {total} 条提交归属到 {EXPECT_NAME}")
            unknown = [c for c in data if c.get("name") != EXPECT_NAME]
            if unknown:
                problems.append(f"{len(unknown)} 个邮箱未归属到 {EXPECT_NAME}")
        except Exception:
            print("  响应解析失败")
    else:
        print("  取不到（可能网络受限，不影响本地结论）")

    # 4.2 贡献日历
    code, body = http_get(
        f"https://gitee.com/{REPO_OWNER}/contribution_calendar?year=2026",
        referer=f"https://gitee.com/{REPO_OWNER}")
    print(f"\n  贡献日历接口 HTTP {code}")
    if code == 200:
        text = body.replace("\\'", "'")
        boxes = re.findall(r"class='box ([a-z ]*)' data-content='([^']*)' date='(\d+)'", text)
        lit = []
        for cls, content, date in boxes:
            m = re.findall(r"(\d+)个贡献", content)
            n = int(m[0]) if m else 0
            if n > 0:
                lit.append((date, n))
        if lit:
            print(f"  有贡献的日期（{len(lit)} 天）：")
            for d, n in lit:
                print(f"    {d}  {n} 个贡献")
        else:
            print("  2026 年全年 0 个贡献 —— 绿点确实没亮")
            problems.append("贡献日历 2026 年无任何贡献")

    # 4.3 push 事件
    code, body = http_get(
        f"https://gitee.com/{REPO_OWNER}/contribution_timeline?year=2026",
        referer=f"https://gitee.com/{REPO_OWNER}/{REPO_NAME}")
    print(f"\n  推送时间线 HTTP {code}")
    if code == 200:
        try:
            ev = json.loads(body)
            print(f"  事件数 = {len(ev)}")
            for e in ev[-5:]:
                proj = e.get("project", {}).get("name_with_namespace", "?")
                print(f"    {e.get('created_at','')[:19]}  {e.get('action')}  "
                      f"{proj}  is_proper={e.get('is_proper')}")
        except Exception:
            print("  解析失败")

    print("\n  判定依据（Gitee 官方规则）：")
    print("    贡献度按 commit 的【作者邮箱】归属统计，且该邮箱必须是")
    print("    Gitee 账号在「设置 → 多邮箱管理」里绑定/验证过的地址。")
    print("    推送到远端 ≠ 自动点亮；作者邮箱没绑上就不计。")
    return problems


# ---------------------------------------------------------------- 5. 修复指引
def print_fix(problems):
    section("5. 需要你手动做的事")
    if not problems:
        print("  本地体检全部通过，无需处理。")
        return

    print("  ① 绿点修复（最重要，必须你本人在浏览器里做）")
    print("     a. 打开 https://gitee.com/profile/emails （多邮箱管理）")
    print("     b. 找到「提交邮箱 / Web 提交邮箱」那一栏，抄下完整地址")
    print("        · Gitee 隐私邮箱形如 12345+flowerjan10@user.noreply.gitee.com")
    print("          注意是【单数 user】，且带 +用户名 前缀")
    print("        · 或者是你已绑定验证过的真实邮箱")
    print("     c. 回到仓库，改本地配置（--global 影响你所有仓库）：")
    print("          git config --global user.email  \"上面抄下的地址\"")
    print("          git config --global user.name   \"FlowerJan10\"")
    print("     d. 做一次新提交验证：")
    print("          echo \"# 绿点验证\" >> README.md")
    print("          git add README.md && git commit -m \"docs: 绿点归属验证\"")
    print("          git push origin master")
    print("     e. 等 1~5 分钟刷新 https://gitee.com/flowerjan10 看绿点")
    print("        · 亮 → 成功，以后照这个配置提交即可")
    print("        · 不亮 → 回 b 确认该邮箱在多邮箱管理里是【已验证】状态")
    print("     完整步骤见 docs/OWNER-GUIDE.md 第 1 章")

    print("\n  ② 令牌")
    print("     令牌在 https://gitee.com/profile/personal_access_tokens 生成，")
    print("     勾选 projects 读写权限。存到仓库根目录的 .gitee-token 文件（已被 gitignore），")
    print("     不要写进任何被跟踪的文件里。用完可以随时在页面上吊销。")

    print("\n  ③ 其它待办")
    for p in problems:
        print(f"     - {p}")


def main():
    print("face-attendance 自主体检 —— 只读，不改任何东西")
    print("仓库目录:", os.getcwd())
    if not os.path.isdir(".git"):
        print("\n[FAIL] 当前目录不是 git 仓库，请先 cd 到仓库根目录。")
        return 2

    problems = []
    problems += check_identity()
    problems += check_sync()
    problems += check_secrets()
    problems += check_green_dots()
    print_fix(problems)

    print("\n" + "=" * 68)
    print("  体检结束。完整操作手册见 docs/OWNER-GUIDE.md")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
