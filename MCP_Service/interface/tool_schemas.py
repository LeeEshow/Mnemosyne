from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

SaveMemoryDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Domain name (e.g. 'coding', 'finance', 'life'). Must be a registered domain "
            "(case-insensitive, trimmed). If unregistered, save_memory returns "
            "decision=\"requires_registration\" with the registered domain list."
        ),
    ),
]
SaveMemoryTitle = Annotated[str, Field(description="Short title for quick human reference (e.g. '0056 take-profit rule').")]
SaveMemoryPremise = Annotated[
    str,
    Field(
        description="Context/cause (≤500 chars): the situation or constraint that led to this memory.",
    ),
]
SaveMemoryConclusion = Annotated[
    str,
    Field(
        description="Decision/outcome (≤500 chars): the rule or lesson that will be applied.",
    ),
]
SaveMemoryType = Annotated[str, Field(description="Memory category (e.g. 'Notes', 'DailyReport', 'Preference').")]
SaveMemoryTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) Tags for exact-match retrieval. Include both precise technical "
            "strings (error codes, function names, ticker symbols) and core topic entities (names, objects, "
            "key concepts). Tags enable detection of low-similarity thematic conflicts."
        ),
    ),
]
SaveMemoryImportanceScore = Annotated[
    int | None,
    Field(default=None, ge=1, le=10, description="Self-assessed importance (1-10); defaults to mid-range if omitted."),
]

SearchMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Domain name. Must be registered (case-insensitive, trimmed). An unknown domain raises an error "
            "containing the registered domain list — not an empty result."
        ),
    ),
]
SearchMemoriesQuery = Annotated[
    str, Field(description="Natural-language query (e.g. 'What was the 0056 take-profit rule we discussed?').")
]
SearchMemoriesType = Annotated[
    str | None, Field(default=None, description="Filter by memory category (e.g. 'Notes', 'Preference').")
]
SearchMemoriesExactTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) Exact-match keywords for precise strings: error codes, "
            "function names, ticker symbols, or named entities."
        ),
    ),
]
SearchMemoriesLimit = Annotated[
    int, Field(default=2, ge=1, description="Maximum number of memories to return.")
]
SearchMemoriesIncludeSuperseded = Annotated[
    bool, Field(default=False, description="Include superseded memories.")
]
SearchMemoriesIncludeArchived = Annotated[
    bool, Field(default=False, description="Include archived memories.")
]

ForgetMemoryDocId = Annotated[str, Field(description="Firestore document ID.")]
ForgetMemoryHardDelete = Annotated[
    bool,
    Field(default=False, description="True to permanently delete; default archives (status=archived) only."),
]

PinMemoryDocId = Annotated[str, Field(description="Firestore document ID.")]
PinMemoryPinned = Annotated[bool, Field(default=True, description="True to pin; False to unpin.")]

LoadPinnedMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Domain name. Must be registered (case-insensitive, trimmed). An unknown domain raises an error "
            "containing the registered domain list."
        ),
    ),
]
LoadPinnedMemoriesLimit = Annotated[
    int, Field(default=5, ge=1, description="Maximum number of pinned memories to return.")
]

RegisterDomainName = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Domain name (e.g. 'coding', 'finance', 'life'). Normalized (trimmed, lowercased). "
            "Returns existing record if already registered."
        ),
    ),
]
RegisterDomainDescription = Annotated[
    str,
    Field(
        description="Scope description for this domain, shown in tool parameter hints to guide domain selection.",
    ),
]


class MemoryView(BaseModel):
    doc_id: str
    type: str
    title: str
    premise: str
    conclusion: str
    tags: list[str] = Field(default_factory=list)


class SaveMemoryResponse(BaseModel):
    decision: str
    doc_id: str | None
    registered_domains: list[str] | None = None
    conflicting_memory: MemoryView | None = None
    merged_memory: MemoryView | None = None


class SearchMemoriesResponse(BaseModel):
    memories: list[MemoryView]


class DomainView(BaseModel):
    name: str
    description: str
    created_at: datetime


class ListDomainsResponse(BaseModel):
    domains: list[DomainView]


class RegisterDomainResponse(BaseModel):
    domain: DomainView
    already_registered: bool
