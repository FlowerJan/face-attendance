# =============================================================================
# 学生刷脸考勤系统 —— 后端服务镜像
#
# ⚠️ 构建状态：本文件由仓库作者编写，但**编写环境没有安装 Docker，尚未实机 docker build 过**
#    （2026-10-26 再次尝试：本机无 docker 引擎，winget 也未安装 Docker Desktop，
#     Windows 的 Hyper-V / VirtualMachinePlatform 特性均为 Disabled）。
#    首次构建请在能跑 Docker 的机器上执行；若失败，请把报错发回修正（不要直接对外声称"一键可用"）。
#    已做的静态核对见 `docs/VERIFY.md`（修掉了 .venv 泄漏与数据卷挂错路径两个问题）。
#
# 构建（在仓库根目录，注意上下文是 root 不是 backend/）：
#     docker build -t face-attendance-api:latest .
# 运行（state 卷要一起给，否则 SQLite / Milvus-Lite 会落在容器层里，删容器即丢）：
#     docker volume create face-models
#     docker volume create face-state
#     docker run --rm -p 8000:8000 \
#         -v face-models:/root/.insightface \
#         -v face-state:/app/backend/state \
#         -e SQLITE_URL=sqlite:////app/backend/state/attendance.db \
#         -e MILVUS_DB_PATH=/app/backend/state/milvus_lite.db \
#         face-attendance-api:latest
#
# （更省事的做法是直接用 `docker compose up -d`，卷和环境变量都已经配好。）
#
# 三个刻意的设计取舍：
#   1) 只打包后端。前端是 Vite 静态站，走 `npm run build` + nginx 是另一条链路，
#      混进同一个镜像会把关注点搅在一起，而且前端 keep贴近开发工作流更省事。
#   2) 模型不烤进镜像。buffalo_l 约 600MB，且不在仓库里（.gitignore 排除），
#      烤进去会让镜像巨大且每次改代码都要重传。改为首次启动时下载 + 挂卷缓存，
#      第二次起容器直接命中缓存，秒起。
#   3) 编译依赖用完即删。insightface 的 PyPI 包是源码分发（含 Cython/C++ 扩展），
#      python:3.11-slim 没有编译器，所以装 build-essential，装完再 purge，
#      避免把 300MB 工具链留在运行时层。
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ---- 1) 系统依赖 ----
# libgl1 / libglib2.0-0：opencv-python 在 Linux 上是带 GUI 后端的 wheel，缺 GL 库会 ImportError
# libgomp1                 ：onnxruntime CPU 推理需要 OpenMP 运行时
# curl                     ：容器健康检查用
# build-essential          ：仅为编译 insightface，下一步装完就删
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---- 2) Python 依赖（单独一层，改代码不会重装依赖） ----
COPY backend/requirements.txt ./backend/requirements.txt
# setuptools / wheel 一起升：insightface 是源码分发（含 Cython 扩展），
# slim 镜像自带的旧打包工具在构建 sdist 时不够稳，这是低成本保险。
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r backend/requirements.txt

# insightface 装完后编译工具链不再需要，从运行时层移除以压缩体积
RUN apt-get purge -y --auto-remove build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---- 3) 应用代码 ----
COPY backend ./backend
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app/backend

ENV PYTHONPATH=/app/backend
EXPOSE 8000

# 首次启动要下 600MB 模型，90s 起步宽限期不够，会被判 unhealthy（服务其实已经可用），
# 所以放宽到 300s。
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# state：SQLite + Milvus-Lite 的落盘位置（见 docker-compose.yml 里的环境变量说明）
VOLUME ["/root/.insightface", "/app/backend/uploads", "/app/backend/state"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
