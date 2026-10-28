# -*- coding: utf-8 -*-
"""估算 docker build 上下文体积，验证 .dockerignore 有没有真的把大目录排掉。

为什么需要它：`.dockerignore` 里写 `.venv` 只会匹配根目录那一层，
子目录里的 `backend/.venv`（本机实测 875MB）照样会被 `COPY backend ./backend` 打进镜像。
这个脚本不依赖 Docker，用来在没有 Docker 的机器上量化"构建上下文到底有多大"。

用法：
    python tools/check_docker_context.py <仓库根目录>

⚠️ 这是**本项目的近似实现，不是 Docker 官方 matcher**，只覆盖仓库用到的几种 pattern
   （目录名、`**/x/`、`*.ext`、相对路径前缀）。用来看数量级与回归对比，
   不要拿它当 Docker 行为的权威依据 —— 真正的判定仍然要跑 docker build。
"""
import fnmatch
import os
import re
import sys

ROOT = sys.argv[1]
IGNORE_FILE = os.path.join(ROOT, ".dockerignore")

patterns = []
with open(IGNORE_FILE, encoding="utf-8") as f:
    for raw in f:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)


def norm(p: str) -> str:
    return p.replace("\\", "/")


def is_dir_pat(pat: str) -> bool:
    return pat.endswith("/")


def match_path(relpath: str, isdir: bool, pat: str) -> bool:
    p = pat.strip("/")
    parts = p.split("/")
    rp = relpath.strip("/")
    rparts = rp.split("/")
    # **/X 形式：只要路径里出现过这个名字的目录即可
    lead = parts[0] == "**"
    tail = parts[1:] if lead else parts
    # 简化处理：把 ** 当作匹配任意深度的前缀
    if tail:
        last = tail[-1]
        if is_dir_pat(pat):
            if lead:
                return last in rparts
            # 非 lead 的目录 pattern：按相对根路径前缀匹配
            return rparts[: len(tail)] == tail
        if lead:
            return last in [os.path.basename(x) for x in rparts]
        if fnmatch.fnmatch(rp, p):
            return True
        return len(rparts) == len(tail) and all(
            fnmatch.fnmatch(a, b) for a, b in zip(rparts, tail)
        )
    return False


def ignored(relpath: str, isdir: bool) -> bool:
    rp = norm(relpath)
    for pat in patterns:
        try:
            if match_path(rp, isdir, pat):
                return True
        except Exception:
            pass
    return False


def scan():
    total = {"n": 0, "size": 0}
    kept = {"n": 0, "size": 0}
    dropped = []
    # 剪枝：被忽略的目录不再往下走
    stack = [("", True)]
    while stack:
        rel, isdir = stack.pop()
        if rel and ignored(rel, isdir):
            dropped.append((rel, isdir))
            continue
        abspath = os.path.join(ROOT, rel) if rel else ROOT
        if not isdir:
            try:
                sz = os.path.getsize(abspath)
            except OSError:
                sz = 0
            total["n"] += 1
            total["size"] += sz
            kept["n"] += 1
            kept["size"] += sz
            continue
        try:
            entries = sorted(os.listdir(abspath))
        except OSError:
            continue
        for e in entries:
            child_rel = f"{rel}/{e}" if rel else e
            child = os.path.join(abspath, e)
            stack.append((child_rel, os.path.isdir(child)))
    return kept, dropped


kept, dropped = scan()
mb = lambda b: round(b / 1024 / 1024, 1)
print(f"上下文保留文件数: {kept['n']}   体积: {mb(kept['size'])} MB")
print("被忽略的顶层项:")
for rel, isdir in dropped:
    if rel.count("/") <= 2:
        try:
            sz = 0
            rootp = os.path.join(ROOT, rel)
            for dp, dn, fn in os.walk(rootp):
                for f in fn:
                    try:
                        sz += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
            print(f"  - {rel}{'/' if isdir else ''}  ({mb(sz)} MB)")
        except OSError:
            print(f"  - {rel}")
