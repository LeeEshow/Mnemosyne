import os
from dataclasses import dataclass
from functools import lru_cache

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

import config
from application.forget_memory_use_case import ForgetMemoryRequest, ForgetMemoryUseCase
from application.list_domains_use_case import ListDomainsUseCase
from application.load_pinned_memories_use_case import (
    LoadPinnedMemoriesRequest,
    LoadPinnedMemoriesUseCase,
)
from application.pin_memory_use_case import PinMemoryUseCase
from application.register_domain_use_case import RegisterDomainRequest, RegisterDomainUseCase
from application.save_memory_use_case import SaveMemoryRequest, SaveMemoryUseCase
from application.search_memories_use_case import SearchMemoriesRequest, SearchMemoriesUseCase
from domain.exceptions import DomainNotRegisteredError
from domain.models import Domain, Memory
from infrastructure.firestore_domain_repository import FirestoreDomainRepository
from infrastructure.firestore_memory_repository import FirestoreMemoryRepository
from infrastructure.gemini_gate_classifier import GeminiGateClassifier
from infrastructure.vertex_embedding_provider import VertexEmbeddingProvider
from interface import key_auth_middleware, tool_schemas

_INSTRUCTIONS = """\
Mnemosyne long-term memory rules:

CONFLICT: When save_memory returns decision="conflict_detected", immediately pause all actions. \
Present both the existing and new memory to the user and ask whether to overwrite or keep both. \
Do NOT call forget_memory or retry save_memory until you have an explicit answer from the user.

DOMAIN REGISTRATION: When save_memory returns decision="requires_registration", or when \
search_memories/load_pinned_memories raises an error about an unregistered domain, immediately pause. \
Explain to the user that this is a new domain, describe its proposed scope, and get explicit \
confirmation before calling register_domain. Do NOT auto-register or substitute an existing domain \
without confirmation.

SEARCH: An unknown domain raises an error (not an empty result); the error message includes the \
registered domain list. An empty search result means no matching memories were found — it does not \
mean the user never mentioned the topic.

LOAD PINNED: Call load_pinned_memories once at the start of each session, not repeatedly.
"""

mcp_server = MCPServer("mnemosyne", instructions=_INSTRUCTIONS)


@dataclass(frozen=True)
class _Dependencies:
    save_memory: SaveMemoryUseCase
    search_memories: SearchMemoriesUseCase
    forget_memory: ForgetMemoryUseCase
    pin_memory: PinMemoryUseCase
    load_pinned_memories: LoadPinnedMemoriesUseCase
    list_domains: ListDomainsUseCase
    register_domain: RegisterDomainUseCase


@lru_cache(maxsize=1)
def _dependencies() -> _Dependencies:
    repository = FirestoreMemoryRepository()
    domain_repository = FirestoreDomainRepository()
    embedding_provider = VertexEmbeddingProvider()
    gate_classifier = GeminiGateClassifier()
    return _Dependencies(
        save_memory=SaveMemoryUseCase(repository, embedding_provider, gate_classifier, domain_repository),
        search_memories=SearchMemoriesUseCase(repository, embedding_provider, domain_repository),
        forget_memory=ForgetMemoryUseCase(repository),
        pin_memory=PinMemoryUseCase(repository),
        load_pinned_memories=LoadPinnedMemoriesUseCase(repository, domain_repository),
        list_domains=ListDomainsUseCase(domain_repository),
        register_domain=RegisterDomainUseCase(domain_repository),
    )


def _to_memory_view(memory: Memory) -> tool_schemas.MemoryView:
    return tool_schemas.MemoryView(
        doc_id=memory.id,
        type=memory.type,
        title=memory.title,
        premise=memory.premise,
        conclusion=memory.conclusion,
        tags=list(memory.tags) if memory.tags else [],
    )


def _to_domain_view(domain: Domain) -> tool_schemas.DomainView:
    return tool_schemas.DomainView(name=domain.name, description=domain.description, created_at=domain.created_at)


@mcp_server.tool(
    description="Store long-term memory. If result is 'conflict_detected' or 'requires_registration', pause and confirm with user before proceeding."
)
async def save_memory(
    domain: tool_schemas.SaveMemoryDomain,
    title: tool_schemas.SaveMemoryTitle,
    premise: tool_schemas.SaveMemoryPremise,
    conclusion: tool_schemas.SaveMemoryConclusion,
    type: tool_schemas.SaveMemoryType,
    tags: tool_schemas.SaveMemoryTags = None,
    importance_score: tool_schemas.SaveMemoryImportanceScore = None,
) -> tool_schemas.SaveMemoryResponse:
    request = SaveMemoryRequest(
        domain=domain,
        title=title,
        premise=premise,
        conclusion=conclusion,
        type=type,
        tags=tuple(tags) if tags else None,
        importance_score=importance_score,
    )
    try:
        result = await _dependencies().save_memory.execute(request)
    except ValueError as error:
        raise ToolError(str(error)) from error
    return tool_schemas.SaveMemoryResponse(
        decision=result.decision.value,
        doc_id=result.memory_id,
        registered_domains=list(result.registered_domains) if result.registered_domains else None,
        conflicting_memory=_to_memory_view(result.conflicting_memory) if result.conflicting_memory else None,
    )


@mcp_server.tool(
    description="Retrieve relevant memories by semantic similarity. Provide exact_tags for precise technical strings (error codes, function names, ticker symbols)."
)
async def search_memories(
    domain: tool_schemas.SearchMemoriesDomain,
    query: tool_schemas.SearchMemoriesQuery,
    type: tool_schemas.SearchMemoriesType = None,
    exact_tags: tool_schemas.SearchMemoriesExactTags = None,
    limit: tool_schemas.SearchMemoriesLimit = 2,
    include_superseded: tool_schemas.SearchMemoriesIncludeSuperseded = False,
    include_archived: tool_schemas.SearchMemoriesIncludeArchived = False,
) -> tool_schemas.SearchMemoriesResponse:
    request = SearchMemoriesRequest(
        domain=domain,
        query=query,
        type=type,
        exact_tags=tuple(exact_tags) if exact_tags else None,
        limit=limit,
        include_superseded=include_superseded,
        include_archived=include_archived,
    )
    try:
        result = await _dependencies().search_memories.execute(request)
    except DomainNotRegisteredError as error:
        raise ToolError(str(error)) from error
    return tool_schemas.SearchMemoriesResponse(memories=[_to_memory_view(m) for m in result.memories])


@mcp_server.tool(
    description="Archive or permanently delete a memory by doc_id. Search for it first to obtain the doc_id."
)
async def forget_memory(
    doc_id: tool_schemas.ForgetMemoryDocId,
    hard_delete: tool_schemas.ForgetMemoryHardDelete = False,
) -> None:
    request = ForgetMemoryRequest(memory_id=doc_id, hard_delete=hard_delete)
    await _dependencies().forget_memory.execute(request)


@mcp_server.tool(
    description="Mark a memory as always-available (pinned=True) or remove that mark (pinned=False). Search for it first to obtain the doc_id."
)
async def pin_memory(doc_id: tool_schemas.PinMemoryDocId, pinned: tool_schemas.PinMemoryPinned = True) -> None:
    await _dependencies().pin_memory.execute(doc_id, pinned=pinned)


@mcp_server.tool(
    description="Load pinned memories at session start. Call once per session only."
)
async def load_pinned_memories(
    domain: tool_schemas.LoadPinnedMemoriesDomain,
    limit: tool_schemas.LoadPinnedMemoriesLimit = 5,
) -> tool_schemas.SearchMemoriesResponse:
    request = LoadPinnedMemoriesRequest(domain=domain, limit=limit)
    try:
        memories = await _dependencies().load_pinned_memories.execute(request)
    except DomainNotRegisteredError as error:
        raise ToolError(str(error)) from error
    return tool_schemas.SearchMemoriesResponse(memories=[_to_memory_view(m) for m in memories])


@mcp_server.tool(
    description="List all registered domains."
)
async def list_domains() -> tool_schemas.ListDomainsResponse:
    domains = await _dependencies().list_domains.execute()
    return tool_schemas.ListDomainsResponse(domains=[_to_domain_view(d) for d in domains])


@mcp_server.tool(
    description="Register a new domain. Call only after explicit user confirmation."
)
async def register_domain(
    name: tool_schemas.RegisterDomainName,
    description: tool_schemas.RegisterDomainDescription,
) -> tool_schemas.RegisterDomainResponse:
    request = RegisterDomainRequest(name=name, description=description)
    try:
        result = await _dependencies().register_domain.execute(request)
    except ValueError as error:
        raise ToolError(str(error)) from error
    return tool_schemas.RegisterDomainResponse(
        domain=_to_domain_view(result.domain), already_registered=result.already_registered
    )


def _get_transport_security() -> TransportSecuritySettings | None:
    disable_protection = os.environ.get("MNEMOSYNE_DISABLE_DNS_REBINDING_PROTECTION", "").lower() in ("true", "1", "yes")
    if disable_protection:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    allowed_hosts_str = os.environ.get("MNEMOSYNE_ALLOWED_HOSTS")
    if allowed_hosts_str:
        allowed_hosts = [h.strip() for h in allowed_hosts_str.split(",") if h.strip()]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )
    return None


security_settings = _get_transport_security()
streamable_http_app_kwargs = {}
if security_settings is not None:
    streamable_http_app_kwargs["transport_security"] = security_settings

app = key_auth_middleware.KeyAuthMiddleware(
    mcp_server.streamable_http_app(**streamable_http_app_kwargs),
    expected_key=os.environ.get("MNEMOSYNE_MCP_KEY"),
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.SERVER_PORT)


if __name__ == "__main__":
    main()
