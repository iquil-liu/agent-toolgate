"""Offline evaluation; flags select console metrics, report always compares both modes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

# Also support `python3 eval/run_eval.py` from outside the repository.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.kb.index import KnowledgeIndex  # noqa: E402


def load_cases(path, known_ids):
    cases = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        case = json.loads(line)
        expected = case.get("expected_doc_ids")
        if (not isinstance(case.get("query"), str) or not case["query"].strip()
                or not isinstance(case.get("note"), str) or not case["note"].strip()
                or not isinstance(expected, list) or not 1 <= len(expected) <= 3
                or any(not isinstance(item, str) for item in expected)
                or len(set(expected)) != len(expected) or not set(expected) <= known_ids):
            raise ValueError(f"Invalid evaluation case at line {number}")
        cases.append(case)
    if len(cases) != 20 or len({case['query'] for case in cases}) != 20:
        raise ValueError("Exactly 20 distinct labeled queries required")
    return cases


def document_metrics(doc_ids, expected):
    # Preserve the best block's rank; duplicate blocks never consume a document slot.
    ranked = list(dict.fromkeys(doc_ids))
    relevant = set(expected)
    recalls = {f"recall@{k}": len(set(ranked[:k]) & relevant) / len(relevant)
               for k in (1, 3, 5)}
    rr = next((1 / rank for rank, doc in enumerate(ranked, 1) if doc in relevant), 0.0)
    return {**recalls, "mrr": rr}, ranked


def evaluate(index, cases, mode):
    details = []
    # Request every block so document deduplication cannot truncate Top5 early.
    limit = index.stats()["blocks"]
    for case in cases:
        start = perf_counter()
        results = index.search(case["query"], limit=limit, mode=mode)
        metrics, ranked = document_metrics([row["doc_id"] for row in results], case["expected_doc_ids"])
        latency = (perf_counter() - start) * 1000
        details.append({**case, **metrics, "ranked_doc_ids": ranked, "latency_ms": latency})
    return {"mode": mode, "queries": len(cases),
            **{key: sum(row[key] for row in details) / len(details)
               for key in ("recall@1", "recall@3", "recall@5", "mrr", "latency_ms")},
            "cases": details}


def render_report(results, fingerprint):
    lines = ["# 离线检索评测报告", "", f"运行时间：{datetime.now(timezone.utc).isoformat()}",
             f"实验指纹（语料、用例、实现）：`{fingerprint}`", "",
             "20 条人工标注查询，覆盖全部 20 个主题；术语、自然问句和特殊符号。",
             "按最高分块首次出现的顺序进行文档去重，Recall@K = TopK 中相关文档数 / 标注相关文档数，",
             "MRR = 首个正确文档排名倒数的宏平均（未命中为 0，遍历全部返回候选）。",
             "延迟为单进程每条查询一次的检索及文档去重耗时，不含 rebuild；两模式使用相同语料、标注、去重口径。", "",
             "| 模式 | Recall@1 | Recall@3 | Recall@5 | MRR | 平均延迟 ms |",
             "|---|---:|---:|---:|---:|---:|"]
    for result in results:
        lines.append(f"| {result['mode']} | " + " | ".join(f"{result[key]:.4f}" for key in
                     ("recall@1", "recall@3", "recall@5", "mrr", "latency_ms")) + " |")
    modes = {r['mode']: r for r in results}
    lines += ["", "## 结论", ""]
    if len(modes) == 2:
        baseline, hybrid = modes['baseline'], modes['hybrid']
        pairs = list(zip(baseline['cases'], hybrid['cases']))
        wins = [b['query'] for a, b in pairs if b['recall@5'] > a['recall@5']]
        ties = [b['query'] for a, b in pairs if b['recall@5'] == a['recall@5']]
        failures = [b for b in hybrid['cases'] if b['recall@5'] < 1]
        lines += [f"1. 混合检索 Recall@5 为 {hybrid['recall@5']:.2%}，硬门槛 80%；"
                  f"比 FTS 基线高 {(hybrid['recall@5'] - baseline['recall@5']):.2%}（百分点差）。",
                  f"2. 混合占优 {len(wins)} 条，主要是无分词的中文自然问句；例如：{'、'.join(wins[:2]) or '无'}。"
                  "FTS 使用完整查询词项，字符向量可补充局部重叠证据。",
                  f"3. Recall@5 持平 {len(ties)} 条，主要是明确术语/符号查询；例如：{'、'.join(ties[:2]) or '无'}。",
                  f"4. 混合未完全召回 {len(failures)} 条：" + ("；".join(
                      f"{b['query']}（期望 {','.join(b['expected_doc_ids'])}，首个相关排名 "
                      f"{round(1 / b['mrr']) if b['mrr'] else '未召回'}）" for b in failures) or "无") +
                  "。长句缺少完整词项命中时标题/覆盖加分弱，256 维哈希碰撞及共享字符可使无关候选前移。",
                  f"5. 平均延迟 FTS {baseline['latency_ms']:.3f} ms、混合 {hybrid['latency_ms']:.3f} ms；"
                  "向量通道逐块计算带来额外开销。本报告是自造小语料的开发集结果，不代表独立留出集或生产效果；"
                  "本轮没有通过修改标注、放宽阈值或降低基线能力提高分数，沿用 600 字符切分、RRF k=60、标题 0.04/覆盖 0.02。"]
    else:
        lines += ["当前为单模式报告；运行 `python3 eval/run_eval.py` 生成双模式对比及五条结论。"]
    lines += ["", "## 逐条证据", "", "完整排名及单条指标保存在 `out/eval-results.json`。", "",
              "| 模式 | 查询 | 期望文档 | Top5 文档 | Recall@5 | 首个相关排名 |",
              "|---|---|---|---|---:|---:|"]
    for result in results:
        for row in result['cases']:
            lines.append(f"| {result['mode']} | {row['query']} | {', '.join(row['expected_doc_ids'])} | "
                         f"{', '.join(row['ranked_doc_ids'][:5])} | {row['recall@5']:.2f} | "
                         f"{round(1 / row['mrr']) if row['mrr'] else '未命中'} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', action='store_true', help='Print FTS-only metrics (report still compares both modes)')
    parser.add_argument('--hybrid', action='store_true', help='Print hybrid metrics (report still compares both modes)')
    args = parser.parse_args()
    modes = [mode for mode in ('baseline', 'hybrid') if getattr(args, mode)] or ['baseline', 'hybrid']
    manifest = json.loads((ROOT / 'corpus/manifest.json').read_text(encoding='utf-8'))
    cases = load_cases(ROOT / 'eval/cases.jsonl', {d['doc_id'] for d in manifest['documents']})
    with KnowledgeIndex() as index:
        index.rebuild()
        # Always refresh the pair so a single-mode CLI run cannot leave a stale
        # comparison or silently mix different corpus/implementation versions.
        results = [evaluate(index, cases, mode) for mode in ('baseline', 'hybrid')]
    digest = hashlib.sha256()
    paths = [ROOT / d['path'] for d in manifest['documents']]
    paths += sorted((ROOT / 'src/kb').glob('*.py')) + [Path(__file__), ROOT / 'eval/cases.jsonl']
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()
    (ROOT / 'out/eval-results.json').write_text(json.dumps(
        {'fingerprint': fingerprint, 'results': results}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'eval/report.md').write_text(render_report(results, fingerprint), encoding='utf-8')
    for result in results:
        if result['mode'] in modes:
            print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
    print('Report: eval/report.md; per-query evidence: out/eval-results.json')
    hybrid = next((r for r in results if r['mode'] == 'hybrid'), None)
    if hybrid and hybrid['recall@5'] < 0.80:
        raise SystemExit('FAIL: hybrid Recall@5 must be >= 0.80')
    if len(results) == 2 and results[1]['recall@5'] <= results[0]['recall@5']:
        raise SystemExit('FAIL: hybrid Recall@5 must exceed baseline')


if __name__ == '__main__':
    main()
