from dataclasses import dataclass

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
        if request.hard_delete:
            await self._repository.delete(request.memory_id)
            return
        await self._repository.set_status(request.memory_id, MemoryStatus.ARCHIVED)
