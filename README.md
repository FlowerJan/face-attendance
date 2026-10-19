# 学生刷脸考勤系统 — 第一阶段

FastAPI + Vue 3 全栈实现，覆盖需求文档第一阶段的四大模块：
学生管理、人脸录入、信息汇总、公告推送，并额外完成 **脸库审计（非监督聚类）** 的 ML 实践。

> **一句话**：录入一道脸 512 维 ArcFace 特征 → SQLite 存业务元数据 + Milvus Lite 存向量 →
> 既能实时查重，又能用层次聚类离线审计出「一人多号 / 录错人」。端到端联调 **42/42 通过**。

## 界面预览

| 学生管理 | 信息汇总 |
|---|---|
| ![学生管理](docs/screenshots/01-students.png) | ![信息汇总](docs/screenshots/02-stats.png) |
| 人脸录入 | 脸库审计（ML） |
| ![人脸录入](docs/screenshots/04-enroll.png) | ![脸库审计](docs/screenshots/03-audit.png) |

![公告管理](docs/screenshots/05-notice.png)

## 项目亮点（三条）

1. **三处强一致的双写与级联删除**：一次人脸录入要同时写磁盘原图、SQLite 元数据、Milvus 向量；
   删除学生时三处级联回收（含孤儿脸照），端到端用例已覆盖并在 Milvus Lite 上实测通过。
2. **阈值是实测标定的，不是抄来的**：网上资料常见的 0.8 阈值对已归一化的 ArcFace 向量**永远查不出问题**；
   本项目拉了真实的相似度矩阵，测出「同人 0.45~0.65 / 异人 -0.1~0.15」，据此把边界定在 0.4~0.5（详见第六节）。
3. **零外部服务依赖就能跑**：Milvus Lite 以本地文件形式提供向量检索（HNSW + COSINE），
   搭配 SQLite，整机不需要装任何数据库服务即可启动演示。

---

## 一、当前状态（已验证）

| 项目 | 状态 |
|------|------|
| 后端接口 | ✅ 已实现并通过端到端联调 **42/42 项** |
| 人脸识别模型 | ✅ buffalo_l (ArcFace) 已下载并加载成功 |
| 向量库 | ✅ Milvus Lite 在 Windows 实测可用（HNSW + COSINE） |
| 前端 | ✅ `npm run build` 通过（2245 模块），dev server 可访问 |
| 演示数据 | ✅ 40 名学生，其中 6 人已录入真实人脸 |

联调结果示例：

```
[1] 健康检查 / 系统状态 / 枚举        PASS
[2] 学生 CRUD + 学号唯一 + 枚举校验    PASS
[3] 人脸录入 + 无脸拒收               PASS
[4] 汇总统计 / 覆盖率 / 脸照分布       PASS
[5] 脸库审计（ML 聚类）               PASS
[6] 公告 CRUD + WebSocket 实时推送     PASS
[7] 级联删除（SQLite+Milvus+磁盘）     PASS
[8] 真实人脸入库 + 聚类审计            PASS

结果：通过 42 项，失败 0 项
```

---

## 二、目录结构

```
face-attendance/
├── start-backend.cmd          一键启动后端 (8000)
├── start-frontend.cmd         一键启动前端 (5173)
├── quiz.html                  知识点考核（14 题交互式）
│
├── backend/
│   ├── main.py                入口：路由装配 + lifespan + 统一异常处理
│   ├── requirements.txt
│   ├── attendance.db          SQLite（运行时生成）
│   ├── milvus_lite.db/        Milvus Lite 数据目录（运行时生成）
│   ├── uploads/faces/<学号>/  人脸原图（运行时生成）
│   ├── core/                  ★ 基础设施层
│   │   ├── config.py          Settings（路径/端口/维度/模型名）
│   │   ├── db.py              SQLAlchemy engine + PRAGMA foreign_keys=ON
│   │   ├── milvus.py          向量库客户端 + 集合管理 + load 保证
│   │   ├── face_engine.py     insightface 单例 + 检测/取特征
│   │   ├── enums.py           CSV 驱动的班级/专业枚举
│   │   ├── ws_manager.py      WebSocket 连接池（同步/异步双入口）
│   │   ├── response.py        统一响应包裹
│   │   └── exceptions.py
│   ├── models/                ORM 实体：student / face / notice
│   ├── schemas/               DTO：student / face / notice / common
│   ├── repository/            数据访问层：student_repo / face_repo / notice_repo
│   ├── service/               业务层
│   │   ├── student_service.py    CRUD + CSV 导入 + 三处级联删除
│   │   ├── face_service.py       录入流水线 + 实时查重
│   │   ├── stats_service.py      汇总统计
│   │   ├── cluster_service.py    ★ ML 脸库审计（层次聚类）
│   │   └── notice_service.py     公告 + WS 广播
│   ├── api/                   控制层：students / faces / stats / audit / notices / system / deps
│   ├── data/                  班级.csv、专业.csv、示例学生导入.csv
│   └── scripts/
│       ├── download_model.py   下载 buffalo_l（多镜像并发择优）
│       ├── prepare_samples.py  裁切真实人脸样本（离线、零成本）
│       ├── seed_demo.py        种子数据
│       └── e2e_test.py         端到端联调（42 项断言）
│
└── frontend/
    ├── vite.config.js         /api、/uploads、/ws 代理到 8000
    └── src/
        ├── api/               request(axios封装) / student / face / stats / audit / notice
        ├── components/        CameraCapture.vue（摄像头采集）
        ├── stores/            notice.js（pinia）
        ├── utils/             websocket.js（自动重连 + 补拉）
        └── views/             StudentList / FaceEnroll / StatsDashboard / FaceAudit / NoticeBoard
```

---

## 三、启动方式

### 首次准备（本机已完成，换机器才需要）

```bat
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\download_model.py
```

> `download_model.py` 会依次尝试 gh-proxy / ghfast / ghproxy / 官方源，自动挑最快的。
> 实测 gh-proxy.com 最快（约 30 秒下完 275MB）。

### 日常启动

```bat
start-backend.cmd
start-frontend.cmd
```

然后浏览器打开 **http://localhost:5173** ；接口文档在 **http://localhost:8000/docs** 。

> ⚠️ **Milvus Lite 是单进程文件锁**：同一个 `backend/milvus_lite.db` 只能被一个进程打开。
> 若旧后端没退干净，新进程会报 `DataDirLockedError / Open local milvus failed`。
> 解决办法：`netstat -ano | findstr :8000` 找到 PID 后 `taskkill /F /PID <pid>`。
> 另外 `scripts/seed_demo.py` 需要先停掉后端再运行。

---

## 四、接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 存活探针 |
| GET | `/api/system/status` | 向量后端/向量数/模型加载/WS 在线数 |
| GET | `/api/enums` | 班级 + 专业枚举 |
| GET | `/api/students` | 分页 + keyword + class_name 筛选 |
| POST/PUT/DELETE | `/api/students[/{id}]` | 增改删（含学号唯一、枚举校验） |
| POST | `/api/students/import` | CSV/Excel 批量导入 |
| POST | `/api/students/{id}/faces` | 人脸录入（multipart，返回 detected + 实时查重结果） |
| GET | `/api/students/{id}/faces` | 该学生脸照列表 |
| DELETE | `/api/faces/{face_id}` | 删除单张脸照 |
| GET | `/api/stats/summary` | 总量 + 按班级/性别/专业分布 |
| GET | `/api/stats/face-coverage` | 录入覆盖率 |
| GET | `/api/stats/face-count` | 0/1/多张脸照人数分布 + 孤儿脸照数 |
| GET | `/api/audit/duplicates` | **脸库审计**（阈值可调） |
| GET | `/api/audit/similarity-matrix` | **相似度矩阵**（用于标定阈值） |
| GET/POST/PUT/DELETE | `/api/notices[/{id}]` | 公告 CRUD |
| WS | `/ws/notices` | 公告实时推送（支持 ping/pong 心跳） |

统一响应格式：`{"code": 0, "message": "ok", "data": {...}}`，`code != 0` 即为业务失败；
参数校验失败返回 `{"code": 422, "message": "<字段>: <原因>", "data": {...}}`。

---

## 五、人脸录入流水线

```
摄像头/文件上传
  → cv2.imdecode 解码
  → insightface buffalo_l 检测人脸（取面积最大的 1 张）
  → 512 维 ArcFace 特征（已 L2 归一化）
  → ① 原图落盘 uploads/faces/<学号>/<ts>.jpg
  → ② SQLite 写元数据（face_id = 自增 id，与向量库 1:1）
  → ③ Milvus 写向量（HNSW / COSINE）
  → ④ 向量检索查重：与库中已有脸比对，超过阈值即返回 duplicate_suspect
  → ⑤ 更新学生 face_status
```

未检出人脸时返回 `code=0 + detected=false + reason`，前端弹友好提示而不触发全局错误。

---

## 六、ML 实践：脸库审计

**思路**（非监督，无需标注）：

```
Milvus 拉全部向量 → L2 归一化 → N×N 余弦相似度矩阵
  → linkage(average, cosine distance) + fcluster 层次聚类
  → 三类异常：
       ① 同一学号内相似度 < 0.40        → 脏数据（可能录错人）
       ② 不同学号间相似度 > 0.50        → 疑似一人多号
       ③ 同一聚类簇内混入多个学号        → 强证据的重复注册
```

### 阈值是实测标定的，不是拍的

ArcFace `normed_embedding` 的余弦相似度实测分布：

| 关系 | 相似度区间 |
|------|-----------|
| 同一人的不同照片 | **0.45 ~ 0.65** |
| 不同人 | **-0.1 ~ 0.15** |

所以判定边界取 **0.4~0.5**。常见资料里写的 0.8+ 是针对未归一化向量或别的模型，
直接套用会导致**审计永远查不出任何问题**（本项目初版就踩了这个坑，实测矩阵才暴露出来）。

当前演示数据（3 张同一人照片误录到 3 个学号）的审计结果：

```json
{
  "cluster_count": 4,
  "duplicate_pairs": 3,
  "dirty_pairs": 0,
  "suspicious_cluster_count": 1,
  "students_involved": 3
}
```

调参方式（无需改代码）：

```
GET /api/audit/similarity-matrix              # 先看自己数据的真实分布
GET /api/audit/duplicates?cross_student=0.45  # 再定阈值
```

---

## 六·补：审计效果怎么验的（实测数据）

「能查重」这种话谁都会说，所以补了一个可复现的评测脚本：`backend/scripts/audit_recall_test.py`。

**它是怎么保证结论可信的：**

- 跑在**独立评测库** `backend/.eval/` 上（用环境变量改写 `SQLITE_URL` / `MILVUS_DB_PATH` / `FACE_IMG_DIR`），
  既不碰演示数据，也不和正在运行的后端抢 Milvus Lite 的单进程文件锁；
- 先用 ArcFace 特征把素材**自动聚成身份**（余弦相似度 > 0.45 判为同一人），再按
  **「一个身份只进一个组」** 造真值——否则两组里是同一个人，审计判他们重复其实是判对了，却被算成误报；
- 负样本入库前先校验两两相似度 < 0.40，确认彼此独立后才计入。

**实测结果**（完整报告见 `docs/audit-recall-report.md`）：

| 指标 | 结果 |
|---|---|
| 构造的异常组 | 4（1 组真实同人照片 + 3 组翻转／提亮增广） |
| 命中 | 4 |
| **召回率** | **100%** |
| 误报的跨组配对数 | 0 |
| 负样本（互不相干的人） | 3 个，被误报 0 个 |

组内相似度实测：**真实同人照片 0.565**、水平翻转增广 0.955、提亮增广 0.886
（前者再次印证第六节那句「同一人的不同照片落在 0.45~0.65」）。

> 素材限制要如实说：本机可检出人脸的真实素材有限，同一人的多张照片只有 3 张可用，
> 其余身份来自 insightface 自带合影测试图。所以这是**小样本的正确性验证**，不是大规模基准测试。

## 七、技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | FastAPI 0.136 + Uvicorn |
| ORM | SQLAlchemy 2.0（Mapped 声明式）+ SQLite |
| 向量库 | Milvus Lite 2.5（HNSW + COSINE，512 维） |
| 人脸识别 | insightface 1.0.1 (buffalo_l / ArcFace) + onnxruntime |
| ML | numpy 相似度矩阵 + scipy 层次聚类 |
| 前端 | Vue 3.5 + Vite 6 + Element Plus + ECharts 5 + Pinia |
| 实时通信 | WebSocket（服务端连接池 + 前端断线重连） |

---

## 八、验证与演示脚本

```bat
cd backend

REM 端到端联调（需后端已启动）：42 项断言，覆盖全部阶段一接口
.venv\Scripts\python.exe scripts\e2e_test.py

REM 生成演示数据（需先停后端）：40 名学生 + 真实人脸录入
.venv\Scripts\python.exe scripts\seed_demo.py --drop --students 40 ^
    --faces-from scripts/sample_faces --max-enroll 20

REM 重新生成真实人脸样本（离线，无需联网）
.venv\Scripts\python.exe scripts\prepare_samples.py
```

`scripts/sample_faces/` 内的样本由 `prepare_samples.py` 从 insightface 自带测试图
与本机已有图片裁切而来，**不依赖网络、不产生额外费用**。

---

## 八·补：技术笔记

- [ArcFace 相似度阈值别再抄 0.8 了：我用一次实测把它改成 0.45](docs/blog/arcface-threshold-0.45.md)
  —— 记录阈值从"抄资料"到"实测标定"的完整过程，含评测脚本设计与踩坑复盘

---

## 九、已知边界（第一阶段范围外）

- 未做登录鉴权（需求文档第一阶段未要求）
- 未做真实课堂考勤批次/课程表（属第二阶段）
- 摄像头采集依赖浏览器 `getUserMedia`，需 `localhost` 或 HTTPS 环境
- 向量规模为万级以内；更大规模建议换成独立部署的 Milvus 服务
