"""Summarize local audit JSONL with explicit token-usage coverage."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, quantile):
    """Linear interpolation at (n - 1) * quantile; empty samples have no value."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(path, prompt_price=1.0, completion_price=2.0):
    for price in (prompt_price, completion_price):
        if not math.isfinite(price) or price < 0:
            raise ValueError('Prices must be finite nonnegative USD per million tokens')
    events = []
    # Missing/invalid logs fail visibly; never silently omit broken calls.
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Expected event object')
            duration = event.get('duration_ms')
            if (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
                    or event.get('status') not in ('success', 'error')
                    or not isinstance(event.get('trace_id'), str) or not event['trace_id']
                    or (event.get('task_id') is not None and
                        (not isinstance(event['task_id'], str) or not event['task_id']))
                    or type(event.get('error_code')) not in (type(None), str, int)):
                raise ValueError('Invalid duration/status/trace_id/task_id/error_code')
            for field in ('prompt_tokens', 'completion_tokens'):
                if field in event and (type(event[field]) is not int or event[field] < 0):
                    raise ValueError(f'Invalid {field}')
            if 'usage_estimated' in event and type(event['usage_estimated']) is not bool:
                raise ValueError('Invalid usage_estimated')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Invalid trace line {number}: {exc}') from exc
        events.append(event)
    errors = Counter()
    tasks = {}
    known_cost = 0.0
    complete = 0
    estimated = measured = unspecified = 0
    estimated_cost = measured_cost = 0.0
    prompt = completion = 0
    for event in events:
        if event['status'] == 'error':
            errors[str(event['error_code']) if event.get('error_code') is not None else 'UNSPECIFIED'] += 1
        task = tasks.setdefault(event.get('task_id') or event['trace_id'], {'known_cost_usd': 0.0, 'missing_usage': 0})
        if 'prompt_tokens' not in event or 'completion_tokens' not in event:
            task['missing_usage'] += 1
            continue
        complete += 1
        prompt += event['prompt_tokens']
        completion += event['completion_tokens']
        cost = (event['prompt_tokens'] * prompt_price + event['completion_tokens'] * completion_price) / 1_000_000
        known_cost += cost
        if event.get('usage_estimated') is True:
            estimated += 1
            estimated_cost += cost
        elif event.get('usage_estimated') is False:
            measured += 1
            measured_cost += cost
        else:
            unspecified += 1
        task['known_cost_usd'] += cost
    count = len(events)
    durations = [e['duration_ms'] for e in events]
    return {'calls': count, 'success_rate': sum(e['status'] == 'success' for e in events) / count if count else None,
            'error_codes': dict(sorted(errors.items())), 'p50_ms': percentile(durations, 0.50),
            'p95_ms': percentile(durations, 0.95), 'usage_complete_calls': complete,
            'usage_missing_calls': count - complete, 'prompt_tokens': prompt, 'completion_tokens': completion,
            'known_cost_usd': known_cost, 'total_cost_usd': known_cost if complete == count else None,
            'usage_estimated_calls': estimated, 'usage_measured_calls': measured,
            'usage_unspecified_calls': unspecified, 'estimated_cost_usd': estimated_cost,
            'measured_cost_usd': measured_cost,
            'usage_estimation_method': 'ceil(len(canonical JSON Unicode characters) / 4), separately for arguments and tool result/error; not provider metering',
            'mean_task_cost_usd': known_cost / len(tasks) if tasks and complete == count else None,
            'tasks': tasks, 'prompt_price': prompt_price, 'completion_price': completion_price}


def render_dashboard(summary, source):
    def value(number, unit=''):
        return '未知/无样本' if number is None else f'{number:.6f}{unit}'

    rows = [('总调用数', summary['calls']), ('成功率', value(
        summary['success_rate'] * 100 if summary['success_rate'] is not None else None, '%')),
        ('P50 延迟 ms', value(summary['p50_ms'])), ('P95 延迟 ms', value(summary['p95_ms'])),
        ('用量完整 / 缺失调用数', f"{summary['usage_complete_calls']} / {summary['usage_missing_calls']}"),
        ('已知 prompt / completion tokens', f"{summary['prompt_tokens']} / {summary['completion_tokens']}"),
        ('已知成本小计 USD', value(summary['known_cost_usd'])),
        ('估算 / 实测 / 未标来源调用数', f"{summary['usage_estimated_calls']} / {summary['usage_measured_calls']} / {summary['usage_unspecified_calls']}"),
        ('字符估算成本 USD', value(summary['estimated_cost_usd'])),
        ('明确标记实测成本 USD', value(summary['measured_cost_usd'])),
        ('总成本估算 USD', value(summary['total_cost_usd'])),
        ('平均单任务成本 USD', value(summary['mean_task_cost_usd']))]
    lines = ['# 调用观测面板', '', f'来源：`{source}`，窗口为文件内全部 {summary["calls"]} 条调用。', '',
             '| 指标 | 值 |', '|---|---:|']
    lines += [f'| {name} | {result} |' for name, result in rows]
    lines += ['', '## 错误码分布', '', '| 错误码 | 调用数 |', '|---|---:|']
    lines += [f'| {code} | {count} |' for code, count in summary['error_codes'].items()] or ['| 无 | 0 |']
    lines += ['', '## 成本口径', '',
              f"占位单价：输入 {summary['prompt_price']:.6f}、输出 {summary['completion_price']:.6f} USD / 百万 tokens；可通过 CLI 替换。",
              '每条成本 = (prompt_tokens × 输入单价 + completion_tokens × 输出单价) / 1,000,000，失败调用也计费。',
              '按 task_id 聚合；缺少 task_id 时以 trace_id 为独立任务。缺少任一 token 字段时用量未知，不能推定为零。',
              'usage_estimated=true 表示字符估算，false 表示上游明确标记实测；缺标记的旧用量不推定为实测。',
              '字符估算：入参和工具结果/错误分别序列化为 sort_keys=true、ensure_ascii=false、无额外空格的 JSON，按 Unicode 字符数除以 4 向上取整。',
              '估算包含 JSON 结构字符，不含系统提示、协议封装或 tokenizer 差异；本地工具未调用模型，这只是容量与占位价格演示，不是供应商账单。',
              'P50/P95 使用排序样本的 (n−1)×分位点位置做线性插值；没有样本时不报告零延迟。', '',
              '| 任务 | 已知成本 USD | 缺用量调用数 |', '|---|---:|---:|']
    lines += [f"| {task.replace('|', '/')} | {data['known_cost_usd']:.6f} | {data['missing_usage']} |"
              for task, data in sorted(summary['tasks'].items())]
    lines += ['', '## 结论', '',
              f"- 共 {summary['calls']} 次调用；错误码按全部失败事件聚合，成功率分母为全部调用。",
              '- 延迟只覆盖审计记录的工具执行阶段，不含网络排队与端到端等待；小样本分位数不代表稳定服务水平。',
              f"- {summary['usage_missing_calls']} 次缺少完整 token 用量；已知小计是可计价部分，"
              '有缺失时总成本及平均单任务成本标为未知；已知小计包含明确标记的估算部分，旧记录不回填。',
              '- 本面板只读取调用元数据，不读取或还原原始入参；占位价格不是供应商报价。']
    return '\n'.join(lines) + '\n'


def nonnegative_price(text):
    number = float(text)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('Price must be finite and nonnegative')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, default=ROOT / 'out/trace.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'out/dashboard.md')
    parser.add_argument('--prompt-price', type=nonnegative_price, default=1.0, help='USD per million input tokens (placeholder)')
    parser.add_argument('--completion-price', type=nonnegative_price, default=2.0, help='USD per million output tokens (placeholder)')
    args = parser.parse_args()
    try:
        summary = summarize(args.trace, args.prompt_price, args.completion_price)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_dashboard(summary, args.trace.name), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    print(f'Dashboard: {args.output}')


if __name__ == '__main__':
    main()
