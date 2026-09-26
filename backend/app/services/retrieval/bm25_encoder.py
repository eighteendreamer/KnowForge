import hashlib
import math
import re
import unicodedata
from collections import Counter

import jieba
from qdrant_client.models import SparseVector

from app.services.retrieval.normalize import drop_stopwords, to_simplified

SEGMENTER = jieba.Tokenizer()
WORD = re.compile(r"[\w+#-]+", re.UNICODE)


def terms(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [
        word
        for token in SEGMENTER.cut(normalized, HMM=False)
        for word in WORD.findall(token)
        if word.strip("_-+")
    ]


def term_id(term: str) -> int:
    return int.from_bytes(hashlib.blake2s(term.encode(), digest_size=4).digest(), "big")


def warm_segmenter() -> None:
    """Build jieba's prefix dictionary ahead of traffic; the first cut costs ~0.45s of event-loop time."""
    terms("缓存穿透")


def bm25_vector(text: str, *, query: bool = False, average_length: float = 600) -> SparseVector:
    # 归一只作用在查询侧：文档侧的词形已经写进 Qdrant，改它会要求一次全量重建才能对齐。
    tokens = drop_stopwords(terms(to_simplified(text))) if query else terms(text)
    frequencies = Counter(tokens)
    weights: dict[int, float] = {}
    for token, frequency in frequencies.items():
        index = term_id(token)
        weight = (
            1.0
            if query
            else frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * len(tokens) / average_length))
        )
        weights[index] = weights.get(index, 0) + weight
    indices = sorted(weights)
    if not all(math.isfinite(value) for value in weights.values()):
        raise ValueError("Invalid sparse weight")
    return SparseVector(indices=indices, values=[weights[index] for index in indices])
