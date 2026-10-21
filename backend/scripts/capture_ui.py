# -*- coding: utf-8 -*-
"""逐页截图，生成 README 用的界面图。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/capture_ui.py

流程：
    1. 先跑 pii_guard.py —— 演示库里若残留「看起来像真的」手机号，直接中止。
       背景：本项目曾把带手机号的截图提交到公开仓库，文本扫描全绿查不出来
       （泄露载体是 PNG 像素，不是文本），所以把检查前移到数据层。
    2. 用 playwright-core 驱动本机 Edge 逐页截图。

依赖：
    pip install playwright-core
    本机需已安装 Edge（默认 C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe，
    可用环境变量 EDGE_PATH 覆盖）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
OUT = BACKEND.parent / "docs" / "screenshots"
BASE = os.environ.get("CAPTURE_BASE", "http://127.0.0.1:5173")

EDGE = os.environ.get(
    "EDGE_PATH",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

# playwright-core 装在 WorkBuddy 的 node workspace 里
NODE_WORKSPACE = Path(
    r"C:\Users\Administrator\.workbuddy\binaries\node\workspace"
)

# (路由, 输出文件名, 截图前需要点击的文本, 页面等待毫秒, 点击后额外等待毫秒)
PAGES = [
    ("/students", "01-students.png", None, 2500, 0),
    ("/stats", "02-stats.png", None, 3500, 0),
    ("/audit", "03-audit.png", "运行审计", 1500, 7000),
    ("/faces", "04-enroll.png", None, 2500, 0),
    ("/notices", "05-notice.png", None, 2500, 0),
]

JS_SNIPPET = r"""
const { chromium } = require('playwright-core');
const path = require('path');
const OUT = process.argv[2];
const BASE = process.argv[3];
const EDGE = process.argv[4];
const PAGES = JSON.parse(process.argv[5]);
(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE, args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await (await browser.newContext({
    viewport: { width: 1080, height: 480 },
  })).newPage();
  for (const p of PAGES) {
    try {
      await page.goto(BASE + p.route, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(p.wait);
      if (p.click) {
        try {
          await page.getByText(p.click, { exact: false }).first().click({ timeout: 8000 });
          await page.waitForTimeout(p.extra || 6000);
        } catch (e) {
          console.log('  click miss: ' + p.click);
        }
      }
      await page.screenshot({ path: path.join(OUT, p.out) });
      console.log('OK ' + p.out);
    } catch (e) {
      console.log('FAIL ' + p.out + ' ' + String(e).slice(0, 160));
    }
  }
  await browser.close();
})();
"""


def run_pii_guard() -> bool:
    """截图前必须通过 PII 守卫，不通过直接中止。"""
    print("[capture] 检查演示数据是否含疑似真实手机号 ...")
    r = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "pii_guard.py")],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
        cwd=str(BACKEND),
    )
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print("\n[capture] ✗ PII 守卫未通过，已中止截图。")
        print("[capture]   先把库里的手机号改成 1380000xxxx 段假号，再重跑。")
        return False
    return True


def main() -> int:
    if not run_pii_guard():
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    js_path = BACKEND / "scripts" / ".capture_ui.js"
    js_path.write_text(JS_SNIPPET, encoding="utf-8")

    env = dict(os.environ)
    env["NODE_PATH"] = str(NODE_WORKSPACE / "node_modules")
    node_exe = os.environ.get("NODE_EXE", "node")
    payload = [
        {"route": r, "out": o, "click": c, "wait": w, "extra": x}
        for (r, o, c, w, x) in PAGES
    ]
    cmd = [
        node_exe, str(js_path),
        str(OUT), BASE, EDGE, json.dumps(payload),
    ]
    print("[capture] 开始截图 ...")
    try:
        r = subprocess.run(cmd, env=env, cwd=str(NODE_WORKSPACE), timeout=300)
    finally:
        try:
            js_path.unlink()
        except OSError:
            pass
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
