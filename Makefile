PY ?= python3

.PHONY: help demo test eval dashboard rebuild probe ingest

help:
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t

demo: ## 跑通四条 JSON-RPC 调用（initialize / tools.list / tools.call x2）
	$(PY) -m src.cli demo

test: ## 运行全部单元测试
	$(PY) -m unittest discover -s tests -v

eval: ## 同一份语料对比 baseline 与 hybrid，并写出 eval/report.md
	$(PY) eval/run_eval.py --hybrid

dashboard: ## 由 out/trace.jsonl 生成 out/dashboard.md 与成本汇总
	$(PY) -m src.observability

rebuild: ## 重建检索索引
	$(PY) -m src.cli rebuild

probe: ## 独立 MCP 客户端探针（子进程握手 + 断言）
	$(PY) scripts/mcp_client_probe.py

ingest: ## 用自己的 Markdown 建索引：make ingest SRC=~/notes
	$(PY) -m src.cli ingest --from "$(SRC)"
