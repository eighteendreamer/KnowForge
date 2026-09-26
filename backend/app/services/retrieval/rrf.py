from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    score: float


def reciprocal_rank_fusion(
    rankings: list[list[Candidate]], limit: int = 50, constant: int = 60
) -> list[Candidate]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        seen = set()
        for rank, candidate in enumerate(ranking, 1):
            if candidate.chunk_id not in seen:
                scores[candidate.chunk_id] += 1 / (constant + rank)
                seen.add(candidate.chunk_id)
    return [
        Candidate(chunk_id, score)
        for chunk_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
