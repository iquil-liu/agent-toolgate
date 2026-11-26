"""Standard MCP envelopes, resilient inputs, citations and audit degradation."""

from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.audit import AuditLog
from src.mcp_server import Server, demo_registry
from src.kb.index import ROOT


class MCPResilienceTests(unittest.TestCase):
    def test_bad_requests_then_normal_request_survive_real_stdio(self):
        bad = ['{', '{"x":NaN}', '{"x":Infinity}',
               '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"x":1e999}}}',
               '[' * 100 + '0' + ']' * 100,
               '[' * 2000 + '0' + ']' * 2000]
        normal = json.dumps({'jsonrpc': '2.0', 'id': 'next', 'method': 'tools/list'})
        payload = '\n'.join(item for invalid in bad for item in (invalid, normal)) + '\n'
        completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'serve', '--root', str(ROOT)],
                                   cwd=ROOT, input=payload, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 2 * len(bad))
        for error, recovered in zip(responses[::2], responses[1::2]):
            self.assertIn('error', error)
            self.assertEqual(recovered['id'], 'next')
            self.assertEqual(len(recovered['result']['tools']), 3)

    def test_direct_nonfinite_requests_rejected(self):
        server = Server(demo_registry())
        for value in (float('inf'), float('nan')):
            response = server.handle({'jsonrpc': '2.0', 'id': value, 'method': 'tools/list'})
            self.assertEqual(response['error']['code'], -32600)

    def test_unpaired_surrogates_rejected_and_next_echo_survives(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
             'params': {'name': 'echo', 'arguments': {'message': '\ud800'}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'echo', 'arguments': {'message': '\udfff'}}},
            {'jsonrpc': '2.0', 'id': '\ud800', 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/\ud800'},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'echo', 'arguments': {'\ud800': 'value', 'message': 'hello'}}},
        ]
        normal = {'jsonrpc': '2.0', 'id': 'next', 'method': 'tools/call',
                  'params': {'name': 'echo', 'arguments': {'message': '正常 emoji 😀'}}}
        payload = '\n'.join(json.dumps(item) for request in requests for item in (request, normal)) + '\n'
        for command in (['src.cli', 'serve', '--root', str(ROOT)], ['src.mcp_server']):
            with self.subTest(command=command):
                completed = subprocess.run([sys.executable, '-S', '-m', *command], cwd=ROOT,
                                           input=payload, capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, '')
                responses = [json.loads(line) for line in completed.stdout.splitlines()]
                self.assertEqual(len(responses), 2 * len(requests))
                for rejected, recovered in zip(responses[::2], responses[1::2]):
                    self.assertEqual(rejected['error']['code'], -32700)
                    self.assertEqual(recovered['id'], 'next')
                    self.assertFalse(recovered['result']['isError'])
                    self.assertEqual(json.loads(recovered['result']['content'][0]['text']),
                                     {'message': '正常 emoji 😀'})
        server = Server(demo_registry())
        for request in requests:
            with self.subTest(request=ascii(request)):
                self.assertEqual(server.handle(request)['error']['code'], -32600)

    def test_audit_failure_preserves_success_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            server = Server(demo_registry(), AuditLog(Path(directory) / 'trace'))
            warning = io.StringIO()
            with patch.object(server.audit, 'record', side_effect=OSError('disk full')), contextlib.redirect_stderr(warning):
                response = server.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                                          'params': {'name': 'echo', 'arguments': {'message': 'hello'}}})
            result = response['result']
            self.assertFalse(result['isError'])
            self.assertEqual(json.loads(result['content'][0]['text']), {'message': 'hello'})
            self.assertIs(result['degraded'], True)
            self.assertEqual(len(warning.getvalue().splitlines()), 1)
            self.assertIn('WARNING', warning.getvalue())
            self.assertIn('tools', server.handle({'jsonrpc': '2.0', 'id': 8, 'method': 'tools/list'})['result'])

    def test_error_envelope_and_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            server = Server(demo_registry(), AuditLog(Path(directory) / 'trace'))
            for arguments, error in [({'message': 'hello'}, False), ({}, True)]:
                result = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                        'params': {'name': 'echo', 'arguments': arguments}})['result']
                self.assertEqual(result['isError'], error)
                self.assertEqual(result['content'][0]['type'], 'text')
                self.assertEqual(json.loads(result['content'][0]['text']).get('code'),
                                 'SCHEMA_VIOLATION' if error else None)
        registry = demo_registry()
        for mode in ('passages', 'documents'):
            rows = registry.call('kb_search', {'query': '混合检索 重排', 'limit': 8, 'mode': mode})['result']['results']
            counts = Counter(row['doc_id'] for row in rows)
            self.assertLessEqual(max(counts.values()), 2 if mode == 'passages' else 1)
            for row in rows:
                for key in ('text', 'source_path', 'doc_id', 'heading_path', 'block_id', 'score', 'score_detail'):
                    self.assertIn(key, row)
                self.assertIn(row['text'], Path(row['source_path']).read_text())
                if mode == 'documents':
                    self.assertEqual(row['best_passage']['block_id'], row['block_id'])
                    self.assertIn(row['text'], row['summary'])
                    self.assertEqual(row['matched_passages'], len(row['passages']))
                    self.assertTrue(all(item['doc_id'] == row['doc_id'] for item in row['passages']))
        rows = registry.call('kb_search', {'query': '混合检索 重排', 'limit': 5, 'max_per_doc': 1})['result']['results']
        self.assertEqual(len({row['doc_id'] for row in rows}), len(rows))
