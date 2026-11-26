#!/bin/sh
# 供 MCP 客户端启动本仓库的服务端：不依赖客户端是否支持 cwd 字段。
set -eu
cd "$(dirname "$0")/.."
exec python3 -m src.mcp_server "$@"
