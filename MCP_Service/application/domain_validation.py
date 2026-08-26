from domain.domain_naming import normalize_domain_name
from domain.exceptions import DomainNotRegisteredError
from domain.ports.domain_repository import DomainRepository


async def ensure_domain_registered(repository: DomainRepository, domain: str) -> str:
    normalized = normalize_domain_name(domain)
    if normalized and await repository.find_by_name(normalized) is not None:
        return normalized
    registered = await repository.list_all()
    raise DomainNotRegisteredError(tuple(d.name for d in registered))
