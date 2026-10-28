#!/bin/sh
# 容器入口：确保人脸模型就位后启动服务。
#
# 为什么不把模型烤进镜像：buffalo_l 约 600MB，且仓库 .gitignore 排除它
# （可用 scripts/download_model.py 重建）。放进镜像会让每次改代码都重传 600MB，
# 放进 named volume 则可以跨容器复用 —— 第二次起容器是秒级的。
#
# 流程：检查 ~/.insightface/models/buffalo_l 是否存在 → 不在就下载（多镜像重试）→ exec 启动。
set -eu

MODEL_DIR="${HOME:-/root}/.insightface/models/buffalo_l"

# 业务数据目录：docker-compose 里把命名卷挂在这里，
# SQLITE_URL / MILVUS_DB_PATH 两个环境变量也指向它（见 backend/core/config.py）。
# 首次起来卷是空的，SQLAlchemy 不会代建父目录，所以在这里先 mkdir；
# 写不进去就当场失败，好过启动后再报 "unable to open database file"。
STATE_DIR="${STATE_DIR:-/app/backend/state}"
mkdir -p "$STATE_DIR"
echo "[entrypoint] 数据目录：$STATE_DIR"

if [ ! -d "$MODEL_DIR" ]; then
    echo "[entrypoint] 未检测到模型，开始下载 buffalo_l（约 600MB，仅首次）..."
    python /app/backend/scripts/download_model.py
else
    echo "[entrypoint] 命中模型缓存：$MODEL_DIR"
fi

exec "$@"
