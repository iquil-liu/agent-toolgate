# 参与贡献

## 先跑通

```bash
python3 -m unittest discover -s tests -v      # 62 条
python3 eval/run_eval.py --hybrid             # 评测
python3 scripts/mcp_client_probe.py           # 独立客户端握手
```

三条都过再改代码。

## 改代码的几条约定

**别引入第三方依赖。** 主路径只用标准库，CI 会额外跑一次 `python3 -S`（禁用 site-packages）。加了 `requests`、`numpy` 这类包会让这一步直接失败。

**改检索逻辑要带指标。** 碰 `src/kb/` 下的任何排序、分词、融合计算，必须同时跑 `eval/run_eval.py` 并在 PR 里贴新的 Recall@5 与 MRR。hybrid 的 Recall@5 低于 0.80 会被 CI 判失败，这是有意设的闸。

**`docs/` 只放语料。** 有测试断言 `docs/*.md` 必须与 `corpus/manifest.json` 一一对应，往里塞 README 之类的文件会让测试挂。文档放仓库根或 `examples/`。

**改语料要同步改标注。** `eval/cases.jsonl` 里的 20 条查询是人工标注的 ground truth，换语料就得重标，否则指标不可信。

## 提交

- 一个提交做一件事，标题写清做了什么
- 涉及行为变化就更新 `CHANGELOG.md`
- 边界情况、取舍理由写进提交信息，比写在代码注释里更有用

## 报问题

开 issue 时说清三样：复现命令、实际输出、期望输出。检索结果不对的话，把 `score_detail` 一起贴上。
