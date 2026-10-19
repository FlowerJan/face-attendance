# -*- coding: utf-8 -*-
"""用 agent-browser 逐页截图，并按页面语义命名。

用法：python scripts/capture_ui.py
依赖：已安装 agent-browser（可复用本机 Edge，免下载 Chromium）
"""
import os
import re
import subprocess
import sys
import time

NODE_DIR = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3"
# 直接调用原生 exe：Windows 下用 subprocess 调 .cmd 会失败
AB = os.path.join(NODE_DIR, "node_modules", "agent-browser", "bin",
                  "agent-browser-win32-x64.exe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = r"E:\workbuddy-projects\face-attendance\docs\screenshots"
BASE = "http://localhost:5173"

# (路由, 输出文件名, 截图前需要点击的文本)
PAGES = [
    ("/students", "01_学生管理.png", None),
    ("/stats", "02_信息汇总.png", None),
    ("/audit", "03_脸库审计.png", "运行审计"),
    ("/faces", "04_人脸录入.png", None),
    ("/notices", "05_公告管理.png", None),
]


def env():
    e = dict(os.environ)
    e["PATH"] = NODE_DIR + os.pathsep + e.get("PATH", "")
    e["AGENT_BROWSER_EXECUTABLE_PATH"] = EDGE
    e["AGENT_BROWSER_SCREENSHOT_DIR"] = OUT
    return e


def run(*args, timeout=120):
    r = subprocess.run([AB, *args], capture_output=True, text=True,
                       errors="ignore", env=env(), timeout=timeout, cwd=OUT)
    return (r.stdout or "") + (r.stderr or "")


def main():
    os.makedirs(OUT, exist_ok=True)
    run("close")          # 清掉可能残留的守护进程，避免卡住
    time.sleep(1)
    # 清理旧图
    for f in os.listdir(OUT):
        if f.lower().endswith(".png"):
            try:
                os.remove(os.path.join(OUT, f))
            except OSError:
                pass

    ok, fail = [], []
    for route, outname, click_text in PAGES:
        url = BASE + route
        log = run("open", url)
        if "http" not in log:
            fail.append((outname, "open 失败: " + log.strip()[:120]))
            continue
        time.sleep(2.5)  # 等接口返回 + 图表渲染

        if click_text:
            # ref 会失效，改用文本选择器
            clog = run("click", f"text={click_text}")
            time.sleep(3)  # 等审计接口返回
            if "✗" in clog:
                fail.append((outname, "点击失败: " + clog.strip()[:120]))

        slog = run("screenshot")
        m = re.search(r"saved to (.+\.png)", slog)
        if not m:
            fail.append((outname, "截图失败: " + slog.strip()[:120]))
            continue
        src = m.group(1).strip()
        dst = os.path.join(OUT, outname)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
            os.replace(src, dst)
        size = os.path.getsize(dst) if os.path.exists(dst) else 0
        ok.append((outname, route, size))

    run("close")

    print("=" * 60)
    for name, route, size in ok:
        flag = "OK  " if size > 20000 else "SMALL"
        print(f"  [{flag}] {name:<18} {route:<12} {size/1024:.0f} KB")
    for name, why in fail:
        print(f"  [FAIL] {name:<18} {why}")
    print("=" * 60)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
