import json
from pathlib import Path
import tempfile
import unittest

from eval.run_eval import ROOT, document_metrics, evaluate, load_cases
from src.kb.index import KnowledgeIndex


class EvaluationTests(unittest.TestCase):
    def test_document_deduplication_multiple_relevant_and_missing(self):
        metrics, ranked = document_metrics(['wrong', 'a', 'a', 'b', 'c'], ['a', 'b'])
        self.assertEqual(ranked, ['wrong', 'a', 'b', 'c'])
        self.assertEqual(metrics, {'recall@1': 0, 'recall@3': 1, 'recall@5': 1, 'mrr': 0.5})
        metrics, _ = document_metrics(['a'], ['a', 'missing'])
        self.assertEqual(metrics['recall@1'], 0.5)
        self.assertEqual(document_metrics([], ['a'])[0]['mrr'], 0)
        self.assertEqual(document_metrics(['wrong'], ['a'])[0]['recall@5'], 0)

    def test_exactly_twenty_cases_cover_all_corpus_topics(self):
        ids = {d['doc_id'] for d in json.loads((ROOT / 'corpus/manifest.json').read_text())['documents']}
        cases = load_cases(ROOT / 'eval/cases.jsonl', ids)
        self.assertEqual(len(cases), 20)
        self.assertEqual({doc for case in cases for doc in case['expected_doc_ids']}, ids)

    def test_invalid_labels_and_duplicate_queries_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cases.jsonl'
            original = [json.loads(line) for line in (ROOT / 'eval/cases.jsonl').read_text().splitlines()]
            ids = {doc for case in original for doc in case['expected_doc_ids']}
            for mutation in ('unknown', 'duplicate', 'empty', 'too_few'):
                cases = json.loads(json.dumps(original))
                if mutation == 'unknown':
                    cases[0]['expected_doc_ids'] = ['missing']
                elif mutation == 'duplicate':
                    cases[1]['query'] = cases[0]['query']
                elif mutation == 'empty':
                    cases[0]['expected_doc_ids'] = []
                else:
                    cases.pop()
                path.write_text('\n'.join(json.dumps(case) for case in cases))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    load_cases(path, ids)

    def test_real_corpus_quality_gate_and_fts_baseline(self):
        with tempfile.TemporaryDirectory() as directory, KnowledgeIndex(ROOT, Path(directory) / 'kb.sqlite3') as index:
            index.rebuild()
            ids = {row[0] for row in index.connection.execute('SELECT doc_id FROM documents')}
            cases = load_cases(ROOT / 'eval/cases.jsonl', ids)
            baseline = evaluate(index, cases, 'baseline')
            hybrid = evaluate(index, cases, 'hybrid')
        self.assertGreaterEqual(hybrid['recall@5'], 0.80)
        self.assertGreater(hybrid['recall@5'], baseline['recall@5'])
        self.assertEqual(hybrid['queries'], 20)
        self.assertGreater(hybrid['latency_ms'], 0)


if __name__ == '__main__':
    unittest.main()
