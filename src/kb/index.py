"""Incremental SQLite FTS5 + offline vector retrieval and explainable reranking."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .embedder import Embedder, cosine, features, normalize
from .splitter import split_markdown

ROOT = Path(__file__).resolve().parents[2]


def query_terms(query: str) -> list[str]:
    # FTS syntax never reaches MATCH: extract literal Unicode words only.
    return list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold())))


def fts_tokens(text: str) -> str:
    tokens = []
    for term in query_terms(text):
        tokens.append(term)
        if re.search(r"[\u3400-\u9fff]", term):
            tokens.extend(term[i:i + n] for n in (1, 2, 3) for i in range(len(term) - n + 1))
    return " ".join(tokens)


def rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    scores = Counter()
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            scores[key] += 1 / (k + rank)
    return dict(scores)


class KnowledgeIndex:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = Path(root).resolve()
        self.db_path = Path(db_path) if db_path else self.root / "out" / "kb.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, mtime_ns INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS blocks (
                id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, heading_path TEXT NOT NULL,
                block_index INTEGER NOT NULL, text TEXT NOT NULL, features TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(tokens);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def rebuild(self) -> dict:
        manifest_path = self.root / "corpus" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["documents"]
        ids = [entry["doc_id"] for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate doc_id in manifest")
        # Read and validate all sources before any database mutation.
        sources = []
        manifest_changed = False
        for entry in entries:
            path = (self.root / entry["path"]).resolve()
            if not path.is_relative_to(self.root / "docs") or path.suffix != ".md":
                raise ValueError("Corpus paths must point to Markdown under docs/")
            raw = path.read_bytes()
            digest, mtime = hashlib.sha256(raw).hexdigest(), path.stat().st_mtime_ns
            if entry.get("sha256") != digest or entry.get("mtime_ns") != mtime:
                entry.update(sha256=digest, mtime_ns=mtime)
                manifest_changed = True
            sources.append((entry, raw.decode("utf-8")))
        previous = {row["doc_id"]: row for row in self.connection.execute("SELECT * FROM documents")}
        removed = set(previous) - set(ids)
        changed = skipped = 0
        with self.connection:
            for doc_id in removed:
                self._delete(doc_id)
            for entry, content in sources:
                doc_id = entry["doc_id"]
                digest, mtime = entry["sha256"], entry["mtime_ns"]
                old = previous.get(doc_id)
                if old and old["sha256"] == digest and old["path"] == entry["path"]:
                    skipped += 1
                    if old["mtime_ns"] != mtime:
                        self.connection.execute("UPDATE documents SET mtime_ns=? WHERE doc_id=?", (mtime, doc_id))
                    continue
                self._delete(doc_id)
                self.connection.execute("INSERT INTO documents VALUES (?,?,?,?)", (doc_id, entry["path"], digest, mtime))
                for block in split_markdown(doc_id, content):
                    heading = " / ".join(block.heading_path)
                    searchable = heading + "\n" + block.text
                    cursor = self.connection.execute(
                        "INSERT INTO blocks(doc_id,heading_path,block_index,text,features) VALUES (?,?,?,?,?)",
                        (doc_id, json.dumps(block.heading_path, ensure_ascii=False), block.block_index,
                         block.text, json.dumps(features(searchable))))
                    self.connection.execute("INSERT INTO blocks_fts(rowid,tokens) VALUES (?,?)", (cursor.lastrowid, fts_tokens(searchable)))
                changed += 1
            # Reuse saved term counts; refresh global IDF without re-splitting or
            # re-embedding unchanged documents. L2 vectors are materialized at search.
            if changed or removed:
                df = Counter()
                count = 0
                for row in self.connection.execute("SELECT features FROM blocks"):
                    df.update(json.loads(row[0]).keys())
                    count += 1
                self.connection.execute("INSERT OR REPLACE INTO metadata VALUES ('idf',?)", (json.dumps([count, dict(df)]),))
        if manifest_changed:
            # Publish the verified snapshot only after a successful index update.
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(manifest_path)
        return {"processed_documents": changed, "unchanged_documents": skipped, "deleted_documents": len(removed), **self.stats()}

    def _delete(self, doc_id):
        self.connection.execute("DELETE FROM blocks_fts WHERE rowid IN (SELECT id FROM blocks WHERE doc_id=?)", (doc_id,))
        self.connection.execute("DELETE FROM blocks WHERE doc_id=?", (doc_id,))
        self.connection.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

    def stats(self) -> dict:
        return {"documents": self.connection.execute("SELECT count(*) FROM documents").fetchone()[0],
                "blocks": self.connection.execute("SELECT count(*) FROM blocks").fetchone()[0],
                "index_size_bytes": self.db_path.stat().st_size}

    def search(self, query: str, limit: int = 3, mode: str = "hybrid") -> list[dict]:
        if mode not in ("hybrid", "baseline"):
            raise ValueError("mode must be hybrid or baseline")
        terms = query_terms(query)
        if not terms or limit < 1:
            return []
        rows = {row["id"]: row for row in self.connection.execute("SELECT * FROM blocks")}
        # Quoted OR terms are literal and safe even for malformed FTS expressions.
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        lexical = [row[0] for row in self.connection.execute(
            "SELECT rowid FROM blocks_fts WHERE blocks_fts MATCH ? ORDER BY bm25(blocks_fts),rowid LIMIT 100", (expression,))]
        if mode == "baseline":
            fused = rrf([lexical])
            vector_scores = {}
            semantic = []
        else:
            meta = self.connection.execute("SELECT value FROM metadata WHERE key='idf'").fetchone()
            embedder = Embedder(*json.loads(meta[0])) if meta else Embedder()
            query_vector = embedder.embed(" ".join(terms))
            vector_scores = {key: cosine(query_vector, embedder.transform(json.loads(row["features"])))
                             for key, row in rows.items()}
            semantic = sorted((key for key in rows if vector_scores[key] > 0),
                              key=lambda key: (-vector_scores[key], key))[:100]
            fused = rrf([lexical, semantic])
        lexical_ranks = {key: rank for rank, key in enumerate(lexical, 1)}
        vector_ranks = {key: rank for rank, key in enumerate(semantic, 1)}
        results = []
        for key, fusion in fused.items():
            row = rows[key]
            heading = json.loads(row["heading_path"])
            title = normalize(" ".join(heading))
            body = normalize(row["text"])
            title_hit = sum(term in title for term in terms) / len(terms)
            coverage = sum(term in title or term in body for term in terms) / len(terms)
            # Hash collisions alone are not evidence of relevance. Require a literal
            # term or character bigram shared with the source before reranking.
            shared = any(term in title + body or (len(term) > 1 and any(
                term[i:i+2] in title + body for i in range(len(term)-1))) for term in terms)
            if not shared:
                continue
            score = fusion + (0.04 * title_hit + 0.02 * coverage if mode == "hybrid" else 0)
            results.append({"doc_id": row["doc_id"], "heading_path": heading,
                            "block_index": row["block_index"], "text": row["text"], "score": score,
                            "score_detail": {"fts_rank": lexical_ranks.get(key), "vector_rank": vector_ranks.get(key),
                                             "cosine": vector_scores.get(key, 0), "rrf": fusion,
                                             "title_hit": title_hit, "term_coverage": coverage,
                                             "title_boost": 0.04 * title_hit if mode == "hybrid" else 0,
                                             "coverage_boost": 0.02 * coverage if mode == "hybrid" else 0}})
        results.sort(key=lambda result: (-result["score"], result["doc_id"], result["block_index"]))
        return results[:limit]
