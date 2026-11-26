"""Offline baseline: hashed character n-grams with corpus TF-IDF weights.

Production may replace this baseline with bge-m3. No network or model required.
"""

from collections import Counter
import hashlib
import math
import re

DIMENSIONS = 256


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def features(text: str) -> dict[int, int]:
    text = normalize(text)
    counts = Counter()
    for size in (2, 3):
        for start in range(len(text) - size + 1):
            gram = text[start:start + size]
            bucket = int.from_bytes(hashlib.sha256(gram.encode()).digest()[:8], "big") % DIMENSIONS
            counts[bucket] += 1
    return dict(counts)


class Embedder:
    def __init__(self, document_count: int = 0, document_frequency: dict | None = None):
        self.idf = [math.log((1 + document_count) / (1 + (document_frequency or {}).get(str(i), 0))) + 1
                    for i in range(DIMENSIONS)]

    def transform(self, counts: dict) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for bucket, count in counts.items():
            vector[int(bucket)] = (1 + math.log(count)) * self.idf[int(bucket)]
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, text: str) -> list[float]:
        return self.transform(features(text))


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
