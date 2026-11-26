import json
from pathlib import Path
import tempfile
import unittest

from src.observability import percentile, render_dashboard, summarize


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'trace.jsonl'

    def write(self, events):
        self.path.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')

    def event(self, **changes):
        return {'trace_id': 'trace-a', 'status': 'success', 'duration_ms': 10,
                'error_code': None, 'prompt_tokens': 1000, 'completion_tokens': 500, **changes}

    def test_hand_calculated_percentiles_and_empty(self):
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([3], .95), 3)
        self.assertEqual(percentile([40, 10, 30, 20], .5), 25)
        self.assertAlmostEqual(percentile([40, 10, 30, 20], .95), 38.5)

    def test_cost_includes_failures_and_groups_tasks(self):
        self.write([self.event(task_id='task-a'),
                    self.event(trace_id='trace-b', task_id='task-a', status='error',
                               error_code='HANDLER_ERROR', duration_ms=30),
                    self.event(trace_id='trace-c', task_id='task-b', duration_ms=20)])
        result = summarize(self.path, prompt_price=2, completion_price=4)
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['success_rate'], 2/3)
        self.assertEqual(result['error_codes'], {'HANDLER_ERROR': 1})
        self.assertEqual(result['p50_ms'], 20)
        self.assertAlmostEqual(result['p95_ms'], 29)
        self.assertAlmostEqual(result['total_cost_usd'], .012)
        self.assertAlmostEqual(result['mean_task_cost_usd'], .006)
        self.assertAlmostEqual(result['tasks']['task-a']['known_cost_usd'], .008)
        self.assertEqual(result['prompt_tokens'], 3000)

    def test_legacy_missing_tokens_are_unknown_not_free(self):
        legacy = self.event(trace_id='legacy')
        del legacy['completion_tokens']
        self.write([self.event(), legacy])
        result = summarize(self.path)
        self.assertIsNone(result['total_cost_usd'])
        self.assertIsNone(result['mean_task_cost_usd'])
        self.assertEqual(result['usage_missing_calls'], 1)
        self.assertAlmostEqual(result['known_cost_usd'], .002)
        self.assertEqual(len(result['tasks']), 2)
        dashboard = render_dashboard(result, 'trace.jsonl')
        for required in ('P50', 'P95', '总成本', '单任务成本', '未知', 'HANDLER_ERROR' if result['error_codes'] else '无'):
            self.assertIn(required, dashboard)

    def test_explicit_zero_usage_and_empty_file(self):
        self.write([self.event(prompt_tokens=0, completion_tokens=0)])
        self.assertEqual(summarize(self.path)['total_cost_usd'], 0)
        self.write([])
        result = summarize(self.path)
        self.assertEqual(result['calls'], 0)
        self.assertIsNone(result['success_rate'])
        self.assertIsNone(result['p95_ms'])
        self.assertIsNone(result['mean_task_cost_usd'])

    def test_corrupt_and_invalid_values_fail_with_line_number(self):
        for event in (self.event(duration_ms=-1), self.event(duration_ms=float('nan')),
                      self.event(prompt_tokens=True), self.event(completion_tokens=-1),
                      self.event(status='pending'), self.event(task_id=[]), []):
            self.write([event])
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'line 1'):
                summarize(self.path)
        self.path.write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'line 1'):
            summarize(self.path)

    def test_prices_are_configurable_and_invalid_prices_rejected(self):
        self.write([self.event()])
        self.assertEqual(summarize(self.path, 0, 0)['total_cost_usd'], 0)
        for price in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                summarize(self.path, price, 2)


if __name__ == '__main__':
    unittest.main()
