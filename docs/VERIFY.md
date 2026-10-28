# 数字自检清单（VERIFY）

## 为什么有这个文件

这个项目里有几个数字会同时出现在**五个地方**：

| 出处 | 能不能自动同步 |
|---|---|
| 简历（`论文选题/简历-投递版.md`、仓库 `RESUME.md`） | 手动 |
| `README.md` | 手动 |
| 论文（表 6-7、1.3、6.6、7.1） | 手动 |
| 掘金文章（已发布，改不了正文只能加更正版） | **发布后不可改** |
| **Gitee 仓库简介** | **不在仓库文件里**，只能登录 Gitee 仓库设置改 |

改代码时动了一个数，其余四处立刻变成谎言。而且"我记得是 68"这种记忆不可靠——
`e2e_test.py` 里 `check(` 静态出现 **70 次**，实际运行是 **68 项**，静态数和运行数本来就不相等。

**所以：只有跑出来的才是真相。** 本表记录每个数字的实测值、核验命令与原始输出，
改任何一个数之前，先照这张表过一遍。

---

## 实测值（2026-10-26 10:37–10:38 跑出）

| 数字 | 实测值 | 核验命令 | 原始输出 |
|---|---|---|---|
| 端到端断言 | **68 项通过，0 失败** | 先起后端，再 `cd backend && .venv\Scripts\python.exe scripts\e2e_test.py` | [`verify-log/e2e_2026-10-26.txt`](verify-log/e2e_2026-10-26.txt) |
| 单元测试 | **33 passed** | `cd backend && .venv\Scripts\python.exe -m pytest test/ -q` | [`verify-log/pytest_2026-10-26.txt`](verify-log/pytest_2026-10-26.txt) |
| 自动化合计 | **101 项**（33 + 68） | 上两行相加 | — |
| REST 接口 | 25 个 + 1 WebSocket | `grep -c "@router\.\(get\|post\|put\|delete\|patch\)" backend/api/*.py` | — |
| 审计阈值 | cross_student 0.40 / cluster_distance 0.60 | `backend/service/cluster_service.py` | — |
| 脏库审计召回 | 15/15（阈值 0.40）；默认阈值 0.80 时仅 1/15 | `scripts\audit_baseline_compare.py --scenario dirty` | `docs/verify-log/audit_baseline_dirty.json` |
| 正常库审计误报 | 0（0.80→0.30 全程为零） | `scripts\audit_baseline_compare.py --scenario normal` | `docs/verify-log/audit_baseline_normal.json` |
| 业务接口压测（读为主） | 50 并发 P95 119 ms / 304 QPS / 零错误 | `cd backend && .venv\Scripts\python.exe scripts\bench_api.py --concurrency 50` | `backend/scripts/bench_result.json` |
| SQLite 并发写（出厂默认） | 50 并发 P95 ≈ 2.2 s / ≈ 190 TPS | `cd backend && .venv\Scripts\python.exe scripts\bench_sqlite_write.py` | `docs/verify-log/bench_sqlite_write.json` |
| SQLite 并发写（开 WAL 后） | 50 并发 P95 ≈ 0.28 s / ≈ 420 TPS | 同上（脚本内自带 A/B 对照） | 同上 |

### 性能类数字的限定语（写简历前必读）

性能数字脱离硬件与数据量就是空话。以下三条是对外引用**必须同时带上**的限定语：

| 限定语 | 本项目实测情况 |
|---|---|
| **机器** | Intel i5-13500H（12 核 16 线程）/ 16 GB / Windows 11；服务与压测脚本同机（会互相抢 CPU） |
| **数据量** | 压测时库内仅 28 名学生 / 12 张人脸 / 3 条考勤（52 KB）。**不代表大数据量下的表现** |
| **读写比例** | A 组压测全是 GET；写路径单独一层（SQLite 并发写，见上两行） |

> 正确写法：本机、小数据量、读为主的业务接口，50 并发 P95 ≤ 120 ms。
> 错误写法：接口 P95 120 ms、300 QPS（少了任何一个限定语都可能被当场问穿）。

### 未经实机验证的东西（不要对外声称）

| 产物 | 状态 |
|---|---|
| `Dockerfile` / `docker-compose.yml` | **仍未实机 `docker build`**（2026-10-26 再次尝试：本机无 docker 引擎，Windows 的 Hyper-V / VirtualMachinePlatform 全为 Disabled，装 Docker 需要启用特性并重启）。已做的**静态核对**见 `docs/verify-log/docker_static_check_2026-10-26.txt`，据此修掉两个问题：① `.dockerignore` 漏 `backend/.venv`（上下文 671.9 MB → 7.1 MB）；② 数据卷挂在 `/app/backend/data`，而 SQLite/Milvus 实际落在 `/app/backend/*.db`，容器一删数据全丢，改为 `app-state` 卷 + `SQLITE_URL` / `MILVUS_DB_PATH` 环境变量重定向。<br>**剩余未验证项**：`insightface` 源码编译、opencv/onnxruntime 运行时库、模型源可达性 —— 必须真机跑通才能去掉这条标注 |

单测各文件明细（合计 33）：

| 文件 | 用例数 |
|---|---|
| `test_attendance_margin.py` | 9 |
| `test_cluster_service.py` | 8 |
| `test_enroll_rollback.py` | 6 |
| `test_m5_recognition.py` | 5 |
| `test_quality_gate.py` | 1 |
| `test_student_repo.py` | 4 |

---

## 口径约定（最容易算错的一条）

- **"N 项单元测试"一律指 pytest 用例数**，即 `pytest test/ -q` 输出的 `N passed`。
- `test_quality_gate.py` 内部有 19 条 `assert`，但它只是 **1 个用例**。
  **"19 项断言"与"25 项单元测试"不是同一口径，不能相加。**
- 端到端按运行时 `[PASS]` 计数，不要按源码里 `check(` 的出现次数数（70 ≠ 68）。

---

## 已知不能自动核验的地方

1. **Gitee 仓库简介**仍写着「端到端 60 项断言全通过」——
   这不是仓库文件内容，只能在 Gitee 仓库页 → 管理 → 仓库设置 → 简介 里手改。
2. **已发布的掘金文章**：正文数字发布后不可改，只能在文末加更正版说明。
   所以**发文章之前**先照本表核一遍数字。

---

## 归档输出里的长数字是什么

`verify-log/e2e_*.txt` 里会出现 13 位数字文件名，例如 `1792982256131.jpg`。
**它是 `int(time.time() * 1000)` 的毫秒时间戳，不是手机号**（对应 2026-10-26 10:37:36）。

用宽松正则 `1[3-9]\d{9}` 扫这类输出会误报——手机号是 11 位，而时间戳文件名是 13 位，
正则会匹配到它的前 11 位。看到告警先换算时间戳再判断，别直接当成泄露。
