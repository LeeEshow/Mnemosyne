import copy
import os
import time
from dataclasses import dataclass
from functools import lru_cache

from mcp_types import Tool as MCPTool

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
from application.pin_memory_use_case import PinMemoryUseCase, UnpinMemoryUseCase
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

_DOMAIN_PARAM_TOOL_NAMES = frozenset({"save_memory", "search_memories", "load_pinned_memories"})


class _DomainDescriptionCache:
    """`list_tools` 動態注入用的 domain 清單快取（TTL，見 Proposal 4.1）。

    僅影響 UX 輔助文字，與 requires_registration/DomainNotRegisteredError 的即時驗證無關，
    因此可以安全地快取一段時間，避免 Client 高頻呼叫 list_tools 時每次都打 Firestore。
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl_seconds = ttl_seconds
        self._cached_at: float | None = None
        self._rendered = ""

    async def get(self) -> str:
        now = time.monotonic()
        if self._cached_at is None or now - self._cached_at >= self._ttl_seconds:
            domains = await _dependencies().list_domains.execute()
            self._rendered = _render_domain_list(domains)
            self._cached_at = now
        return self._rendered


def _render_domain_list(domains: tuple[Domain, ...]) -> str:
    if not domains:
        return "目前尚未註冊任何 domain。"
    entries = "；".join(f"{d.name}：{d.description}" for d in domains)
    return f"目前已註冊的 domain：{entries}"


_domain_description_cache = _DomainDescriptionCache(config.DOMAIN_LIST_CACHE_TTL_SECONDS)


def _with_dynamic_domain_description(tool: MCPTool, domain_description: str) -> MCPTool:
    if tool.name not in _DOMAIN_PARAM_TOOL_NAMES:
        return tool
    properties = tool.input_schema.get("properties", {})
    domain_property = properties.get("domain")
    if domain_property is None:
        return tool
    schema = copy.deepcopy(tool.input_schema)
    base_description = domain_property.get("description", "")
    schema["properties"]["domain"]["description"] = f"{base_description}\n\n{domain_description}"
    return tool.model_copy(update={"input_schema": schema})


class _MnemosyneMCPServer(MCPServer):
    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        domain_description = await _domain_description_cache.get()
        return [_with_dynamic_domain_description(tool, domain_description) for tool in tools]


mcp_server = _MnemosyneMCPServer("mnemosyne")


@dataclass(frozen=True)
class _Dependencies:
    save_memory: SaveMemoryUseCase
    search_memories: SearchMemoriesUseCase
    forget_memory: ForgetMemoryUseCase
    pin_memory: PinMemoryUseCase
    unpin_memory: UnpinMemoryUseCase
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
        unpin_memory=UnpinMemoryUseCase(repository),
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
        importance_score=memory.importance_score,
    )


def _to_domain_view(domain: Domain) -> tool_schemas.DomainView:
    return tool_schemas.DomainView(name=domain.name, description=domain.description, created_at=domain.created_at)


@mcp_server.tool(
    description=(
        "當對話中出現值得長期保存的新事實、重要決策、個人偏好或代碼知識點時呼叫，"
        "或任務完成後想記錄過程與教訓時呼叫（premise 填任務過程與成因、conclusion 填經驗教訓，"
        "不需要另外呼叫別的工具）。"
        "已內建重複偵測與合併更新機制，呼叫前不需要先以 search_memories 確認是否重複。"
        "⚠️ 硬性規則：回傳 decision=\"conflict_detected\" 時，你必須立刻暫停所有操作，"
        "在對話中向使用者詳細說明新舊記憶的衝突內容，並詢問使用者要「覆蓋」還是「並存」，"
        "絕對不可在取得使用者明確回覆前擅自呼叫 forget_memory 或自行判斷處理。"
        "取得回覆後：選擇覆蓋則呼叫 forget_memory(doc_id=衝突記憶的 doc_id) 封存後再重新呼叫本工具；"
        "選擇並存則調整內容後再重新呼叫本工具。"
    )
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
    description=(
        "當使用者的提問涉及過去的討論、決策、偏好，或你需要當前對話沒有的歷史脈絡時呼叫。"
        "檢索範圍會鎖定於指定的 domain 與全域通用偏好（例如回覆語言格式），"
        "查無結果不代表使用者從未提過，可能只是超出目前可讀取的範圍。"
        "若查詢包含特定的錯誤代碼、函式名、股票代號等精確字串，請務必填入 exact_tags 參數以確保字面精確命中。"
    )
)
async def search_memories(
    domain: tool_schemas.SearchMemoriesDomain,
    query: tool_schemas.SearchMemoriesQuery,
    type: tool_schemas.SearchMemoriesType = None,
    exact_tags: tool_schemas.SearchMemoriesExactTags = None,
    limit: tool_schemas.SearchMemoriesLimit = 3,
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
    description=(
        "當使用者告知某筆記憶已過時、錯誤或不再需要時呼叫，將其封存或刪除。"
        "你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫此工具。"
    )
)
async def forget_memory(
    doc_id: tool_schemas.ForgetMemoryDocId,
    hard_delete: tool_schemas.ForgetMemoryHardDelete = False,
) -> None:
    request = ForgetMemoryRequest(memory_id=doc_id, hard_delete=hard_delete)
    await _dependencies().forget_memory.execute(request)


@mcp_server.tool(
    description=(
        "當某筆記憶被判定為『極端重要、不能被一般排序稀釋』時呼叫，將其標記為常駐記憶，"
        "確保之後一定會出現在對話開頭的常駐清單（load_pinned_memories）中。避免濫用造成常駐清單膨脹。"
        "你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫。"
    )
)
async def pin_memory(doc_id: tool_schemas.PinMemoryDocId) -> None:
    await _dependencies().pin_memory.execute(doc_id)


@mcp_server.tool(
    description=(
        "當某筆記憶不再需要保持常駐狀態時呼叫，取消其常駐標記。"
        "你必須先知道該記憶的 doc_id（可透過 search_memories 或既有的常駐清單得知）才能呼叫。"
    )
)
async def unpin_memory(doc_id: tool_schemas.PinMemoryDocId) -> None:
    await _dependencies().unpin_memory.execute(doc_id)


@mcp_server.tool(
    description=(
        "當新對話開始時呼叫一次，取得少量常駐記憶直接帶入上下文；"
        "不需要在同一次對話中重複呼叫多次。"
    )
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
    description=(
        "當你需要確認目前已註冊的 domain 完整清單時呼叫，主要供人工檢視管理使用。"
        "平時判斷該填哪個既有 domain 時，優先參考各工具 domain 參數說明中動態列出的清單，"
        "不需要每次都先呼叫此工具確認。"
    )
)
async def list_domains() -> tool_schemas.ListDomainsResponse:
    domains = await _dependencies().list_domains.execute()
    return tool_schemas.ListDomainsResponse(domains=[_to_domain_view(d) for d in domains])


@mcp_server.tool(
    description=(
        "當使用者已在對話中明確同意建立一個尚未存在的新 domain 時才呼叫此工具。"
        "⚠️ 硬性規則：不可在 save_memory/search_memories/load_pinned_memories 收到 requires_registration 或"
        "「此 domain 尚未註冊」錯誤後自行判斷觸發——必須先向使用者說明這是尚未存在的新領域、其定位為何，"
        "取得明確同意後才呼叫。呼叫前也必須先參考已注入的既有 domain 清單，"
        "若語意重疊應建議使用者沿用既有 domain，而非新建，避免分類漂移。"
    )
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
sse_app_kwargs = {}
if security_settings is not None:
    sse_app_kwargs["transport_security"] = security_settings

app = key_auth_middleware.KeyAuthMiddleware(
    mcp_server.sse_app(**sse_app_kwargs),
    expected_key=os.environ.get("MNEMOSYNE_MCP_KEY"),
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.SERVER_PORT)


if __name__ == "__main__":
    main()
