"""Transactional ingestion regressions, including the four requested cases."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.cli import main
from src.kb.index import KnowledgeIndex


class IngestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'notes'
        self.root = self.base / 'kb'
        for folder, text in [('a', 'uniquesaffron'), ('b', 'uniqueindigo')]:
            path = self.source / folder / 'guide.md'
            path.parent.mkdir(parents=True)
            path.write_text('# Guide\n' + text, encoding='utf-8')

    def ingest(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(['ingest', '--from', str(self.source), '--root', str(self.root), '--recursive', *extra])
        return json.loads(output.getvalue())

    def test_a_recursive_duplicate_names_both_searchable(self):
        self.assertEqual(self.ingest()['documents'], 2)
        with KnowledgeIndex(self.root) as index:
            for term, doc in [('uniquesaffron', 'a/guide'), ('uniqueindigo', 'b/guide')]:
                self.assertEqual(index.search(term)[0]['doc_id'], doc)
                self.assertTrue((self.root / 'docs' / (doc + '.md')).is_file())

    def test_b_source_overlap_rejected_before_clean(self):
        self.ingest()
        for source in (self.root / 'docs', self.root, self.root / 'docs/a'):
            with self.assertRaisesRegex(SystemExit, 'contain each other'):
                main(['ingest', '--from', str(source), '--root', str(self.root), '--clean'])
            completed = subprocess.run([sys.executable, '-S', '-m', 'src.cli', 'ingest',
                                        '--from', str(source), '--root', str(self.root), '--clean'],
                                       cwd=Path(__file__).resolve().parents[1],
                                       capture_output=True, text=True, timeout=10)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('contain each other', completed.stderr)
            self.assertTrue((self.root / 'docs/a/guide.md').exists())

    def test_c_repeat_is_idempotent(self):
        self.ingest()
        result = self.ingest()
        self.assertEqual(result['documents'], 2)
        self.assertEqual(result['processed_documents'], 0)
        self.assertEqual(result['copied'], 0)

    def test_d_manifest_write_failure_preserves_old_index(self):
        self.ingest()
        old = (self.root / 'corpus/manifest.json').read_bytes()
        (self.source / 'a/guide.md').write_text('# New\nchangedviolet')
        write = Path.write_text
        def fail_manifest(path, *args, **kwargs):
            if path.name == 'manifest.json':
                raise OSError('injected manifest write failure')
            return write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', fail_manifest), self.assertRaises(OSError):
            self.ingest('--overwrite')
        self.assertEqual((self.root / 'corpus/manifest.json').read_bytes(), old)
        with KnowledgeIndex(self.root) as index:
            self.assertEqual(index.search('uniquesaffron')[0]['doc_id'], 'a/guide')
        self.assertIn('uniquesaffron', (self.root / 'docs/a/guide.md').read_text())

    def test_conflicts_require_explicit_overwrite_even_with_clean(self):
        self.ingest()
        (self.source / 'a/guide.md').write_text('# New\nchangedviolet')
        for flags in ((), ('--clean',)):
            with self.assertRaisesRegex(SystemExit, 'a/guide.md'):
                self.ingest(*flags)
        self.ingest('--overwrite')
        with KnowledgeIndex(self.root) as index:
            self.assertTrue(index.search('changedviolet'))

    def test_doc_id_collision_rejected_without_changes(self):
        self.ingest()
        (self.source / 'a/guide!.md').write_text('# Collision\ncollision')
        old = (self.root / 'corpus/manifest.json').read_bytes()
        with self.assertRaisesRegex(SystemExit, 'Duplicate doc_id'):
            self.ingest()
        self.assertEqual((self.root / 'corpus/manifest.json').read_bytes(), old)

    def test_publish_failure_rolls_back_all_files(self):
        self.ingest()
        old = (self.root / 'corpus/manifest.json').read_bytes()
        (self.source / 'a/guide.md').write_text('# New\nchangedviolet')
        replace = Path.replace
        def fail_index(path, target):
            if path.name == 'kb.sqlite3' and '.ingest-' in str(path) and 'backup' not in path.parts:
                raise OSError('injected publish failure')
            return replace(path, target)
        with patch.object(Path, 'replace', fail_index), self.assertRaises(OSError):
            self.ingest('--overwrite')
        self.assertEqual((self.root / 'corpus/manifest.json').read_bytes(), old)
        with KnowledgeIndex(self.root) as index:
            self.assertTrue(index.search('uniquesaffron'))

    def test_rebuild_manifest_write_failure_rolls_back_database(self):
        self.ingest()
        (self.root / 'docs/a/guide.md').write_text('# New\nchangedviolet')
        with KnowledgeIndex(self.root) as index:
            with patch.object(Path, 'write_text', side_effect=OSError('injected')), self.assertRaises(OSError):
                index.rebuild()
            self.assertTrue(index.search('uniquesaffron'))
            self.assertEqual(index.search('changedviolet', mode='baseline'), [])

    def test_failed_source_read_or_index_build_preserves_previous_generation(self):
        self.ingest()
        old = (self.root / 'corpus/manifest.json').read_bytes()
        with patch('src.kb.index.split_markdown', side_effect=RuntimeError('split failed')):
            (self.source / 'a/guide.md').write_text('# New\nchangedviolet')
            with self.assertRaises(RuntimeError):
                self.ingest('--overwrite')
        (self.source / 'a/guide.md').write_bytes(b'\xff')
        with self.assertRaises(UnicodeDecodeError):
            self.ingest('--overwrite')
        self.assertEqual((self.root / 'corpus/manifest.json').read_bytes(), old)
        with KnowledgeIndex(self.root) as index:
            self.assertTrue(index.search('uniquesaffron'))

    def test_clean_preserves_unrelated_files_and_removes_old_markdown(self):
        self.ingest()
        (self.root / 'docs/keep.txt').write_text('preserved')
        (self.source / 'b/guide.md').unlink()
        self.assertEqual(self.ingest('--clean')['documents'], 1)
        self.assertEqual((self.root / 'docs/keep.txt').read_text(), 'preserved')
        self.assertFalse((self.root / 'docs/b/guide.md').exists())

    def test_find_root_explicit_upward_and_no_package_fallback(self):
        from src.cli import find_root
        self.ingest()
        child = self.root / 'nested'
        child.mkdir()
        with patch('src.cli.Path.cwd', return_value=child):
            self.assertEqual(find_root(None), self.root.resolve())
            with self.assertRaises(SystemExit):
                find_root(str(self.base / 'missing'))
        with patch('src.cli.Path.cwd', return_value=self.source):
            with self.assertRaisesRegex(SystemExit, 'toolgate ingest'):
                find_root(None)
