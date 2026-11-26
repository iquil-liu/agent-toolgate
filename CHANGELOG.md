# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [0.1.0] - 2026-11-25

首个可用版本。

### 新增

- 工具注册与契约校验：JSON Schema 子集校验、字段级错误定位（`field` / `path`）、`deprecated` 标记
- MCP 风格 JSON-RPC 2.0 服务端：`initialize` 能力协商、`tools/list`、`tools/call`，stdio 逐行收发
- 知识库：Markdown 按标题切块，FTS5 字面检索 + 字符 n-gram 特征向量，RRF 融合与可解释重排
- 增量索引：按 `sha256` 判断变更，只重建改动文档，索引落本地 SQLite
- 评测：20 条人工标注查询，baseline 与 hybrid 同条件对比，输出 `eval/report.md` 与逐条证据
- 观测：调用审计（参数只存摘要不存原文）、token 用量估算、P50/P95 延迟与成本面板
- CLI：`demo` / `serve` / `search` / `rebuild` / `ingest` / `eval` / `dashboard`
- `ingest`：把自己的 Markdown 目录建成独立索引
- Makefile：`make demo`、`make test`、`make eval`、`make dashboard`、`make probe`
- 独立 MCP 客户端探针：子进程真实握手，断言结果与 CLI 一致

### 数据

- 语料 20 篇、80 块、索引 536,576 bytes
- baseline Recall@1/3/5 = 0.55 / 0.55 / 0.55，MRR 0.55
- hybrid Recall@1/3/5 = 0.80 / 0.90 / 0.95，MRR 0.8625
- 单元测试 62 条，`python3 -S` 下同样全过

### 已知限制

- 字符 n-gram 向量是词袋的轻量替代，不是神经嵌入，跨语言与语义泛化有限
- 未在 Claude Desktop / Cursor 等真实 MCP 客户端上人工验证；配置样例见 `MCP-CLIENTS.md`
- 成本数字是按字符数估算的代理值，不对应真实模型计费
