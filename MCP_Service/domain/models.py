from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MemoryStatus(Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Memory:
    type: str
    domain: str
    title: str
    premise: str
    conclusion: str
    embedding: tuple[float, ...]
    created_at: datetime
    importance_score: int
    id: str | None = None
    is_pinned: bool = False
    status: MemoryStatus = MemoryStatus.ACTIVE
    tags: tuple[str, ...] | None = None
    superseded_by: str | None = None
    access_count: int | None = None


@dataclass(frozen=True)
class ScoredMemory:
    memory: Memory
    similarity: float


@dataclass(frozen=True)
class MemoryStatusFilter:
    include_superseded: bool = False
    include_archived: bool = False

    def allowed_statuses(self) -> tuple[MemoryStatus, ...]:
        statuses = [MemoryStatus.ACTIVE]
        if self.include_superseded:
            statuses.append(MemoryStatus.SUPERSEDED)
        if self.include_archived:
            statuses.append(MemoryStatus.ARCHIVED)
        return tuple(statuses)


@dataclass(frozen=True)
class Domain:
    name: str
    description: str
    created_at: datetime
