#!/bin/zsh
# 手动启动 presence-server（按 Ctrl+C 停止）。日志同时写入 ~/Library/Logs/presence/
set -euo pipefail
cd "${0:A:h}/.."
mkdir -p ~/Library/Logs/presence
exec > >(tee -a ~/Library/Logs/presence/server.log) 2>&1
# 页面的 SSE 推送连接不会自己断开；关闭时最多等 1 秒，然后直接断开它们（数据照常保存）
exec uv run --locked uvicorn presence.main:app --host 127.0.0.1 --port "${PRESENCE_PORT:-8790}" --no-access-log \
  --timeout-graceful-shutdown 1
