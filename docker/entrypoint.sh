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

if [ ! -d "$MODEL_DIR" ]; then
    echo "[entrypoint] 未检测到模型，开始下载 buffalo_l（约 600MB，仅首次）..."
    python /app/backend/scripts/download_model.py
else
    echo "[entrypoint] 命中模型缓存：$MODEL_DIR"
fi

exec "$@"
