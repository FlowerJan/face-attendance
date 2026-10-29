# -*- coding: utf-8 -*-
"""M0 → M6 全链路跑批：一条命令把六个里程碑依次跑完。

# 为什么要这个脚本

本仓库的能力是按「里程碑」分阶段建起来的，但每个阶段的验证脚本散落在
`backend/scripts/` 下，各自的**前置条件还不一样**，手动串起来容易踩三类坑：

| 坑 | 现象 | 本脚本怎么处理 |
|---|---|---|
| **Milvus Lite 是单进程文件锁** | 同一个 `backend/milvus_lite.db` 只能被一个进程打开。同时跑两个脚本 → `DataDirLockedError / Open local milvus failed` | 把「独占主库」的阶段（播种、e2e、压测）与「用独立评测库」的阶段（各种 sweep）**物理串行**，绝不并发 |
| **e2e 需要后端在跑** | `e2e_test.py` 打 `http://127.0.0.1:8000`，后端没起直接连接失败 | 跑 e2e / API 压测前**自动起后端**，跑完**自动停** |
| **seed_demo 要先停后端** | 播种要写主库，后端开着会抢 Milvus 锁 | 播种前**自动检测并停掉**占用主库的后端 |

# 用法

    cd backend
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py

    # 只跑某几个阶段（阶段号可组合，逗号分隔；也支持 --from/--to）
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py M0,M5,M6
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py --from M5
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py --to M3

    # 只看各阶段要干什么，不真跑
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py --dry-run

    # 跳过耗时的重活（M2 画质标定 / M6 阈值扫描）
    .venv\Scripts\python.exe scripts\run_all_m0_m6.py --skip-slow

# 阶段与里程碑的对应

| 阶段 | 里程碑 | 干什么 | 依赖后端？ | 占用主库？ |
|---|---|---|---|---|
| M0 | 环境与模型落地 | 校验 venv / 依赖 / 模型 / 样本人脸素材 | 否 | 否 |
| M1 | 单元测试 | pytest 全量 | 否 | 否（monkeypatch） |
| M2 | 画质门控标定 | 7 条规则的阈值实测标定 | 否 | 否（离线推理） |
| M3 | 播种演示数据 | 造学生 + 真实人脸录入 | **需要停后端** | **是** |
| M4 | 端到端联调 | 全接口 68 项断言 | **需要起后端** | **是** |
| M5 | 人脸识别演示 | 真实推理的识别闭环 + 一人多号歧义检测 | 否（隔离环境） | 否 |
| M6 | 阈值标定 | 审计阈值 sweep + 签到阈值 sweep | 否（独立评测库） | 否 |

> M2/M6 各自还要跑 **API 压测** 与 **SQLite 并发写对照**，这两项需要后端在跑，
> 由本脚本在对应阶段内自动起停。

# 注意

- 本脚本**不会**改动你的真实演示库：M6 的 sweep 全部用独立评测库（`backend/.eval/`），
  M3 的播种是项目 README 里既有的标准流程（`--drop` 会重建演示库，这是预期行为）。
- 阶段失败会**立即停止**并打印失败原因，不会带着错误继续往下跑（避免后面阶段给出误导性结论）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
IS_WIN = os.name == "nt"

# 后端监听地址与端口（与 backend/main.py、scripts/start-backend.cmd 保持一致）
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
# 用于认领端口：本项目后端 /api/system/status 返回体里的 app 标识。
# 端口上跑的是别人的服务时，绝不能动它（见 stop_backend）。
PROJECT_APP_MARK = "学生刷脸考勤系统"


# ────────────────────────────────────────────────────────────────────
# 阶段定义
# ────────────────────────────────────────────────────────────────────
@dataclass
class Step:
    """一个可执行步骤。"""

    title: str
    argv: list[str]
    # 后端状态要求："any" 不关心 / "stopped" 必须停 / "running" 必须起
    need_backend: str = "any"
    slow: bool = False
    # 从输出里提取的"成功标志"（任一命中即认为该步骤通过）
    expect: list[str] = field(default_factory=list)
    # 期望的退出码，None 表示只要 0
    cwd: Path | None = None


@dataclass
class Stage:
    """一个里程碑阶段（若干步骤）。"""

    code: str
    name: str
    goal: str
    steps: list[Step]


def _py(*args: str) -> list[str]:
    """用 venv 解释器跑一个脚本。"""
    return [str(VENV_PY), *args]


STAGES: list[Stage] = [
    Stage(
        code="M0",
        name="环境与模型落地",
        goal="确认 venv / 依赖 / 模型 / 真实人脸素材都在位 —— 后面每一环都建立在这上面",
        steps=[
            Step(
                title="校验运行环境与素材",
                argv=_py("-c", r'''
import os, sys, importlib
from pathlib import Path

# 以 -c 运行时没有 __file__，工作目录由调用方设为 backend/
BACKEND = Path.cwd()
ok = True

def check(label, cond, detail=""):
    global ok
    print(f"  {'OK ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        ok = False

check("Python 版本 >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])

for mod in ["fastapi", "uvicorn", "sqlalchemy", "cv2", "numpy", "insightface", "pymilvus"]:
    try:
        importlib.import_module(mod)
        check(f"依赖 {mod}", True)
    except Exception as e:
        check(f"依赖 {mod}", False, type(e).__name__)

model_dir = Path(os.path.expanduser("~")) / ".insightface" / "models" / "buffalo_l"
onnx = list(model_dir.glob("*.onnx")) if model_dir.is_dir() else []
check("buffalo_l 模型", len(onnx) >= 5, f"{len(onnx)} 个 onnx @ {model_dir}")

samples = BACKEND / "scripts" / "sample_faces"
imgs = sorted(samples.glob("*.jpg")) if samples.is_dir() else []
check("真实人脸素材", len(imgs) >= 12, f"{len(imgs)} 张 @ {samples}")

print("  => M0 " + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
'''.strip()),
                expect=["全部通过"],
            ),
        ],
    ),
    Stage(
        code="M1",
        name="单元测试",
        goal="34 项单测全绿 —— 改阈值、改逻辑后回归的第一道关",
        steps=[
            Step(
                title="pytest 全量",
                argv=_py("-m", "pytest", "test/", "-q"),
                expect=["passed"],
            ),
        ],
    ),
    Stage(
        code="M2",
        name="画质门控标定",
        goal="7 条画质规则的阈值是实测出来的，不是抄的 —— 标定脚本可复现",
        steps=[
            Step(
                title="画质门控阈值标定（真实推理，耗时）",
                argv=_py("scripts/calibrate_quality.py"),
                slow=True,
                expect=["G3", "合格"],
            ),
        ],
    ),
    Stage(
        code="M3",
        name="播种演示数据",
        goal="28 名学生 + 真实人脸录入（走完整 enroll_face 流水线，含画质门控与查重）",
        steps=[
            Step(
                title="seed_demo 播种（需先停后端）",
                argv=_py(
                    "scripts/seed_demo.py", "--drop", "--students", "28",
                    "--faces-from", "scripts/sample_faces", "--max-enroll", "20",
                ),
                need_backend="stopped",
                expect=["录入", "学生"],
            ),
        ],
    ),
    Stage(
        code="M4",
        name="端到端联调",
        goal="68 项断言覆盖全部接口与签到闭环 —— 需后端在跑",
        steps=[
            Step(
                title="启动后端",
                argv=["__START_BACKEND__"],
                need_backend="running",
                expect=["Application startup complete"],
            ),
            Step(
                title="端到端联调（68 项断言）",
                argv=_py("scripts/e2e_test.py"),
                need_backend="running",
                expect=["通过", "PASS", "失败"],
            ),
            Step(
                title="API 压测（50 并发）",
                argv=_py("scripts/bench_api.py", "--concurrency", "50"),
                need_backend="running",
                expect=["P95"],
            ),
            Step(
                title="SQLite 并发写对照（默认 vs WAL）",
                argv=_py("scripts/bench_sqlite_write.py"),
                slow=True,
                expect=["P95"],
            ),
        ],
    ),
    Stage(
        code="M5",
        name="人脸识别演示",
        goal="真实推理跑通识别闭环，并验证一人多号能被识别为歧义（M5 的硬门槛）",
        steps=[
            Step(
                title="识别闭环演示（隔离环境，不污染演示库）",
                argv=_py("tools/demo_recognition.py"),
                expect=["识别", "decision"],
            ),
        ],
    ),
    Stage(
        code="M6",
        name="阈值标定",
        goal="审计阈值与签到阈值都是实测标定的 —— 审计 0.40/0.60，签到 0.56/0.27",
        steps=[
            Step(
                title="审计阈值 sweep + 留一法（独立评测库，耗时）",
                argv=_py("scripts/audit_threshold_sweep.py"),
                slow=True,
                expect=["CROSS_STUDENT", "阈值"],
            ),
            Step(
                title="签到阈值 sweep（独立评测库，耗时）",
                argv=_py("scripts/checkin_threshold_sweep.py"),
                slow=True,
                expect=["CHECKIN_THRESHOLD", "MARGIN_WARN"],
            ),
        ],
    ),
]

STAGE_BY_CODE = {s.code: s for s in STAGES}


# ────────────────────────────────────────────────────────────────────
# 后端进程管理
# ────────────────────────────────────────────────────────────────────
def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def health_ok(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def find_backend_pids() -> list[int]:
    """找出占用 8000 端口的进程 PID（Windows 走 netstat，其它走 lsof 兜底）。"""
    pids: list[int] = []
    if IS_WIN:
        try:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=15
            ).stdout
            for line in out.splitlines():
                if f":{BACKEND_PORT}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.append(int(parts[-1]))
        except Exception:
            pass
    else:
        try:
            out = subprocess.run(
                ["lsof", "-ti", f":{BACKEND_PORT}"], capture_output=True, text=True, timeout=15
            ).stdout
            pids = [int(x) for x in out.split() if x.isdigit()]
        except Exception:
            pass
    # 去重且排除自己
    me = os.getpid()
    return sorted({p for p in pids if p != me})


def project_backend_running(timeout: float = 2.0) -> bool:
    """确认 8000 端口上跑的是**本项目**后端，而不是用户的其它服务。

    判据用 /api/system/status 返回体里的 `app` 标识（"学生刷脸考勤系统"）——
    /api/health 太通用（任何服务都能返回 200），不足以用来认领端口。
    """
    try:
        with urllib.request.urlopen(
            f"{BACKEND_URL}/api/system/status", timeout=timeout
        ) as r:
            body = r.read().decode("utf-8", "replace")
        return PROJECT_APP_MARK in body
    except Exception:
        return False


def stop_backend(verbose: bool = True) -> bool:
    """停掉占用 8000 的**本项目**后端进程（Milvus Lite 单进程锁的前提）。

    ⚠️ 只杀认领得动端口的进程：先确认 8000 上跑的确实是本项目后端，
    否则**不动它** —— 误杀用户自己挂在 8000 上的其它服务是不可接受的后果。
    返回是否真的停了。
    """
    if not project_backend_running():
        if verbose:
            print(f"     {BACKEND_PORT} 端口上的服务不是本项目后端（或未在运行），不动它")
        return False

    pids = find_backend_pids()
    if not pids:
        if verbose:
            print("     后端未在运行，无需停止")
        return False
    for pid in pids:
        if verbose:
            print(f"     停止占用 {BACKEND_PORT} 的进程 PID={pid}")
        try:
            if IS_WIN:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=30,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            print(f"     WARN 停止 PID={pid} 失败：{e}")
    time.sleep(1.5)
    if port_open(BACKEND_HOST, BACKEND_PORT):
        print(f"     WARN 端口 {BACKEND_PORT} 仍被占用，后续阶段可能失败")
        return False
    return True


class BackendProc:
    """本脚本拉起的后端进程（上下文管理器式使用）。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log = BACKEND / ".eval" / "run_all_m0_m6_backend.log"

    def start(self, verbose: bool = True) -> None:
        if health_ok():
            if verbose:
                print("     检测到后端已在运行，复用之（不新起进程）")
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self._fh = open(self.log, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "main:app",
             "--host", BACKEND_HOST, "--port", str(BACKEND_PORT)],
            cwd=str(BACKEND), stdout=self._fh, stderr=subprocess.STDOUT, env=env,
        )
        # 等健康检查通过（最多 90 秒：首次要加载模型）
        for _ in range(90):
            if health_ok():
                if verbose:
                    print(f"     后端已就绪 {BACKEND_URL}（日志 {self.log}）")
                return
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"后端进程提前退出（退出码 {self.proc.returncode}），日志：{self.log}"
                )
            time.sleep(1)
        raise RuntimeError(f"后端 90 秒内未就绪，日志：{self.log}")

    def stop(self, verbose: bool = True) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        try:
            self._fh.close()
        except Exception:
            pass
        self.proc = None
        if verbose:
            print("     已停止本脚本启动的后端")


# ────────────────────────────────────────────────────────────────────
# 执行
# ────────────────────────────────────────────────────────────────────
class Runner:
    def __init__(self, dry_run: bool = False, skip_slow: bool = False) -> None:
        self.dry_run = dry_run
        self.skip_slow = skip_slow
        self.backend = BackendProc()
        self.backend_started_by_us = False
        self.results: list[tuple[str, str, bool, float, str]] = []

    # -- 后端状态协调 ------------------------------------------------
    def ensure_backend(self, need: str) -> None:
        """按步骤要求协调后端状态。"""
        if need == "any":
            return
        if need == "running":
            if health_ok():
                # 端口上有服务且 /api/health 返回 200 —— 还需确认是本项目的
                if project_backend_running():
                    return
                raise RuntimeError(
                    f"端口 {BACKEND_PORT} 上的服务虽然返回 200，但不是本项目后端，"
                    f"拒绝复用（否则后续接口断言会给出误导性结论）。"
                )
            # 端口被占但健康检查不通 —— 可能是用户的其它服务在监听
            if port_open(BACKEND_HOST, BACKEND_PORT) and not project_backend_running():
                raise RuntimeError(
                    f"端口 {BACKEND_PORT} 已被其它服务占用（不是本项目后端），"
                    f"本项目无法启动。请先释放该端口，或改用别的端口。"
                )
            self.backend.start()
            self.backend_started_by_us = True
            return
        if need == "stopped":
            # 先停我们自己起的，再停外部占用的（仅限确认是本项目的）
            if self.backend_started_by_us:
                self.backend.stop()
                self.backend_started_by_us = False
            if port_open(BACKEND_HOST, BACKEND_PORT):
                if not stop_backend():
                    raise RuntimeError(
                        f"端口 {BACKEND_PORT} 被非本项目服务占用，无法释放。"
                        f"请手动处理后重跑。"
                    )

    def cleanup(self) -> None:
        if self.backend_started_by_us:
            self.backend.stop()

    # -- 单步 -------------------------------------------------------
    def run_step(self, stage: Stage, step: Step) -> tuple[bool, float, str]:
        label = f"[{stage.code}] {step.title}"

        # 哨兵步骤：不是真命令，只表示"到这一步请确保后端在跑"
        if step.argv and step.argv[0] == "__START_BACKEND__":
            print(f"\n▶ {label}")
            t0 = time.time()
            try:
                self.ensure_backend("running")
            except Exception as e:
                print(f"   → 失败：{e}")
                return False, time.time() - t0, str(e)
            elapsed = time.time() - t0
            if not health_ok():
                print("   → 失败：后端未通过健康检查")
                return False, elapsed, "健康检查未通过"
            print(f"   → 通过（{elapsed:.1f}s）  后端就绪 {BACKEND_URL}")
            return True, elapsed, "后端就绪"

        if self.dry_run:
            print(f"\n▶ {label}")
            if step.argv and step.argv[0] == "__START_BACKEND__":
                print("   DRY-RUN  确保后端在跑")
            else:
                print("   DRY-RUN  " + " ".join(step.argv))
            return True, 0.0, "dry-run"

        print(f"\n▶ {label}")
        self.ensure_backend(step.need_backend)

        t0 = time.time()
        try:
            proc = subprocess.run(
                step.argv,
                cwd=str(step.cwd or BACKEND),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3600,
            )
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            out = "（超时：超过 3600 秒）"
            rc = -1
        except FileNotFoundError as e:
            out = f"（找不到可执行文件：{e}）"
            rc = -1
        elapsed = time.time() - t0

        # 输出留档，便于事后复核
        try:
            logdir = BACKEND / ".eval" / "run_all_m0_m6_logs"
            logdir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w.-]+", "_", step.title)[:40]
            (logdir / f"{stage.code}_{safe}.log").write_text(out, encoding="utf-8")
        except Exception:
            pass

        # 判定
        passed = rc == 0
        detail = ""
        if step.expect:
            hit = [e for e in step.expect if e in out]
            if not hit:
                passed = False
                detail = f"未在输出里找到期望标志 {step.expect}"
            else:
                detail = "命中标志 " + "/".join(hit)
        if not passed and not detail:
            detail = f"退出码 {rc}"
        if rc != 0 and "未在输出里找到" not in detail:
            detail = f"退出码 {rc}"

        # 打印输出尾部（有价值的信息通常在末尾）
        tail = out.strip().splitlines()[-25:]
        for line in tail:
            print("   " + line)
        print(f"   → {'通过' if passed else '失败'}（{elapsed:.1f}s）{('  ' + detail) if detail else ''}")
        return passed, elapsed, detail

    # -- 阶段 -------------------------------------------------------
    def run_stage(self, stage: Stage) -> bool:
        print("\n" + "=" * 78)
        print(f"{stage.code} · {stage.name}")
        print(f"目标：{stage.goal}")
        print("=" * 78)

        steps = stage.steps
        if self.skip_slow:
            kept = [s for s in steps if not s.slow]
            if len(kept) != len(steps):
                skipped = [s.title for s in steps if s.slow]
                print(f"   （--skip-slow：跳过 {len(skipped)} 个耗时步骤：{'、'.join(skipped)}）")
            steps = kept

        for step in steps:
            ok, elapsed, detail = self.run_step(stage, step)
            self.results.append((stage.code, step.title, ok, elapsed, detail))
            if not ok:
                print(f"\n!! {stage.code} 失败，按下不表继续（后续阶段的结论会不可信）")
                return False
        return True


def print_summary(runner: Runner) -> None:
    print("\n" + "=" * 78)
    print("跑批汇总")
    print("=" * 78)
    total_ok = total = 0
    total_time = 0.0
    for code, title, ok, elapsed, detail in runner.results:
        total += 1
        total_ok += 1 if ok else 0
        total_time += elapsed
        print(f"  {'PASS' if ok else 'FAIL'}  {code}  {title:<44} {elapsed:>7.1f}s  {detail}")
    print("-" * 78)
    print(f"  合计 {total_ok}/{total} 个步骤通过，总耗时 {total_time:.1f}s")
    if total_ok == total and total:
        print("  ★ 全链路通过")
    else:
        print("  ★ 有步骤失败 —— 见上表 FAIL 行")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="M0→M6 全链路跑批",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "stages", nargs="*",
        help="要跑的阶段，如 M0,M5,M6（不填则全跑）",
    )
    ap.add_argument("--from", dest="from_", help="从该阶段开始跑（如 M3）")
    ap.add_argument("--to", dest="to", help="跑到该阶段为止（如 M3）")
    ap.add_argument("--dry-run", action="store_true", help="只打印各阶段要干什么，不真跑")
    ap.add_argument("--skip-slow", action="store_true", help="跳过耗时步骤（M2/M6 的标定与压测）")
    args = ap.parse_args()

    # 选阶段（支持 "M0,M5,M6" 这种逗号连写，也支持空格分开的多个位置参数）
    raw: list[str] = []
    for item in args.stages:
        raw.extend(p for p in item.replace(",", " ").split() if p)
    if raw:
        wanted = [s.strip().upper() for s in raw]
        unknown = [c for c in wanted if c not in STAGE_BY_CODE]
        if unknown:
            print(f"未知阶段：{unknown}；可用：{list(STAGE_BY_CODE)}")
            return 2
        selected = [STAGE_BY_CODE[c] for c in wanted]
    else:
        codes = [s.code for s in STAGES]
        start = codes.index(args.from_.upper()) if args.from_ and args.from_.upper() in codes else 0
        end = codes.index(args.to.upper()) + 1 if args.to and args.to.upper() in codes else len(codes)
        selected = STAGES[start:end]

    print("=" * 78)
    print("M0 → M6 全链路跑批")
    print("=" * 78)
    print(f"仓库：{ROOT}")
    print(f"解释器：{VENV_PY}" + ("" if VENV_PY.exists() else "  ← 不存在！"))
    print("计划执行：")
    for st in selected:
        need = sorted({s.need_backend for s in st.steps} - {"any"})
        print(f"  {st.code}  {st.name}"
              + (f"   [后端：{'/'.join(need)}]" if need else "   [后端：不限]"))

    if not VENV_PY.exists():
        print(f"\n找不到 venv 解释器：{VENV_PY}")
        print("请先按 README「三、启动方式 → 首次准备」建好虚拟环境并装依赖。")
        return 2

    runner = Runner(dry_run=args.dry_run, skip_slow=args.skip_slow)
    t0 = time.time()
    try:
        for st in selected:
            if not runner.run_stage(st):
                break
    except KeyboardInterrupt:
        print("\n（用户中断）")
    finally:
        runner.cleanup()

    if not args.dry_run:
        print_summary(runner)
        print(f"\n总耗时 {time.time() - t0:.1f}s")
    return 0 if all(ok for _, _, ok, _, _ in runner.results) else 1


if __name__ == "__main__":
    sys.exit(main())
