from dataclasses import dataclass

from application.superseded_resolution import resolve_active_memory_id
from domain.models import MemoryStatus
from domain.ports.memory_repository import MemoryRepository


@dataclass(frozen=True)
class ForgetMemoryRequest:
    memory_id: str
    hard_delete: bool = False


class ForgetMemoryUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, request: ForgetMemoryRequest) -> None:
        active_id = await resolve_active_memory_id(self._repository, request.memory_id)
        if request.hard_delete:
            await self._repository.delete(active_id)
            return
        await self._repository.set_status(active_id, MemoryStatus.ARCHIVED)
