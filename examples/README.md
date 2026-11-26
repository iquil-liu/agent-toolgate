# 示例

两件事最常被问到：怎么加自己的工具，怎么用自己的文档建索引。

## 1. 加一个自己的工具

`custom_tool.py` 注册了一个 `word_count` 工具，然后通过 JSON-RPC 调三次：列工具、正常调用、故意缺字段触发契约校验。

```bash
python3 examples/custom_tool.py
```

关键只有三行：定义 handler、构造 `Tool`（名字 / 版本 / 描述 / `input_schema` / handler）、`registry.register`。注册之后，参数校验、审计、错误码、`tools/list` 声明全部自动生效，不用自己写。

## 2. 用自己的文档建索引

```bash
python3 -m src.cli ingest --from ~/notes --root ~/.toolgate
python3 -m src.cli search "你的问题" --root ~/.toolgate
```

`ingest` 会把目录下的 `.md` 复制到 `~/.toolgate/docs/`，按文件名生成 doc_id，写入 `corpus/manifest.json`，再建 FTS5 + 向量索引。`--recursive` 递归子目录，`--clean` 先清空已导入的文档。

索引和仓库自带的 20 篇语料完全隔离，互不污染。

想看排序理由：

```bash
python3 -m src.cli search "你的问题" --root ~/.toolgate | python3 -m json.tool
```

每条结果都带 `score_detail`：字面排名、向量排名、余弦相似度、RRF 融合分、标题命中、词覆盖。分数不对时能直接看出是哪一路出了问题。
