# 新契约断言更新后的九项验收

状态：9/9 通过。普通运行及 `python3 -S` 零第三方依赖复跑均为 **62/62 测试通过**，无 failure、error 或 skip。
仅按本轮明确授权修改 tests/test_mcp.py 中两个过时断言所在的方法；没有删除测试、放宽阈值或增加 skip。实现、检索参数、语料和评测用例保持原样。

| 验收项 | 命令 | 实际输出要点 | 通过/未通过 |
|---|---|---|---|
| 1 MCP 演示 | `python3 -m src.mcp_server --demo` | initialize、tools/list（3 工具）、echo 与真实 kb_search 调用成功；与 CLI 的 Top3 doc_id 序列和四个结果字段一致 | 通过 |
| 2 索引统计 | `python3 -m src.kb.cli stats` | 20 文档、80 块、536576 bytes | 通过 |
| 3 混合搜索 | `python3 -m src.kb.cli search "混合检索 重排"` | Top3 块均为 hybrid-rrf，含 score_detail，保持块级排序 | 通过 |
| 4 单元测试 | `python3 -m unittest discover -s tests -v` | Ran 62 tests；OK；62/62，无跳过 | 通过 |
| 5 双模式评测 | `python3 eval/run_eval.py --baseline`；`python3 eval/run_eval.py --hybrid` | baseline Recall@1/3/5=0.55/0.55/0.55，MRR=0.5500；hybrid=0.80/0.90/0.95，MRR=0.8625；Recall@5 ≥ 0.80 且高于 baseline | 通过 |
| 6 评测报告 | `python3 eval/run_eval.py`；读取 `eval/report.md` | 最新双模式对比表、5 条结论、逐条排名均存在；最终报告平均延迟 baseline=0.1792 ms、hybrid=5.5472 ms | 通过 |
| 7 观测与成本 | `python3 -m src.observability`；`python3 -S -m src.observability` | 最终面板 29 次调用，P50=0.014000 ms、P95=10.107600 ms；已知字符估算成本 0.005980 USD，独立公式复算一致；3 条历史记录缺用量如实保留 | 通过 |
| 8 README | 读取 `README.md` 并检查结构 | Mermaid、快速开始、5 条设计取舍均存在，文件未修改 | 通过 |
| 9 零第三方依赖 | `python3 -S` 运行 rebuild、demo、stats、search、unittest、baseline、hybrid、双模式报告、observability；AST 导入检查 | 9 条执行命令全部退出 0；测试仍为 62/62；导入仅标准库及本地 src/eval 模块 | 通过 |

## 两处断言及新增正向校验

1. `test_call_and_audit_privacy`：旧的六字段精确集合更新为新契约的九字段精确集合，新增 prompt_tokens、completion_tokens、usage_estimated，继续禁止其他多余字段；新增 `assertIs(record["usage_estimated"], True)`。原有摘要、隐私、状态、耗时和 trace_id 校验全部保留。
2. `test_demo_tools_are_callable`：移除旧占位 status=not_indexed 预期，改为真实查询「混合检索 重排」，断言调用成功且无占位 status；新增 Top3 数量和与真实 CLI 的 doc_id 序列精确相等断言。原有 clock 校验保留。

测试方法名称与数量全部保留；本轮不新增测试方法，把新增正向断言补入原两个方法，因此总数仍为 62。

## 证据与实际改动范围

- `out/acceptance/run-results.json`：本轮 17 条运行命令及退出码，均为 0。
- `01-demo.txt`、`01-search-parity.txt`、`02-stats.txt`、`03-search.txt`、`04-tests.txt`：普通运行证据。
- `05-baseline.txt`、`05-hybrid.txt`、`06-report.txt`、`06-report-structure.txt`：评测及报告结构证据。
- `07-observability.txt`：普通观测输出；`09-stdlib-09-07-observability.txt`：最终面板来源；`07-cost-check.txt`：最终窗口独立成本复算。
- `08-readme.txt`、`09-imports.txt`：README 与依赖检查。
- `09-stdlib-01-rebuild.txt` 至 `09-stdlib-09-07-observability.txt`：本轮全部零依赖复跑；其中 `09-stdlib-05-04-tests.txt` 显示 Ran 62 tests / OK。
- `protected-inputs.txt`：逐字节 SHA256 与 AST 比对，证明代码只变更两个获准测试方法，所有 src、其他测试、语料、评测用例及执行器、README、task-packet 均未变；eval/report.md 为命令重新生成。
- 两处测试修改前后对照：`/private/tmp/agent-harness-assertions-before/review.html`；完整代码差异：同目录 `changes.diff`。本轮基线副本保留在该目录。

本轮更新 tests/test_mcp.py、out/acceptance.md、自动生成的 out/dashboard.md、eval/report.md、out/eval-results.json 及验收证据。真实 demo 追加 out/trace.jsonl，rebuild 沿现有逻辑刷新本地索引状态。
工作区原本均为未跟踪文件、HEAD 无提交；未提交、推送、部署或派发其他 agent。项目未登记共享工作记忆，未写入外部登记或共享记忆。

## 关键设计取舍

1. 标准库与 SQLite：无需安装、离线可复现，依赖宿主启用 FTS5。
2. 确定性字符向量：无需模型下载，中文局部匹配可解释，但同义语义与哈希碰撞存在限制。
3. RRF 与规则重排：两路融合及得分可复算，适合小语料演示。
4. 缺失用量标为未知：避免将观测盲区当作免费调用。

## 已知局限与生产化下一步

20 篇自造语料与 20 条开发集查询不能代表生产召回；字符估算 tokens 不等于模型实测用量。历史 3 条记录缺失用量，因此完整历史总成本和平均任务成本仍未知，已知小计 0.005980 USD 可核算，未回填历史或虚构账单。
生产化优先接入真实用量与任务关联，再替换 bge-m3、ANN 与语义重排，使用独立留出集评测，并补齐认证、授权、预算、超时和日志治理。
