from dataclasses import dataclass
from datetime import datetime, timezone

from domain.domain_naming import normalize_domain_name
from domain.models import Domain
from domain.ports.domain_repository import DomainRepository


@dataclass(frozen=True)
class RegisterDomainRequest:
    name: str
    description: str


@dataclass(frozen=True)
class RegisterDomainResult:
    domain: Domain
    already_registered: bool


class RegisterDomainUseCase:
    def __init__(self, repository: DomainRepository) -> None:
        self._repository = repository

    async def execute(self, request: RegisterDomainRequest) -> RegisterDomainResult:
        name = normalize_domain_name(request.name)
        if not name:
            raise ValueError("domain 名稱正規化（去除前後空白、轉小寫）後不可為空白字串")
        existing = await self._repository.find_by_name(name)
        if existing is not None:
            return RegisterDomainResult(existing, already_registered=True)
        domain = Domain(name=name, description=request.description, created_at=datetime.now(timezone.utc))
        saved = await self._repository.save(domain)
        return RegisterDomainResult(saved, already_registered=False)
