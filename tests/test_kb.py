import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.kb.embedder import Embedder, cosine, features
from src.kb.index import KnowledgeIndex, ROOT, rrf
from src.kb.splitter import split_markdown


class SplitterTests(unittest.TestCase):
    def test_heading_hierarchy_and_metadata(self):
        blocks = split_markdown('doc', '# Root\n## First\n正文\n### Deep\n细节\n## Second\n结尾')
        self.assertEqual([b.heading_path for b in blocks], [('Root', 'First'), ('Root', 'First', 'Deep'), ('Root', 'Second')])
        self.assertEqual([b.block_index for b in blocks], [0, 1, 2])
        self.assertTrue(all(b.doc_id == 'doc' for b in blocks))

    def test_exact_limit_and_long_unicode_paragraph(self):
        for size in (599, 600, 601, 1201):
            with self.subTest(size=size):
                blocks = split_markdown('doc', '# Title\n' + '文' * size)
                self.assertTrue(all(0 < len(b.text) <= 600 for b in blocks))
                self.assertEqual(''.join(b.text for b in blocks), '文' * size)
                self.assertEqual(len(blocks), math.ceil(size / 600))

    def test_paragraph_boundary_preferred(self):
        blocks = split_markdown('d', '前' * 350 + '\n\n' + '后' * 350)
        self.assertEqual([b.text for b in blocks], ['前' * 350, '后' * 350])

    def test_empty_preamble_and_code_fence(self):
        self.assertEqual(split_markdown('d', '# Empty\n## Also empty'), [])
        blocks = split_markdown('d', '前言\n# Real\n```text\n## Not a heading\n```\n正文')
        self.assertEqual(blocks[0].heading_path, ())
        self.assertEqual(blocks[1].heading_path, ('Real',))
        self.assertIn('## Not a heading', blocks[1].text)
        with self.assertRaises(ValueError):
            split_markdown('d', 'x', 0)


class EmbeddingTests(unittest.TestCase):
    def test_dimensions_norm_cosine_and_empty(self):
        embedder = Embedder()
        vector = embedder.embed('混合检索 重排')
        self.assertEqual(len(vector), 256)
        self.assertAlmostEqual(sum(x*x for x in vector), 1)
        self.assertAlmostEqual(cosine(vector, vector), 1)
        self.assertEqual(embedder.embed(''), [0.0] * 256)
        self.assertEqual(sum(features('abcd').values()), 5)  # 3 bigrams + 2 trigrams

    def test_tfidf_weighting(self):
        embedder = Embedder(9, {'0': 9, '1': 0})
        vector = embedder.transform({0: 2, 1: 2})
        self.assertGreater(vector[1], vector[0])
        self.assertAlmostEqual(vector[1] / vector[0], math.log(10) + 1)

    def test_cross_process_determinism(self):
        command = [sys.executable, '-c', 'import json; from src.kb.embedder import Embedder; print(json.dumps(Embedder().embed("混合检索 重排")))']
        output = subprocess.check_output(command, cwd=ROOT, text=True)
        self.assertEqual(json.loads(output), Embedder().embed('混合检索 重排'))

    def test_rrf_exact_contributions(self):
        scores = rrf([[10, 20], [20, 30]])
        self.assertAlmostEqual(scores[20], 1/62 + 1/61)
        self.assertAlmostEqual(scores[10], 1/61)
        self.assertEqual(max(scores, key=scores.get), 20)


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docs').mkdir()
        (self.root / 'corpus').mkdir()
        self.entries = []
        self.add_doc('hybrid', '# 混合检索与重排\n## RRF\n混合检索将全文与向量候选融合，重排解释标题命中和覆盖率。')
        self.add_doc('retry', '# 失败重试与退避\n## 预算\n指数退避与随机抖动限制重复请求，超时应停止重试。')
        self.add_doc('other', '# 日志\n## 记录\n正文提及混合检索和重排，但主题是事件记录。')
        self.write_manifest()
        self.index = KnowledgeIndex(self.root)
        self.addCleanup(self.index.close)
        self.index.rebuild()

    def add_doc(self, doc_id, text):
        path = self.root / 'docs' / f'{doc_id}.md'
        path.write_text(text, encoding='utf-8')
        self.entries.append({'doc_id': doc_id, 'path': f'docs/{doc_id}.md',
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'mtime_ns': path.stat().st_mtime_ns, 'tags': ['test']})

    def write_manifest(self):
        (self.root / 'corpus' / 'manifest.json').write_text(json.dumps({'documents': self.entries}), encoding='utf-8')

    def test_stats_and_fts5_table(self):
        stats = self.index.stats()
        self.assertEqual(stats['documents'], 3)
        self.assertEqual(stats['blocks'], 3)
        self.assertGreater(stats['index_size_bytes'], 0)
        schema = self.index.connection.execute("SELECT sql FROM sqlite_master WHERE name='blocks_fts'").fetchone()[0]
        self.assertIn('fts5', schema)

    def test_hybrid_ranking_and_explainable_score(self):
        results = self.index.search('混合检索 重排')
        self.assertEqual(results[0]['doc_id'], 'hybrid')
        self.assertEqual(len(results), 2)
        detail = results[0]['score_detail']
        self.assertIsNotNone(detail['fts_rank'])
        self.assertIsNotNone(detail['vector_rank'])
        self.assertAlmostEqual(detail['rrf'], 1/(60+detail['fts_rank']) + 1/(60+detail['vector_rank']))
        self.assertAlmostEqual(results[0]['score'], detail['rrf'] + detail['title_boost'] + detail['coverage_boost'])
        self.assertEqual(detail['term_coverage'], 1)
        self.assertEqual(self.index.search('混合检索 重排'), results)

    def test_special_characters_never_reach_fts_syntax(self):
        for query in ('混合检索/重排', '混合检索:重排', '"混合检索" (重排)', '混合检索 OR NOT : / " () * -', '"', '/:()"', '\x00'):
            with self.subTest(query=query):
                self.assertIsInstance(self.index.search(query), list)
        self.assertEqual(self.index.search('混合检索/重排')[0]['doc_id'], 'hybrid')

    def test_empty_and_no_result(self):
        for query in ('', '   ', '不存在的量子香蕉', 'zzzzzzzzzzzz'):
            self.assertEqual(self.index.search(query), [])
        self.assertEqual(self.index.search('重排', limit=0), [])

    def test_vector_channel_recalls_partial_chinese_term(self):
        # No full FTS token matches, but character fragments still provide evidence.
        results = self.index.search('全文与向量候选的融合')
        self.assertTrue(results)
        self.assertIn('hybrid', [row['doc_id'] for row in results])
        self.assertTrue(any(row['score_detail']['fts_rank'] is None for row in results))

    def test_baseline_only_uses_fts(self):
        results = self.index.search('重排', mode='baseline')
        self.assertTrue(results)
        self.assertTrue(all(r['score_detail']['vector_rank'] is None for r in results))
        self.assertTrue(all(r['score'] == r['score_detail']['rrf'] for r in results))

    def test_second_rebuild_skips_all_processing(self):
        before = list(self.index.connection.execute('SELECT id,features FROM blocks'))
        with patch('src.kb.index.split_markdown', side_effect=AssertionError('unchanged split')), patch('src.kb.index.features', side_effect=AssertionError('unchanged embedding')):
            result = self.index.rebuild()
        self.assertEqual(result['processed_documents'], 0)
        self.assertEqual(result['unchanged_documents'], 3)
        self.assertEqual([tuple(row) for row in before], [tuple(row) for row in self.index.connection.execute('SELECT id,features FROM blocks')])

    def test_changed_added_deleted_documents_and_idf_refresh(self):
        (self.root / 'docs' / 'retry.md').write_text('# 新主题\n## 独立\n独特标记回归验证。', encoding='utf-8')
        self.entries = [entry for entry in self.entries if entry['doc_id'] != 'other']
        self.add_doc('new', '# 新增文档\n## 新术语\n增量写入应成功。')
        self.write_manifest()
        with patch('src.kb.index.split_markdown', wraps=split_markdown) as split:
            result = self.index.rebuild()
        self.assertEqual(split.call_count, 2)
        self.assertEqual(result['processed_documents'], 2)
        self.assertEqual(result['deleted_documents'], 1)
        self.assertEqual(result['unchanged_documents'], 1)
        self.assertEqual(self.index.search('独特标记')[0]['doc_id'], 'retry')
        self.assertEqual(self.index.search('退避', mode='baseline'), [])
        self.assertNotIn('other', [r['doc_id'] for r in self.index.search('重排')])
        count, df = json.loads(self.index.connection.execute("SELECT value FROM metadata WHERE key='idf'").fetchone()[0])
        self.assertEqual(count, self.index.stats()['blocks'])
        self.assertTrue(all(value <= count for value in df.values()))

    def test_touch_does_not_reprocess_and_hash_detects_same_mtime_edit(self):
        import os
        path = self.root / 'docs' / 'retry.md'
        initial = path.stat().st_mtime_ns
        os.utime(path, ns=(initial + 1000000, initial + 1000000))
        self.assertEqual(self.index.rebuild()['processed_documents'], 0)
        current = path.stat().st_mtime_ns
        path.write_text('# 修改\n## 内容\n新的退避说明。', encoding='utf-8')
        os.utime(path, ns=(current, current))
        self.assertEqual(self.index.rebuild()['processed_documents'], 1)
        snapshot = json.loads((self.root / 'corpus' / 'manifest.json').read_text())
        record = next(entry for entry in snapshot['documents'] if entry['doc_id'] == 'retry')
        self.assertEqual(record['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(record['mtime_ns'], path.stat().st_mtime_ns)

    def test_invalid_manifest_keeps_existing_index(self):
        self.entries[0]['path'] = '../outside.md'
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.index.rebuild()
        self.assertEqual(self.index.stats()['documents'], 3)
        self.assertEqual(self.index.search('混合检索')[0]['doc_id'], 'hybrid')

    def test_failed_rebuild_rolls_back(self):
        path = self.root / 'docs' / 'retry.md'
        path.write_text('# changed\nnew text', encoding='utf-8')
        with patch('src.kb.index.split_markdown', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                self.index.rebuild()
        self.assertTrue(self.index.search('退避', mode='baseline'))
        self.assertEqual(self.index.stats()['blocks'], 3)


class CorpusTests(unittest.TestCase):
    def test_authored_corpus_structure_and_manifest(self):
        manifest = json.loads((ROOT / 'corpus' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(20 <= len(manifest['documents']) <= 30)
        paths = set()
        for entry in manifest['documents']:
            path = ROOT / entry['path']
            text = path.read_text(encoding='utf-8')
            self.assertTrue(300 <= len(text) <= 800, path.name)
            self.assertTrue(text.startswith('# '))
            self.assertGreaterEqual(text.count('\n## '), 3)
            self.assertGreaterEqual(len(entry['tags']), 5)
            self.assertEqual(entry['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            # A fresh checkout may change filesystem timestamps; content hashes
            # remain authoritative and rebuild refreshes the observed mtime.
            self.assertIsInstance(entry['mtime_ns'], int)
            self.assertNotIn('://', text)
            paths.add(path)
        self.assertEqual(paths, set((ROOT / 'docs').glob('*.md')))


if __name__ == '__main__':
    unittest.main()
