"""API 压力测试 —— 回答"这套接口能扛多少并发"。

设计上刻意**分组测**，而不是只报一个漂亮的总数：

    A 组 纯业务接口：DB 读写 / 统计聚合，瓶颈在 SQLite 与序列化
    B 组 含模型推理接口：SCRFD 检测 + ArcFace 前向，瓶颈在 CPU

为什么必须分开：这两组的耗时差一个数量级以上，混在一起报"平均响应 xx ms"
是自欺欺人 —— 平均数会掩盖推理接口的存在，而恰恰是它决定了系统的真实容量。
分开报，才能说清楚"瓶颈在哪、为什么、要优化该动哪里"。

用法（需后端已启动，Milvus-Lite 单进程独占）：
    .venv\\Scripts\\python.exe scripts\\bench_api.py
    .venv\\Scripts\\python.exe scripts\\bench_api.py --concurrency 50 --requests 100
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.environ.get("BENCH_BASE", "http://127.0.0.1:8000")
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_faces")


def _request(method: str, path: str, body: bytes | None = None,
             content_type: str | None = None, timeout: float = 60.0) -> tuple[int, float]:
    """单次请求，返回 (状态码, 耗时秒)。异常记 0 并让调用方计入错误。"""
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.perf_counter() - t0
    except Exception:
        return 0, time.perf_counter() - t0


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def run_group(name: str, tasks: list[tuple], concurrency: int, warmup: int = 2) -> dict:
    """tasks: [(label, method, path, body, content_type), ...]"""
    print(f"\n=== {name} ===")
    # warmup：先串行跑几轮，把模型加载、连接池、JIT 预热掉，
    # 否则第一个请求的耗时（含首次推理）会污染 P95。
    for label, method, path, body, ct in tasks:
        for _ in range(warmup):
            _request(method, path, body, ct)

    results = []
    for label, method, path, body, ct in tasks:
        latencies: list[float] = []
        errors = 0
        t_start = time.perf_counter()
        with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_request, method, path, body, ct)
                    for _ in range(concurrency)]
            for f in futures.as_completed(futs):
                code, dt = f.result()
                latencies.append(dt)
                if not (200 <= code < 300):
                    errors += 1
        wall = time.perf_counter() - t_start

        n = len(latencies)
        row = {
            "接口": label,
            "并发": concurrency,
            "请求数": n,
            "P50(ms)": round(_percentile(latencies, 50) * 1000, 1),
            "P95(ms)": round(_percentile(latencies, 95) * 1000, 1),
            "P99(ms)": round(_percentile(latencies, 99) * 1000, 1),
            "最大(ms)": round(max(latencies) * 1000, 1),
            "错误数": errors,
            "吞吐(QPS)": round(n / wall, 2),
        }
        results.append(row)
        print("  {接口:<28} P50 {P50(ms):>8.1f}  P95 {P95(ms):>8.1f}  "
              "P99 {P99(ms):>8.1f}  最大 {最大(ms):>8.1f}  错误 {错误数}  "
              "QPS {吞吐(QPS)}".format(**row))
    return {"group": name, "rows": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--requests", type=int, default=50,
                    help="每组每接口的请求数（默认等于并发数，即打满一轮）")
    ap.add_argument("--no-heavy", action="store_true", help="跳过含模型推理的 B 组")
    args = ap.parse_args()

    # 探活
    code, _ = _request("GET", "/api/health")
    if code != 200:
        print(f"后端未就绪：{BASE} -> {code}")
        return 1

    today = date.today().isoformat()

    # ---- A 组：纯业务接口（无模型推理）----
    group_a = [
        ("health 健康检查", "GET", "/api/health", None, None),
        ("students 分页查询", "GET", "/api/students?page=1&page_size=20", None, None),
        ("attendance 当日汇总", "GET", f"/api/attendance/summary?date={today}", None, None),
    ]
    out_a = run_group("A 组 · 纯业务接口（瓶颈：SQLite / 序列化）", group_a, args.concurrency)

    out_b = {"group": "B 组", "rows": []}
    if not args.no_heavy:
        # ---- B 组：含模型推理 ----
        img_path = None
        if os.path.isdir(SAMPLE_DIR):
            for fn in sorted(os.listdir(SAMPLE_DIR)):
                if fn.lower().endswith((".jpg", ".jpeg", ".png")):
                    img_path = os.path.join(SAMPLE_DIR, fn)
                    break
        if not img_path:
            print("\n（跳过 B 组：未找到 scripts/sample_faces/ 下的样本图）")
        else:
            with open(img_path, "rb") as f:
                img = f.read()
            # multipart 手工拼装，避免依赖 requests
            boundary = "----benchboundary"
            def multipart(data: bytes) -> bytes:
                return (f"--{boundary}\r\n"
                        'Content-Disposition: form-data; name="file"; filename="bench.jpg"\r\n'
                        "Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
            ct = f"multipart/form-data; boundary={boundary}"
            group_b = [
                ("faces/preview 门控预检", "POST", "/api/faces/preview", multipart(img), ct),
                ("attendance/checkin 签到", "POST", "/api/attendance/checkin", multipart(img), ct),
                ("audit/duplicates 全库审计", "GET", "/api/audit/duplicates", None, None),
            ]
            out_b = run_group(
                f"B 组 · 含模型推理（样本 {os.path.basename(img_path)}，瓶颈：CPU）",
                group_b, args.concurrency)

    payload = {"concurrency": args.concurrency, "groups": [out_a, out_b]}
    # 落在脚本旁边并入库：README 引用它作为原始数据，可被第三方核对。
    # （注意与 backend/.repro/ 区分 —— 那个目录放的是复现实验的独立向量库，已被 .gitignore 忽略）
    out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_result.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {out_file}")

    # 结论提示：把两组的量级差说出来，避免只挑好看的那组报
    a95 = [r["P95(ms)"] for r in out_a["rows"]]
    b95 = [r["P95(ms)"] for r in out_b["rows"]]
    if a95 and b95:
        print(f"\n量级对比：A 组 P95 {min(a95):.0f}~{max(a95):.0f} ms ｜ "
              f"B 组 P95 {min(b95):.0f}~{max(b95):.0f} ms "
              f"（相差约 {max(b95) / max(1.0, max(a95)):.0f} 倍）")
        print("注意：只报 A 组属于选择性呈现；两组都报并说明瓶颈来源，才是可核查的口径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
