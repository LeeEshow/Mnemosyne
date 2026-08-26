from dataclasses import dataclass
from enum import Enum

from domain.models import Memory


class WriteGateDecision(Enum):
    NOOP = "NOOP"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    ADD = "ADD"


@dataclass(frozen=True)
class GateCandidate:
    memory: Memory
    similarity: float | None
    is_tag_hit: bool = False
    """`similarity=None` 表示未透過向量軌道命中。`is_tag_hit=True` 表示同時透過標籤交集軌道（軌道 B）
    命中，此時無論 similarity 高低都不可判定為 ADD——需獨立於 similarity 記錄，因為同一候選可能同時被
    兩軌命中，此時 similarity 仍是向量軌道的真實分數，不能用它是否為 None 來判斷有沒有標籤交集
    （見 Proposal 5.1 軌道 B）。"""


@dataclass(frozen=True)
class GateVerdict:
    decision: WriteGateDecision
    matched_memory_id: str | None = None
    merged_title: str | None = None
    merged_premise: str | None = None
    merged_conclusion: str | None = None


@dataclass(frozen=True)
class PreliminaryVerdict:
    decision: WriteGateDecision | None
    """None 表示無法初步判定，需呼叫 LLM。"""
    matched_candidate: GateCandidate | None = None


def normalize_for_exact_match(text: str) -> str:
    return " ".join(text.strip().lower().split())


def decide_preliminary(
    new_premise: str,
    new_conclusion: str,
    candidates: tuple[GateCandidate, ...],
    low_threshold: float,
) -> PreliminaryVerdict:
    if not candidates:
        return PreliminaryVerdict(WriteGateDecision.ADD)
    exact_match = _find_exact_match(new_premise, new_conclusion, candidates)
    if exact_match is not None:
        return PreliminaryVerdict(WriteGateDecision.NOOP, exact_match)
    if _all_below_threshold_without_tag_hit(candidates, low_threshold):
        return PreliminaryVerdict(WriteGateDecision.ADD)
    return PreliminaryVerdict(None)


def _find_exact_match(
    new_premise: str, new_conclusion: str, candidates: tuple[GateCandidate, ...]
) -> GateCandidate | None:
    for candidate in candidates:
        if _is_exact_match(new_premise, new_conclusion, candidate.memory):
            return candidate
    return None


def _all_below_threshold_without_tag_hit(
    candidates: tuple[GateCandidate, ...], low_threshold: float
) -> bool:
    return all(
        not candidate.is_tag_hit
        and candidate.similarity is not None
        and candidate.similarity < low_threshold
        for candidate in candidates
    )


def _is_exact_match(new_premise: str, new_conclusion: str, existing: Memory) -> bool:
    return (
        normalize_for_exact_match(new_premise) == normalize_for_exact_match(existing.premise)
        and normalize_for_exact_match(new_conclusion) == normalize_for_exact_match(existing.conclusion)
    )
