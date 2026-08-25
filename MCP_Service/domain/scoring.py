import math
from dataclasses import dataclass
from datetime import datetime

from domain.models import ScoredMemory

_SECONDS_PER_DAY = 86400
_IMPORTANCE_SCORE_MIN = 1
_IMPORTANCE_SCORE_MAX = 10


@dataclass(frozen=True)
class ScoringWeights:
    relevance: float
    importance: float
    recency: float
    access_frequency: float


@dataclass(frozen=True)
class ScoringParameters:
    weights: ScoringWeights
    decay_lambda: float
    access_frequency_log_cap: float


def calculate_decay_score(
    scored_memory: ScoredMemory, now: datetime, parameters: ScoringParameters
) -> float:
    memory = scored_memory.memory
    weights = parameters.weights
    return (
        weights.relevance * scored_memory.similarity
        + weights.importance * _normalize_importance(memory.importance_score)
        + weights.recency * _calculate_recency(memory.created_at, now, parameters.decay_lambda)
        + weights.access_frequency
        * _calculate_access_frequency(memory.access_count, parameters.access_frequency_log_cap)
    )


def _normalize_importance(importance_score: int) -> float:
    score_range = _IMPORTANCE_SCORE_MAX - _IMPORTANCE_SCORE_MIN
    return (importance_score - _IMPORTANCE_SCORE_MIN) / score_range


def _calculate_recency(created_at: datetime, now: datetime, decay_lambda: float) -> float:
    delta_days = (now - created_at).total_seconds() / _SECONDS_PER_DAY
    delta_days = max(0.0, delta_days)  # 系統時鐘回調時避免負值讓 exp() 超過 1.0
    return math.exp(-decay_lambda * delta_days)


def _calculate_access_frequency(access_count: int | None, log_cap: float) -> float:
    count = access_count or 0
    return min(math.log1p(count) / math.log1p(log_cap), 1.0)
