from dataclasses import dataclass
from enum import Enum

from domain.models import ScoredMemory


class WriteGateDecision(Enum):
    NOOP = "NOOP"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    ADD = "ADD"


@dataclass(frozen=True)
class GateVerdict:
    decision: WriteGateDecision
    merged_title: str | None = None
    merged_context: str | None = None


def normalize_for_exact_match(text: str) -> str:
    return " ".join(text.strip().lower().split())


def decide_preliminary(
    new_context: str, nearest: ScoredMemory | None, low_threshold: float
) -> WriteGateDecision | None:
    if nearest is None:
        return WriteGateDecision.ADD
    if normalize_for_exact_match(new_context) == normalize_for_exact_match(nearest.memory.context):
        return WriteGateDecision.NOOP
    if nearest.similarity < low_threshold:
        return WriteGateDecision.ADD
    return None
