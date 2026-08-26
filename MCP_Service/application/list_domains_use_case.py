from domain.models import Domain
from domain.ports.domain_repository import DomainRepository


class ListDomainsUseCase:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def execute(self) -> tuple[Domain, ...]:
        domains = await self._repository.list_all()
        return tuple(domains)
