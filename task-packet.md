# 任务包：Agent-Toolgate —— 可演示的 Agent 编排与知识检索平台

## 一、背景与目标

产物用途：后端工程师的个人作品集，用于投递 AI Agent / LLM 工程化岗位。面试官看三样东西：能不能跑、有没有架构判断、有没有评测数据。

目标：在本仓库根目录交付一个可运行的仓库，一条命令跑通演示，一条命令产出评测报告。对标四层能力：

1. 后端底座：服务化、契约、错误码
2. Agent 编排：工具注册、调用审计、编排门禁
3. 知识层：切分、全文 + 向量混合检索、重排
4. 生产化：可观测、成本估算、评测

## 二、硬约束

1. Python 3.11+，**主路径零第三方依赖**，全部用标准库（sqlite3、json、hashlib）。可选增强写进 README「后续升级」，不进主路径。
2. **不得使用任何公司内部文件、规则、代码、语料**。语料必须自造，主题为通用技术文档，不含真实公司名、真实域名、真实凭据。
3. **不联网也必须跑通**。向量通道用本地确定性 embedding（字符 n-gram 哈希 + TF-IDF 权重），README 中标注为「离线基线，生产可替换为 bge-m3」。
4. 不 commit、不 push、不部署；只留本地文件，等确认。
5. 不派发、不转派任何其他 agent，全程本人执行。
6. 不许为通过验收而放宽验收标准；验收项不过就改实现。

## 三、目录结构（必须按此落地）

```
agent-toolgate/
├─ README.md
├─ task-packet.md
├─ docs/                      # 自造语料 20~30 篇 markdown
├─ corpus/manifest.json       # 语料清单与主题标签
├─ src/
│  ├─ __init__.py
│  ├─ registry.py
│  ├─ audit.py
│  ├─ mcp_server.py
│  ├─ kb/
│  │  ├─ __init__.py
│  │  ├─ splitter.py
│  │  ├─ embedder.py
│  │  ├─ index.py
│  │  └─ cli.py
│  └─ observability.py
├─ eval/
│  ├─ cases.jsonl
│  ├─ run_eval.py
│  └─ report.md
├─ tests/
│  ├─ test_registry.py
│  ├─ test_kb.py
│  └─ test_mcp.py
└─ out/
```

## 四、模块要求

### M1 工具注册与契约校验（`src/registry.py`）

- 工具声明字段：`name`、`version`、`description`、`input_schema`、`deprecated`、`handler`
- `input_schema` 支持 JSON Schema 子集：`type`(object/string/integer/number/boolean/array)、`required`、`properties`、`enum`、`minimum`、`maximum`、`pattern`、`maxLength`
- 校验失败返回结构化错误：`{"ok": false, "error": {"code": ..., "message": ..., "field": ..., "path": ...}}`
- 错误码覆盖：`UNKNOWN_TOOL`、`INVALID_ARGUMENT`、`SCHEMA_VIOLATION`、`TOOL_DEPRECATED`、`HANDLER_ERROR`
- 支持按名称查询、列出全部、按 deprecated 过滤
- 单测 ≥ 8 条：合法、缺必填、类型错、越界、pattern 不匹配、未知工具、deprecated 工具

### M2 MCP 风格服务端（`src/mcp_server.py`）

- JSON-RPC 2.0 over stdio，方法：`initialize`、`tools/list`、`tools/call`
- 错误码：`-32700` 解析错误、`-32601` 方法不存在、`-32602` 参数不合法
- 每次调用写审计 JSONL 到 `out/trace.jsonl`：`trace_id`、`tool`、`args_digest`（入参摘要，不落原文）、`status`、`duration_ms`、`error_code`
- `--demo` 模式：内置 3 个示例工具（`kb_search`、`echo`、`clock`），跑通一次完整调用链并打印结果
- 启动：`python3 -m src.mcp_server --demo`

### M3 知识库与混合检索（`src/kb/`）

- **切分**：按 markdown 标题层级切块，单块上限 600 字符，超长按段落二次切分，保留 `doc_id`、`heading_path`、`block_index`
- **FTS5**：sqlite3 内置，表 `blocks_fts`；查询前对 `/`、`:`、`"`、`(`、`)` 做 token 化与转义，**不得抛异常**
- **向量**：确定性 embedding —— 字符 2-gram + 3-gram 哈希到 256 维，TF-IDF 加权后 L2 归一化；余弦相似度纯 Python 计算
- **混合**：RRF（k=60）融合两路
- **重排**：标题命中 + 查询词项覆盖率，输出可解释 `score_detail`
- **增量**：按 `corpus/manifest.json` 的 mtime/hash，二次 rebuild 只处理变更文档
- **CLI**：`python3 -m src.kb.cli stats|search|rebuild`
- 单测 ≥ 8 条：切分边界、特殊字符查询、空结果、混合排序、特殊字符不崩

### M4 评测（`eval/`）

- `cases.jsonl` 20 条，字段：`query`、`expected_doc_ids`（1~3 个）、`note`
- 指标：Recall@1/3/5、MRR、平均延迟
- 两种模式：`--baseline`（仅 FTS）、`--hybrid`（FTS+向量+重排）
- 输出 `eval/report.md`：对比表 + 5 条结论（哪类查询混合占优、哪类持平、失败原因）
- **hybrid 的 Recall@5 ≥ 0.80，baseline 明显更低**，不达标就调切分或 RRF 参数直到达标

### M5 可观测与成本（`src/observability.py`）

- 读 `out/trace.jsonl`，输出：总调用数、成功率、错误码分布、P50/P95 延迟
- 成本估算：按 `prompt_tokens`、`completion_tokens` 与可配置单价（默认占位，README 注明可替换），输出总成本与单任务成本
- 生成 `out/dashboard.md`：指标表 + 结论

## 五、语料要求（`docs/`）

20~30 篇自造 markdown，每篇 300~800 字，标题含区分性关键词：

1. MCP 协议与工具注册
2. Agent 编排与 Workflow
3. 上下文管理与记忆
4. 幂等与重复投递
5. 失败重试与退避
6. 超时与取消
7. 工具契约校验
8. 权限与最小授权
9. 密钥管理与审计
10. 审计日志设计
11. 可观测性与 trace
12. 成本控制与配额
13. RAG 文档切分策略
14. 向量检索与 ANN
15. 混合检索与 RRF
16. 重排模型
17. 评测集构建
18. Recall 与 MRR 指标
19. 多 Agent 协作与交接
20. 冲突规则与单一真源

每篇必须有 `# 标题`、不少于 3 个二级标题、至少 5 个领域术语。**不得出现真实公司名、真实域名、真实账号密码。**

## 六、验收标准（逐项给证据，缺证据算未完成）

1. `python3 -m src.mcp_server --demo` 跑通，打印 `tools/list` 与一次 `tools/call` 结果
2. `python3 -m src.kb.cli stats` 输出文档数、块数、索引大小
3. `python3 -m src.kb.cli search "混合检索 重排"` 返回 Top3 且含 `score_detail`
4. `python3 -m unittest discover -s tests -v` 全绿，用例 ≥ 16
5. `--baseline` 与 `--hybrid` 均可跑，hybrid Recall@5 ≥ 0.80
6. `eval/report.md` 存在，含对比表与 5 条结论
7. `python3 -m src.observability` 生成 `out/dashboard.md`，含 P50/P95 与成本
8. `README.md` 含 Mermaid 架构图、快速开始、至少 3 条设计取舍
9. 主路径零第三方依赖也能跑

## 七、交付顺序

- **P1**：registry + mcp_server + audit + M1/M2 单测 → 自检 `--demo`
- **P2**：kb 全链路 + M3 单测 → 自检 `stats`、`search`
- **P3**：eval + observability + report + README → 跑全部验收项

每阶段自检完再进下一阶段，中途不汇报，全部完成后一次性返回。

## 八、最终返回格式

1. 表：验收项 | 命令 | 实际输出要点 | 通过/未通过
2. 关键设计取舍 3~5 条，每条一句话原因
3. 已知局限（如离线 embedding 的召回上限）
4. 下一步建议：生产化要替换什么

验收项未全部通过时不得写「已完成」。
