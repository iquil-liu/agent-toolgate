# 接入到 AI 客户端（MCP）

本仓库的 `src/mcp_server.py` 是 MCP 风格的 JSON-RPC over stdio 服务端，支持 `initialize`、`tools/list`、`tools/call` 三个方法，工具为 `kb_search`、`echo`、`clock`。

> **状态标注：以下客户端接入均为配置样例，尚未在本机实测通过**。仓库的自动化验证覆盖到"独立客户端进程握手 + 工具调用"这一层（`scripts/mcp_client_probe.py`），真实桌面客户端（Claude Desktop / Cursor）的接入属于待人工验证项。若接入时出现兼容问题，请在 Issue 中记录具体不兼容点，不要据此宣称已支持完整 MCP 协议。

## 启动脚本

客户端通常不保证工作目录，因此统一用仓库自带的启动脚本，它自己会 `cd` 到仓库根：

```sh
chmod +x scripts/mcp_server.sh     # 首次执行一次
/绝对路径/agent-toolgate/scripts/mcp_server.sh
```

## Claude Desktop

配置文件：`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "agent-toolgate": {
      "command": "/Users/you/projects/agent-toolgate/scripts/mcp_server.sh"
    }
  }
}
```

改完完全退出并重新打开 Claude Desktop，然后在对话里输入下面这句验证：

> 用 kb_search 查「混合检索 重排」的前三条，并说明每条为什么排在这个位置。

预期：客户端发起 `tools/call`，服务端返回三条结果，每条带 `score_detail`（`fts_rank`、`vector_rank`、`cosine`、`rrf`、`title_boost`、`coverage_boost`）。

## Cursor

配置文件：项目内 `.cursor/mcp.json`，或全局 `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "agent-toolgate": {
      "command": "/Users/you/projects/agent-toolgate/scripts/mcp_server.sh"
    }
  }
}
```

保存后在 Cursor 的 MCP 列表中确认 `agent-toolgate` 处于可用状态，再在对话里发同一句验证提示。

## 手工验证服务端是否正常

在接入客户端之前，先用命令行确认服务端本身可用：

```sh
python3 -m src.mcp_server --demo
```

输出应包含 `initialize`、`tools/list`（3 个工具）、`echo` 与 `kb_search` 的 `tools/call` 结果。任意一步缺失，先修服务端，不要怀疑客户端配置。

## 已知边界

- 仅实现三个方法，不含资源（resources）、提示（prompts）、通知订阅等完整协议能力。
- 单进程阻塞式 stdio，未做并发与超时取消。
- 索引为单机 SQLite，首次调用前需 `python3 -m src.kb.cli rebuild`。
