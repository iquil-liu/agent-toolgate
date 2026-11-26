"""Real stdio client and isolated, offline installed-layout checks."""

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServeEndToEndTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        notes = self.base / 'notes'
        notes.mkdir()
        (notes / 'one.md').write_text('# One\nuniquecitron 混合检索 重排')
        (notes / 'two.md').write_text('# Two\nuniquetangerine')
        self.kb = self.base / 'kb'
        self.run_cli('ingest', '--from', str(notes), '--root', str(self.kb))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-S', '-m', 'src.cli', *args], cwd=ROOT,
                              capture_output=True, text=True, check=True, timeout=10)

    def test_ingest_serve_standard_client_and_trace(self):
        trace = self.base / 'audit/custom.jsonl'
        requests = [dict(jsonrpc='2.0', id=1, method='initialize'),
                    dict(jsonrpc='2.0', id='query', method='tools/call',
                         params={'name': 'kb_search', 'arguments': {'query': 'uniquecitron'}})]
        completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'serve', '--root', str(self.kb),
                                    '--trace', str(trace)], cwd=self.base,
                                   env={**os.environ, 'PYTHONPATH': str(ROOT)},
                                   input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                   capture_output=True, text=True, check=True, timeout=10)
        init, call = map(json.loads, completed.stdout.splitlines())
        self.assertIn('protocolVersion', init['result'])
        self.assertIn('serverInfo', init['result'])
        self.assertEqual(init['result']['capabilities'], {'tools': {}})
        self.assertFalse(call['result']['isError'])
        result = json.loads(call['result']['content'][0]['text'])['results'][0]
        self.assertIn('uniquecitron', result['text'])
        self.assertEqual(Path(result['source_path']), (self.kb / 'docs/one.md').resolve())
        event = json.loads(trace.read_text())
        self.assertEqual(event['request_id'], 'query')
        self.assertEqual(len(event['kb_version']), 64)
        self.assertTrue(event['timestamp'].endswith('+00:00'))

    def copied_command(self):
        site = self.base / 'site'
        shutil.copytree(ROOT / 'src', site / 'toolgate', ignore=shutil.ignore_patterns('__pycache__'))
        executable = self.base / 'bin/toolgate'
        executable.parent.mkdir()
        executable.write_text('#!' + sys.executable + '\nfrom toolgate.cli import main\nmain()\n')
        executable.chmod(0o755)
        return executable, {**os.environ, 'PYTHONPATH': str(site)}

    def test_offline_copied_package_outside_repository(self):
        executable, env = self.copied_command()
        completed = subprocess.run([str(executable), '--root', str(self.kb), 'search', 'uniquetangerine'],
                                   cwd=self.base, env=env,
                                   capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(json.loads(completed.stdout)[0]['text'], 'uniquetangerine')

    def test_configured_library_cannot_be_overridden_by_call_arguments(self):
        notes = self.base / 'foreign-notes'
        notes.mkdir()
        (notes / 'private.md').write_text('# Private\nuniqueforeignviolet')
        foreign = self.base / 'foreign-kb'
        self.run_cli('ingest', '--from', str(notes), '--root', str(foreign))
        manifest = (self.kb / 'corpus/manifest.json').read_bytes()
        foreign_index = (foreign / 'out/kb.sqlite3').read_bytes()
        requests = [dict(jsonrpc='2.0', id=number, method='tools/call',
                         params={'name': 'kb_search', 'arguments': arguments})
                    for number, arguments in enumerate([
                        {'query': 'uniqueforeignviolet', 'root': str(foreign)},
                        {'query': 'uniqueforeignviolet'}, {'query': 'uniquecitron'}], 1)]
        completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'serve', '--root', str(self.kb)],
                                   cwd=ROOT, input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                   capture_output=True, text=True, check=True, timeout=10)
        rejected, local_only, normal = [json.loads(line)['result'] for line in completed.stdout.splitlines()]
        self.assertTrue(rejected['isError'])
        error = json.loads(rejected['content'][0]['text'])
        self.assertEqual((error['code'], error['path']), ('SCHEMA_VIOLATION', '$.root'))
        self.assertFalse(local_only['isError'])
        for row in json.loads(local_only['content'][0]['text'])['results']:
            self.assertTrue(Path(row['source_path']).is_relative_to(self.kb.resolve()))
            self.assertNotIn('uniqueforeignviolet', row['text'])
        self.assertFalse(normal['isError'])
        rows = json.loads(normal['content'][0]['text'])['results']
        self.assertIn('uniquecitron', rows[0]['text'])
        self.assertTrue(all(Path(row['source_path']).is_relative_to(self.kb.resolve()) for row in rows))
        self.assertEqual((foreign / 'out/kb.sqlite3').read_bytes(), foreign_index)
        events = [json.loads(line) for line in (self.kb / 'out/trace.jsonl').read_text().splitlines()]
        self.assertEqual([event['request_id'] for event in events], [1, 2, 3])
        self.assertTrue(all(event['kb_version'] == hashlib.sha256(manifest).hexdigest() for event in events))

    def test_copied_package_demo_uses_root_before_or_after_subcommand(self):
        executable, env = self.copied_command()
        for args in (['--root', str(self.kb), 'demo'], ['demo', '--root', str(self.kb)]):
            with self.subTest(args=args):
                completed = subprocess.run([str(executable), *args], cwd=self.base, env=env,
                                           capture_output=True, text=True, check=True, timeout=10)
                calls = [json.loads(line) for line in completed.stdout.splitlines()]
                self.assertEqual(len(calls), 4)
                results = [row['response']['result'] for row in calls if row['method'] == 'tools/call']
                self.assertTrue(all(result['isError'] is False for result in results))
                rows = json.loads(results[-1]['content'][0]['text'])['results']
                self.assertTrue(rows)
                self.assertTrue(all(Path(row['source_path']).is_relative_to(self.kb.resolve()) for row in rows))
        self.assertTrue((self.kb / 'out/trace.jsonl').is_file())
        self.assertFalse((Path(env['PYTHONPATH']) / 'out').exists())

    def test_copied_package_demo_without_library_fails_with_guidance(self):
        executable, env = self.copied_command()
        completed = subprocess.run([str(executable), 'demo'], cwd=self.base, env=env,
                                   capture_output=True, text=True, timeout=10)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('ingest', completed.stderr)

    def test_demo_tool_failure_has_nonzero_exit(self):
        executable, env = self.copied_command()
        (self.kb / 'out/kb.sqlite3').unlink()
        (self.kb / 'docs/one.md').unlink()
        completed = subprocess.run([str(executable), '--root', str(self.kb), 'demo'],
                                   cwd=self.base, env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(completed.returncode, 0)
        calls = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(calls[-1]['response']['result']['isError'])
        self.assertIn('Demo failed', completed.stderr)

    def test_missing_library_fails_at_start(self):
        for root in (self.base / 'missing',):
            completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'serve', '--root', str(root)],
                                       cwd=ROOT, input='', capture_output=True, text=True, timeout=10)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('ingest', completed.stderr)
        (self.kb / 'out/kb.sqlite3').unlink()
        completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'serve', '--root', str(self.kb)],
                                   cwd=ROOT, input='', capture_output=True, text=True, timeout=10)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('rebuild', completed.stderr)
