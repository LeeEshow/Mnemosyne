import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

import config
from application.domain_validation import ensure_domain_registered
from domain.exceptions import DomainNotRegisteredError
from domain.models import Memory, MemoryContentUpdate, MemoryStatusFilter
from domain.ports.domain_repository import DomainRepository
from domain.ports.embedding_provider import EmbeddingProvider
from domain.ports.gate_classifier import GateClassifier
from domain.ports.memory_repository import MemoryRepository
from domain.write_gate_policy import (
    GateCandidate,
    GateVerdict,
    WriteGateDecision,
    decide_preliminary,
)


@dataclass(frozen=True)
class SaveMemoryRequest:
    domain: str
    title: str
    premise: str
    conclusion: str
    type: str
    tags: tuple[str, ...] | None = None
    importance_score: int | None = None


class SaveMemoryDecision(Enum):
    # 大小寫刻意不一致：逐字對齊 Proposal 定案的字面回傳值，不是疏漏。
    NOOP = "NOOP"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    ADD = "ADD"
    CONFLICT_DETECTED = "conflict_detected"
    REQUIRES_REGISTRATION = "requires_registration"


@dataclass(frozen=True)
class SaveMemoryResult:
    decision: SaveMemoryDecision
    memory_id: str | None
    registered_domains: tuple[str, ...] | None = None
    conflicting_memory: Memory | None = None
    memory: Memory | None = None


class SaveMemoryUseCase:
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider,
        gate_classifier: GateClassifier,
        domain_repository: DomainRepository,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._gate_classifier = gate_classifier
        self._domain_repository = domain_repository

    async def execute(self, request: SaveMemoryRequest) -> SaveMemoryResult:
        try:
            domain = await ensure_domain_registered(self._domain_repository, request.domain)
        except DomainNotRegisteredError as error:
            return SaveMemoryResult(
                SaveMemoryDecision.REQUIRES_REGISTRATION, None, error.registered_domains
            )
        request = replace(request, domain=domain, type=request.type.strip().lower())
        embedding = await self._embedding_provider.embed(
            _embeddable_text(request.title, request.premise, request.conclusion)
        )
        candidates = await self._gather_candidates(request.domain, embedding, request.tags)
        preliminary = decide_preliminary(request.premise, request.conclusion, candidates, config.LOW_THRESHOLD)
        if preliminary.decision == WriteGateDecision.NOOP:
            return await self._apply_noop(preliminary.matched_candidate)
        if preliminary.decision == WriteGateDecision.ADD:
            return await self._apply_add(request, embedding)
        new_memory = self._build_memory(request, embedding)
        verdict = await self._gate_classifier.classify(
            new_memory, tuple(candidate.memory for candidate in candidates)
        )
        return await self._apply_verdict(verdict, candidates, request, embedding)

    async def _gather_candidates(
        self, domain: str, embedding: tuple[float, ...], tags: tuple[str, ...] | None
    ) -> tuple[GateCandidate, ...]:
        status_filter = MemoryStatusFilter()
        vector_hits, tag_hits = await asyncio.gather(
            self._repository.find_nearest(domain, embedding, config.WRITE_GATE_CANDIDATE_LIMIT, status_filter),
            self._find_by_tags_if_any(domain, tags, status_filter),
        )
        merged: dict[str, GateCandidate] = {
            scored.memory.id: GateCandidate(scored.memory, scored.similarity) for scored in vector_hits
        }
        for memory in tag_hits:
            existing = merged.get(memory.id)
            if existing is not None:
                merged[memory.id] = replace(existing, is_tag_hit=True)
            else:
                merged[memory.id] = GateCandidate(memory, None, is_tag_hit=True)
        return tuple(merged.values())

    async def _find_by_tags_if_any(
        self, domain: str, tags: tuple[str, ...] | None, status_filter: MemoryStatusFilter
    ) -> list[Memory]:
        if not tags:
            return []
        return await self._repository.find_by_tags(domain, tags, status_filter)

    async def _apply_noop(self, candidate: GateCandidate) -> SaveMemoryResult:
        await self._repository.record_access(candidate.memory.id)
        return SaveMemoryResult(SaveMemoryDecision.NOOP, candidate.memory.id, memory=candidate.memory)

    async def _apply_add(
        self, request: SaveMemoryRequest, embedding: tuple[float, ...]
    ) -> SaveMemoryResult:
        saved = await self._repository.save(self._build_memory(request, embedding))
        return SaveMemoryResult(SaveMemoryDecision.ADD, saved.id, memory=saved)

    async def _apply_verdict(
        self,
        verdict: GateVerdict,
        candidates: tuple[GateCandidate, ...],
        request: SaveMemoryRequest,
        embedding: tuple[float, ...],
    ) -> SaveMemoryResult:
        if verdict.decision == WriteGateDecision.ADD:
            return await self._apply_add(request, embedding)
        matched = self._find_candidate(candidates, verdict.matched_memory_id)
        if verdict.decision == WriteGateDecision.NOOP:
            return await self._apply_noop(matched)
        if verdict.decision == WriteGateDecision.UPDATE:
            return await self._apply_update(verdict, matched, request)
        if verdict.decision == WriteGateDecision.SUPERSEDE:
            return await self._apply_supersede(verdict, request, matched)
        return SaveMemoryResult(SaveMemoryDecision.CONFLICT_DETECTED, None, conflicting_memory=matched.memory)

    def _find_candidate(self, candidates: tuple[GateCandidate, ...], memory_id: str | None) -> GateCandidate:
        for candidate in candidates:
            if candidate.memory.id == memory_id:
                return candidate
        raise ValueError(f"寫入閘門判定回傳的 matched_memory_id={memory_id!r} 不在候選名單中")

    async def _apply_update(
        self, verdict: GateVerdict, candidate: GateCandidate, request: SaveMemoryRequest
    ) -> SaveMemoryResult:
        memory = candidate.memory
        title = verdict.merged_title or memory.title
        premise = verdict.merged_premise or memory.premise
        conclusion = verdict.merged_conclusion or memory.conclusion
        tags = _merge_tags(memory.tags, request.tags)
        embedding = await self._embedding_provider.embed(_embeddable_text(title, premise, conclusion))
        await self._repository.overwrite_content(
            memory.id, MemoryContentUpdate(title, premise, conclusion, tags, embedding)
        )
        updated_memory = replace(
            memory, title=title, premise=premise, conclusion=conclusion, tags=tags, embedding=embedding
        )
        return SaveMemoryResult(SaveMemoryDecision.UPDATE, memory.id, memory=updated_memory)

    async def _apply_supersede(
        self, verdict: GateVerdict, request: SaveMemoryRequest, candidate: GateCandidate
    ) -> SaveMemoryResult:
        old_memory = candidate.memory
        title = verdict.merged_title or request.title
        premise = verdict.merged_premise or request.premise
        conclusion = verdict.merged_conclusion or request.conclusion
        tags = _merge_tags(old_memory.tags, request.tags)
        embedding = await self._embedding_provider.embed(_embeddable_text(title, premise, conclusion))
        merged_request = replace(request, title=title, premise=premise, conclusion=conclusion, tags=tags)
        new_memory = replace(
            self._build_memory(merged_request, embedding),
            is_pinned=old_memory.is_pinned,
            access_count=old_memory.access_count,
        )
        saved = await self._repository.save(new_memory)
        await self._repository.mark_superseded(old_memory.id, saved.id)
        return SaveMemoryResult(SaveMemoryDecision.SUPERSEDE, saved.id, memory=saved)

    def _build_memory(self, request: SaveMemoryRequest, embedding: tuple[float, ...]) -> Memory:
        return Memory(
            type=request.type,
            domain=request.domain,
            title=request.title,
            premise=request.premise,
            conclusion=request.conclusion,
            embedding=embedding,
            created_at=datetime.now(timezone.utc),
            importance_score=request.importance_score or config.DEFAULT_IMPORTANCE_SCORE,
            tags=request.tags,
        )


def _embeddable_text(title: str, premise: str, conclusion: str) -> str:
    return f"{title}\n{premise}\n{conclusion}"


def _merge_tags(old_tags: tuple[str, ...] | None, new_tags: tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(sorted(set(old_tags or ()) | set(new_tags or ())))
