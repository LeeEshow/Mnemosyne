"""檢索驗收測試集（Task.md Phase 2.9.2）。

對正式 Firestore 執行一批「真實 query → 應命中 doc_id」案例，量化 `search_memories`
的召回與排序正確率，取代目前純靠人工試打幾句話的驗證方式。每次調整 `DECAY_LAMBDA`
或任一 `WEIGHT_*` 權重時都應重跑這份測試集，比對前後的命中率/排序正確率。

案例存放於 `scripts/retrieval_benchmark_cases.py`（同目錄）的 `CASES` 常數，每筆案例為一個 dict：
- domain / query：比照 search_memories 工具的必要參數。
- expected_order：預期命中的 doc_id，依「預期排序」由前到後列出。單一元素只驗證有無命中；
  多個元素會額外驗證彼此的相對排序（例如驗證「重要度高但較舊」排在「重要度低但較新」之前）。
- type / exact_tags / limit：對應 search_memories 的同名選填參數，可省略。
- description：這筆案例想驗證的情境，選填但建議填寫（尤其是時效/重要度衝突案例）。

執行時透過 `SearchMemoriesRequest.record_access=False` 略過命中記憶的 `access_count` 寫回
（Phase 2.9.2 新增欄位），避免正式流程仰賴的 `access_count` 反覆執行間被測試自我墊高分數、
導致同一份測試集每次重跑結果都不可重複。

案例與案例之間刻意加了節流（見 `_CASE_THROTTLE_SECONDS`）：每個案例都會呼叫一次 embedding，
案例一多容易在短時間內打穿 `gemini-embedding-001` 的每分鐘請求配額（2026-09-01 PM 排查
`search_memories` 故障時實際踩過這個坑，屬於暫時性限流、非程式碼問題，見 Task.md Phase 2.9.2）。
撞到配額限制（`google.genai.errors.ClientError`，HTTP 429 RESOURCE_EXHAUSTED）的案例會回報成
獨立的 `QUOTA_EXCEEDED` 狀態；其餘任何例外（逾時、DNS、Google 端 503 等）回報成 `ERROR` 狀態並保留
原始錯誤訊息。兩者都不會跟真正的召回/排序失敗（`FAIL`）混在一起，也都不會讓整個 benchmark 中斷。

案例的 `limit` 預設對齊 `config.SEARCH_MEMORIES_DEFAULT_LIMIT`（正式環境 `search_memories` 實際
使用的預設值），而非隨意放寬——只在寬鬆 limit 下才命中的案例不能代表正式環境也召回得到。`PASS`/
`FAIL` 案例的輸出會額外印出實際命中名次，方便看出「有命中但排名快被擠出 limit 之外」的邊界情況。

執行方式：於 MCP_Service/ 目錄下 `python -m scripts.run_retrieval_benchmark`。
"""

import asyncio
from dataclasses import dataclass
from enum import Enum

from google.genai.errors import ClientError

import config
from application.search_memories_use_case import SearchMemoriesRequest, SearchMemoriesUseCase
from infrastructure.firestore_domain_repository import FirestoreDomainRepository
from infrastructure.firestore_memory_repository import FirestoreMemoryRepository
from infrastructure.vertex_embedding_provider import VertexEmbeddingProvider
from scripts.retrieval_benchmark_cases import CASES

# 對齊正式環境的預設值（config.SEARCH_MEMORIES_DEFAULT_LIMIT），而非隨意挑一個較寬鬆的數字：
# 案例若只在寬鬆的 limit 下才命中，並不能代表正式環境（AI 實際呼叫 search_memories 時的預設
# limit）也看得到這筆記憶，會讓 benchmark 的 PASS 變成假訊號。
_DEFAULT_CASE_LIMIT = config.SEARCH_MEMORIES_DEFAULT_LIMIT
_CASE_THROTTLE_SECONDS = 3.0
_QUOTA_EXCEEDED_HTTP_CODE = 429


class CaseStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class BenchmarkCase:
    domain: str
    query: str
    expected_order: tuple[str, ...]
    type: str | None = None
    exact_tags: tuple[str, ...] | None = None
    limit: int = _DEFAULT_CASE_LIMIT
    description: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: BenchmarkCase
    returned_ids: tuple[str, ...]
    status: CaseStatus
    error_message: str | None = None


def _load_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            domain=item["domain"],
            query=item["query"],
            expected_order=tuple(item["expected_order"]),
            type=item.get("type"),
            exact_tags=tuple(item["exact_tags"]) if item.get("exact_tags") else None,
            limit=item.get("limit", _DEFAULT_CASE_LIMIT),
            description=item.get("description", ""),
        )
        for item in CASES
    ]


def _check_recall(expected_order: tuple[str, ...], returned_ids: tuple[str, ...]) -> bool:
    return all(doc_id in returned_ids for doc_id in expected_order)


def _check_order(expected_order: tuple[str, ...], returned_ids: tuple[str, ...]) -> bool:
    if len(expected_order) < 2:
        return True
    positions = [returned_ids.index(doc_id) for doc_id in expected_order if doc_id in returned_ids]
    return positions == sorted(positions) and len(positions) == len(expected_order)


async def _run_case(use_case: SearchMemoriesUseCase, case: BenchmarkCase) -> CaseResult:
    request = SearchMemoriesRequest(
        domain=case.domain,
        query=case.query,
        type=case.type,
        exact_tags=case.exact_tags,
        limit=case.limit,
        record_access=False,
    )
    try:
        result = await use_case.execute(request)
    except Exception as error:
        # 廣義捕捉：除了配額限制，實務上還會遇到逾時、DNS 解析失敗、Google 端 503 等偶發網路
        # 問題，這些都不該讓整個 main() 的案例迴圈中斷、賠上其他已經或即將跑完的案例結果。
        # 429 配額限制歸類成 QUOTA_EXCEEDED（明確可重試、非排序/召回問題），其餘一律歸類成
        # ERROR，保留原始錯誤訊息供人工判斷。不可用 `except ClientError: ... raise` 兩段式寫法
        # ——re-raise 出去的例外不會被同一個 try 的其他 except 子句攔到，會直接讓 main() 崩潰。
        if isinstance(error, ClientError) and error.code == _QUOTA_EXCEEDED_HTTP_CODE:
            return CaseResult(case=case, returned_ids=(), status=CaseStatus.QUOTA_EXCEEDED, error_message=str(error))
        return CaseResult(case=case, returned_ids=(), status=CaseStatus.ERROR, error_message=str(error))
    returned_ids = tuple(memory.id for memory in result.memories)
    recall_ok = _check_recall(case.expected_order, returned_ids)
    order_ok = _check_order(case.expected_order, returned_ids)
    status = CaseStatus.PASS if recall_ok and order_ok else CaseStatus.FAIL
    return CaseResult(case=case, returned_ids=returned_ids, status=status)


def _print_result(result: CaseResult) -> None:
    label = result.case.description or result.case.query
    print(f"[{result.status.value}] {label}")
    if result.status == CaseStatus.QUOTA_EXCEEDED:
        print(f"  撞到 gemini-embedding-001 配額限制，非排序/召回問題，稍後重跑此案例即可。（{result.error_message}）")
    elif result.status == CaseStatus.ERROR:
        print(f"  執行時發生非配額限制的例外，需人工判斷是否為排序/召回問題：{result.error_message}")
    elif not _check_recall(result.case.expected_order, result.returned_ids):
        missing = [d for d in result.case.expected_order if d not in result.returned_ids]
        print(f"  召回失敗，未命中：{missing}")
    elif result.status == CaseStatus.FAIL:
        print(f"  排序不符預期，期望順序：{result.case.expected_order}，實際命中順序：{result.returned_ids}")
    if result.status in (CaseStatus.PASS, CaseStatus.FAIL):
        ranks = [result.returned_ids.index(d) + 1 for d in result.case.expected_order if d in result.returned_ids]
        print(f"  命中名次：{ranks}（limit={result.case.limit}）")


async def main() -> None:
    cases = _load_cases()
    if not cases:
        print("尚未建立任何測試案例，見 scripts/retrieval_benchmark_cases.py 的格式說明。")
        return
    use_case = SearchMemoriesUseCase(
        FirestoreMemoryRepository(), VertexEmbeddingProvider(), FirestoreDomainRepository()
    )
    results = []
    for index, case in enumerate(cases):
        if index > 0:
            await asyncio.sleep(_CASE_THROTTLE_SECONDS)
        results.append(await _run_case(use_case, case))
    for result in results:
        _print_result(result)
    passed = sum(1 for r in results if r.status == CaseStatus.PASS)
    quota_exceeded = sum(1 for r in results if r.status == CaseStatus.QUOTA_EXCEEDED)
    errored = sum(1 for r in results if r.status == CaseStatus.ERROR)
    print(
        f"\n{passed}/{len(results)} 通過（另有 {quota_exceeded} 筆撞配額限制、{errored} 筆其他例外，"
        "皆未列入判定，建議重跑）"
    )


if __name__ == "__main__":
    asyncio.run(main())
