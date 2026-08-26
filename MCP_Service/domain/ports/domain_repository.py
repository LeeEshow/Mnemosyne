from typing import Protocol

from domain.models import Domain


class DomainRepository(Protocol):
    async def find_by_name(self, name: str) -> Domain | None: ...

    async def list_all(self) -> list[Domain]: ...

    async def save(self, domain: Domain) -> Domain: ...
