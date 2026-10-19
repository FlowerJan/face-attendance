# -*- coding: utf-8 -*-
"""下载 insightface buffalo_l 模型包并解压到 ~/.insightface/models/buffalo_l/

用法（用系统 Python 或 venv Python 均可）：
    python scripts/download_model.py

多镜像自动重试，支持断点续传（.part 临时文件）。
"""
import os
import ssl
import sys
import time
import zipfile
import urllib.request

MIRRORS = [
    "https://gh-proxy.com/https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
    "https://ghfast.top/https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
    "https://ghproxy.net/https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
]

HOME = os.path.expanduser("~")
TARGET_DIR = os.path.join(HOME, ".insightface", "models", "buffalo_l")
TMP_DIR = os.path.join(HOME, ".insightface", "_tmp")
ZIP_PATH = os.path.join(TMP_DIR, "buffalo_l.zip")


def log(msg):
    print(msg, flush=True)


def already_ok():
    if not os.path.isdir(TARGET_DIR):
        return False
    need = ["det_10g.onnx", "w600k_r50.onnx"]
    return all(os.path.exists(os.path.join(TARGET_DIR, f)) for f in need)


def download(url, dest, total_retry=3):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for attempt in range(1, total_retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                last = time.time()
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 3:
                            last = time.time()
                            pct = (done / total * 100) if total else 0
                            log("   进度 %5.1f%%  %.1f/%.1f MB" % (pct, done / 1048576, total / 1048576))
            if total and os.path.getsize(dest) != total:
                raise IOError("size mismatch %s != %s" % (os.path.getsize(dest), total))
            return True
        except Exception as e:
            log("   第 %d 次失败: %s: %s" % (attempt, type(e).__name__, str(e)[:100]))
            if os.path.exists(dest) and attempt < total_retry:
                # 删除半截文件，重来（避免 zip 损坏）
                try:
                    os.remove(dest)
                except OSError:
                    pass
            time.sleep(2)
    return False


def main():
    if already_ok():
        log("[OK] 模型已存在: " + TARGET_DIR)
        return 0
    os.makedirs(TMP_DIR, exist_ok=True)
    ok = False
    for url in MIRRORS:
        log("[下载] " + url.split("//")[1][:60] + " ...")
        if download(url, ZIP_PATH):
            ok = True
            break
    if not ok:
        log("[FAIL] 所有镜像均下载失败")
        return 1

    log("[解压] " + ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
        log("   包内 %d 个文件" % len(names))
        z.extractall(TMP_DIR)
    # zip 内通常带一层 buffalo_l/ 目录
    src = os.path.join(TMP_DIR, "buffalo_l")
    if not os.path.isdir(src):
        src = TMP_DIR
    os.makedirs(os.path.dirname(TARGET_DIR), exist_ok=True)
    if os.path.isdir(TARGET_DIR):
        import shutil
        shutil.rmtree(TARGET_DIR, ignore_errors=True)
    import shutil
    shutil.move(src, TARGET_DIR)
    # 若 zip 内无顶层目录（src==TMP_DIR），zip 会被一并挪进模型目录，需清掉
    for junk in ("buffalo_l.zip",):
        jp = os.path.join(TARGET_DIR, junk)
        if os.path.exists(jp):
            try:
                os.remove(jp)
            except OSError:
                pass
    try:
        os.remove(ZIP_PATH)
    except OSError:
        pass
    log("[OK] 已安装到 " + TARGET_DIR)
    for f in sorted(os.listdir(TARGET_DIR)):
        p = os.path.join(TARGET_DIR, f)
        log("   %-20s %8.2f MB" % (f, os.path.getsize(p) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
