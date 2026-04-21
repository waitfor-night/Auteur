#!/bin/bash
# 定时触发视频流水线：获取热点 → 生成视频 → 发布（每小时最多 MAX_PER_RUN 条）
# 用法：bash utils/run_pipeline_cron.sh <username> [max_per_run]
# crontab 示例（每小时触发，每次最多 5 条）：
#   0 * * * * cd /home/shuyun/work/multi-shot-multi-object-long-video-edit && bash utils/run_pipeline_cron.sh <username> 5 >> workspace/<username>/pipeline_cron.log 2>&1

set -e
USERNAME="${1:?用法: $0 <username> [max_per_run]}"
MAX_PER_RUN="${2:-3}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 锁文件防并发
LOCK="$PROJECT_DIR/workspace/$USERNAME/pipeline.lock"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[$(date)] 已有流水线在运行（lock: $LOCK），跳过本次触发" >&2
    exit 0
fi

echo "[$(date)] 启动视频流水线 username=$USERNAME max_per_run=$MAX_PER_RUN"

# 先刷新热点状态（2天内有缓存则跳过）
python3 "$PROJECT_DIR/utils/hot_topics_cron.py" --username "$USERNAME"

# 用 claude CLI 以 Agent 模式跑完整流水线
# --print 非交互式，跑完退出；video-pipeline skill 内部会循环处理所有 unused 话题
cd "$PROJECT_DIR"
claude \
    --print \
    --dangerously-skip-permissions \
    "/video-pipeline --username $USERNAME --max $MAX_PER_RUN"

echo "[$(date)] 流水线本轮结束"

# 检测发布日志中缺失的平台，自动重试（成功才写入日志）
echo "[$(date)] 开始检测并重试缺失平台发布..."
python3 "$PROJECT_DIR/utils/publish_record_v2.py" retry-publish --username "$USERNAME" || true

echo "[$(date)] 本轮全部完成"
