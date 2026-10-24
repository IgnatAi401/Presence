#!/bin/zsh
# 手动启动 presence-server（按 Ctrl+C 停止）。日志同时写入 ~/Library/Logs/presence/
set -euo pipefail
cd "${0:A:h}/.."
mkdir -p ~/Library/Logs/presence
exec > >(tee -a ~/Library/Logs/presence/server.log) 2>&1
exec uv run --locked uvicorn presence.main:app --host 127.0.0.1 --port "${PRESENCE_PORT:-8790}" --no-access-log
