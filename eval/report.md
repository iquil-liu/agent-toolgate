# 离线检索评测报告

运行时间：2026-11-26T08:33:44.847858+00:00
实验指纹（语料、用例、实现）：`d09f06d3cc1b5144ad3f8c187eca5cc73f0cf699e3a7249a13df389bfecdf79e`

20 条人工标注查询，覆盖全部 20 个主题；术语、自然问句和特殊符号。
按最高分块首次出现的顺序进行文档去重，Recall@K = TopK 中相关文档数 / 标注相关文档数，
MRR = 首个正确文档排名倒数的宏平均（未命中为 0，遍历全部返回候选）。
延迟为单进程每条查询一次的检索及文档去重耗时，不含 rebuild；两模式使用相同语料、标注、去重口径。

| 模式 | Recall@1 | Recall@3 | Recall@5 | MRR | 平均延迟 ms |
|---|---:|---:|---:|---:|---:|
| baseline | 0.5500 | 0.5500 | 0.5500 | 0.5500 | 0.3034 |
| hybrid | 0.8000 | 0.9000 | 0.9500 | 0.8625 | 7.3991 |

## 结论

1. 混合检索 Recall@5 为 95.00%，硬门槛 80%；比 FTS 基线高 40.00%（百分点差）。
2. 混合占优 8 条，主要是无分词的中文自然问句；例如：编排器怎样恢复中断的步骤、响应丢失后怎样避免重复写入。FTS 使用完整查询词项，字符向量可补充局部重叠证据。
3. Recall@5 持平 12 条，主要是明确术语/符号查询；例如：MCP stdio 工具注册、上下文窗口 摘要 来源。
4. 混合未完全召回 1 条：多个相关答案的召回率分母如何确定（期望 recall-mrr，首个相关排名 6）。长句缺少完整词项命中时标题/覆盖加分弱，256 维哈希碰撞及共享字符可使无关候选前移。
5. 平均延迟 FTS 0.303 ms、混合 7.399 ms；向量通道逐块计算带来额外开销。本报告是自造小语料的开发集结果，不代表独立留出集或生产效果；本轮没有通过修改标注、放宽阈值或降低基线能力提高分数，沿用 600 字符切分、RRF k=60、标题 0.04/覆盖 0.02。

## 逐条证据

完整排名及单条指标保存在 `out/eval-results.json`。

| 模式 | 查询 | 期望文档 | Top5 文档 | Recall@5 | 首个相关排名 |
|---|---|---|---|---:|---:|
| baseline | MCP stdio 工具注册 | mcp-tools | mcp-tools | 1.00 | 1 |
| baseline | 编排器怎样恢复中断的步骤 | agent-workflow |  | 0.00 | 未命中 |
| baseline | 上下文窗口 摘要 来源 | context-memory | context-memory, single-truth, audit-log, rag-splitting, secret-audit | 1.00 | 1 |
| baseline | 响应丢失后怎样避免重复写入 | idempotency |  | 0.00 | 未命中 |
| baseline | 指数退避 随机抖动 | retry-backoff | retry-backoff | 1.00 | 1 |
| baseline | 客户端停止等待是否代表写入撤销 | timeout-cancel |  | 0.00 | 未命中 |
| baseline | JSON Schema 布尔值 整数 | tool-contract | tool-contract, mcp-tools | 1.00 | 1 |
| baseline | 权限/最小授权:(资源隔离) | least-privilege | least-privilege, secret-audit, retry-backoff | 1.00 | 1 |
| baseline | 密钥轮换 脱敏 | secret-audit | secret-audit | 1.00 | 1 |
| baseline | 相同参数字段顺序变化后摘要应一致吗 | audit-log |  | 0.00 | 未命中 |
| baseline | trace span 延迟分位数 | observability-trace | observability-trace | 1.00 | 1 |
| baseline | 失败调用也需要计算成本吗 | cost-quota |  | 0.00 | 未命中 |
| baseline | RAG 标题层级 段落 | rag-splitting | rag-splitting | 1.00 | 1 |
| baseline | 哈希向量碰撞能代表语义理解吗 | vector-ann |  | 0.00 | 未命中 |
| baseline | 混合检索 RRF FTS5 | hybrid-rrf | hybrid-rrf, reranking | 1.00 | 1 |
| baseline | 重排能找回没有进入候选的文档吗 | reranking |  | 0.00 | 未命中 |
| baseline | 评测集 困难负例 数据泄漏 | evaluation-set | evaluation-set | 1.00 | 1 |
| baseline | 多个相关答案的召回率分母如何确定 | recall-mrr |  | 0.00 | 未命中 |
| baseline | 多 Agent 交接 所有权 | agent-handoff | agent-handoff, recall-mrr, agent-workflow, rag-splitting, observability-trace | 1.00 | 1 |
| baseline | 并发写入冲突后可以只更新版本号重试吗 | single-truth |  | 0.00 | 未命中 |
| hybrid | MCP stdio 工具注册 | mcp-tools | mcp-tools, vector-ann, agent-workflow, audit-log, context-memory | 1.00 | 1 |
| hybrid | 编排器怎样恢复中断的步骤 | agent-workflow | agent-workflow, timeout-cancel, retry-backoff, context-memory, least-privilege | 1.00 | 1 |
| hybrid | 上下文窗口 摘要 来源 | context-memory | context-memory, single-truth, mcp-tools, rag-splitting, audit-log | 1.00 | 1 |
| hybrid | 响应丢失后怎样避免重复写入 | idempotency | tool-contract, idempotency, agent-workflow, recall-mrr, audit-log | 1.00 | 2 |
| hybrid | 指数退避 随机抖动 | retry-backoff | retry-backoff, vector-ann | 1.00 | 1 |
| hybrid | 客户端停止等待是否代表写入撤销 | timeout-cancel | timeout-cancel, audit-log, single-truth, mcp-tools, retry-backoff | 1.00 | 1 |
| hybrid | JSON Schema 布尔值 整数 | tool-contract | tool-contract, mcp-tools, audit-log, reranking, vector-ann | 1.00 | 1 |
| hybrid | 权限/最小授权:(资源隔离) | least-privilege | least-privilege, secret-audit, retry-backoff, context-memory, timeout-cancel | 1.00 | 1 |
| hybrid | 密钥轮换 脱敏 | secret-audit | secret-audit | 1.00 | 1 |
| hybrid | 相同参数字段顺序变化后摘要应一致吗 | audit-log | audit-log, tool-contract, vector-ann, reranking, retry-backoff | 1.00 | 1 |
| hybrid | trace span 延迟分位数 | observability-trace | observability-trace, recall-mrr, vector-ann, rag-splitting, reranking | 1.00 | 1 |
| hybrid | 失败调用也需要计算成本吗 | cost-quota | mcp-tools, single-truth, cost-quota, timeout-cancel, least-privilege | 1.00 | 3 |
| hybrid | RAG 标题层级 段落 | rag-splitting | rag-splitting, observability-trace, agent-handoff, reranking, agent-workflow | 1.00 | 1 |
| hybrid | 哈希向量碰撞能代表语义理解吗 | vector-ann | vector-ann, recall-mrr, timeout-cancel, hybrid-rrf, tool-contract | 1.00 | 1 |
| hybrid | 混合检索 RRF FTS5 | hybrid-rrf | hybrid-rrf, reranking, recall-mrr, mcp-tools, vector-ann | 1.00 | 1 |
| hybrid | 重排能找回没有进入候选的文档吗 | reranking | reranking, rag-splitting, evaluation-set, hybrid-rrf, mcp-tools | 1.00 | 1 |
| hybrid | 评测集 困难负例 数据泄漏 | evaluation-set | evaluation-set, rag-splitting, agent-workflow, observability-trace | 1.00 | 1 |
| hybrid | 多个相关答案的召回率分母如何确定 | recall-mrr | evaluation-set, hybrid-rrf, vector-ann, observability-trace, rag-splitting | 0.00 | 6 |
| hybrid | 多 Agent 交接 所有权 | agent-handoff | agent-handoff, agent-workflow, recall-mrr, reranking, evaluation-set | 1.00 | 1 |
| hybrid | 并发写入冲突后可以只更新版本号重试吗 | single-truth | secret-audit, agent-workflow, idempotency, single-truth, context-memory | 1.00 | 4 |
