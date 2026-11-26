"""Regression coverage for real retrieval and explicitly estimated local usage."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.audit import AuditLog
from src.mcp_server import Server, demo_registry
from src.observability import render_dashboard, summarize

ROOT = Path(__file__).resolve().parents[1]


class P3FixTests(unittest.TestCase):
    def test_demo_matches_cli_search(self):
        demo = subprocess.run([sys.executable, '-m', 'src.mcp_server', '--demo'],
                              cwd=ROOT, capture_output=True, text=True, check=True)
        calls = [json.loads(line) for line in demo.stdout.splitlines()]
        call = next(row['response']['result'] for row in calls
                    if row['method'] == 'tools/call' and
                    'results' in json.loads(row['response']['result']['content'][0]['text']))
        self.assertFalse(call['isError'])
        cli = subprocess.run([sys.executable, '-m', 'src.kb.cli', 'search', '混合检索 重排'],
                             cwd=ROOT, capture_output=True, text=True, check=True)
        expected = json.loads(cli.stdout)
        actual = json.loads(call['content'][0]['text'])['results']
        self.assertEqual(len(actual), 3)
        self.assertEqual([row['doc_id'] for row in actual], [row['doc_id'] for row in expected])
        self.assertEqual(actual, expected)

    def test_top_k_and_empty_query(self):
        registry = demo_registry()
        result = registry.call('kb_search', {'query': '混合检索 重排', 'limit': 5})
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['result']['results']), 5)
        self.assertEqual(registry.call('kb_search', {'query': ''})['result']['results'], [])
        self.assertFalse(registry.call('kb_search', {'query': '重排', 'limit': 0})['ok'])

    def test_usage_estimates_success_and_error_without_raw_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            server = Server(demo_registry(), AuditLog(path))
            for name, arguments in [('echo', {'message': '私密内容 abc'}), ('missing', {}),
                                    ('echo', []), ('echo', {})]:
                response = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                          'params': {'name': name, 'arguments': arguments}})
                event = json.loads(path.read_text().splitlines()[-1])
                if 'result' in response:
                    payload = response['result']
                    body = json.loads(payload['content'][0]['text'])
                    output = {'ok': False, 'error': body} if payload['isError'] else {'ok': True, 'result': body}
                else:
                    output = response['error']
                for field, data in [('prompt_tokens', arguments), ('completion_tokens', output)]:
                    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                    self.assertEqual(event[field], (len(canonical) + 3) // 4)
                self.assertIs(event['usage_estimated'], True)
            self.assertNotIn('私密内容', path.read_text())
            summary = summarize(path)
            self.assertEqual(summary['usage_missing_calls'], 0)
            self.assertEqual(summary['usage_estimated_calls'], 4)
            self.assertEqual(summary['usage_measured_calls'], 0)
            self.assertGreater(summary['total_cost_usd'], 0)
            self.assertEqual(summary['known_cost_usd'], summary['estimated_cost_usd'])
            self.assertIn('Unicode', render_dashboard(summary, 'trace.jsonl'))

    def test_legacy_missing_and_unmarked_usage_are_not_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            event = AuditLog(path).record('echo', {}, 'success', 1, None, response={})
            event.pop('usage_estimated')
            legacy = {key: value for key, value in event.items()
                      if key not in ('prompt_tokens', 'completion_tokens')}
            with path.open('a') as stream:
                stream.write(json.dumps(event) + '\n' + json.dumps(legacy) + '\n')
            summary = summarize(path)
            self.assertEqual(summary['usage_missing_calls'], 1)
            self.assertEqual(summary['usage_unspecified_calls'], 1)
            self.assertEqual(summary['usage_measured_calls'], 0)
            self.assertIsNone(summary['total_cost_usd'])
            event['usage_estimated'] = 'true'
            path.write_text(json.dumps(event))
            with self.assertRaisesRegex(ValueError, 'usage_estimated'):
                summarize(path)
