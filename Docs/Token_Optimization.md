# Token 消耗優化方案

> 本文件記錄 Mnemosyne MCP 在 Claude 對話 Session 中的 Token 消耗分析，以及依影響程度排序的優化方案。所有估算以 Claude Sonnet 4.6 tokenizer 為基準，中文字約 1.5 chars/token，英數 JSON 結構約 4 chars/token。

---

## 1. 問題結構

### 1.1 兩種成本來源

注入 MCP 後，每個 Session 多出的 Token 來自兩個完全不同的來源，必須分開處理：

| 來源 | 發生時機 | 特性 |
|------|---------|------|
| **工具 Schema**（`list_tools` 的結果） | 每一個 API call（每個 turn）都帶入 | 固定成本，即使沒用到任何工具也要付 |
| **Tool Call 資料**（工具輸入 + 回傳結果） | 只在實際呼叫工具時發生 | 隨使用量線性增長，且會留在 conversation history 繼續佔後續 turn 的 context |

主要問題集中在第一種：**工具 Schema 的大小 × turns 數**。

### 1.2 當前 Schema 大小估算

8 個工具的完整 schema（`mcp_server.py` + `tool_schemas.py`）：

| 組成 | 估算 tokens |
|------|------------|
| 各工具 description 文字 | ~820 |
| 各工具參數 description（中文說明為主） | ~900 |
| JSON Schema 結構 overhead（屬性名稱、型別定義等） | ~310 |
| Domain list 動態注入（注入到 3 個工具，各一份） | ~270 |
| **合計** | **~2,300 tokens / turn** |

### 1.3 典型 Session 的 Token 分布

以 10-turn 的中等對話、含 1 次 load_pinned + 2 次 search + 1 次 save 為例：

| 項目 | 無 Prompt Cache | 有 Prompt Cache |
|------|----------------|----------------|
| Schema（10 turns） | 23,000 | 4,370 |
| load_pinned（2 筆記憶） | 820 | 820 |
| search × 2（3 筆結果各） | 2,180 | 2,180 |
| save × 1（含 premise/conclusion） | 575 | 575 |
| **合計** | **~26,575** | **~7,945** |

差距的來源是：有 Prompt Cache 時，第 2 turn 起的 schema 費率降為約 1/10（cache read vs cache write）。

### 1.4 關鍵障礙：動態注入破壞 Prompt Cache

Anthropic Prompt Cache 的命中條件是：相同 prefix 在 5 分鐘 TTL 內保持**位元完全一致**。

`mcp_server.py:56-76` 的 `_render_domain_list()` + `_with_dynamic_domain_description()` 機制在每次 `list_tools` 回應時，把即時的 domain 清單字串注入到 `save_memory`、`search_memories`、`load_pinned_memories` 三個工具的 `domain` 參數 description 裡。只要 domain 清單有任何新增或修改（即使 TTL=300s 的快取範圍內不變，跨 Session 仍可能不同），schema 的文字就會變，cache 就會 miss。

這是最需要優先解決的結構問題。

---

## 2. 方案一：移除動態 Domain List 注入（最高優先）

### 2.1 說明

**影響**: 解鎖 Prompt Cache，10-turn session 可節省 ~18,600 tokens（相當於 80% 的 schema 成本）。  
**改動幅度**: 小——只需修改 `mcp_server.py` 與 `config.py`。  
**行為影響**: 低。原本注入的目的是 UX 輔助（讓 AI 知道有哪些合法 domain），但 Proposal 2.3 已明確指出這只是輔助層，真正的強制力在 server 端的 `requires_registration` 攔截，而 `requires_registration` 回應本身就附帶 `registered_domains` 清單，AI 第一次填錯後即可學習。

### 2.2 具體改法

**Step 1：靜態化 domain 參數 description**

在 `tool_schemas.py` 中，將三個工具的 `domain` 欄位 description 的末尾（目前是動態注入區）改為靜態提示：

```python
# tool_schemas.py

SaveMemoryDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "領域分區（如 'dev'/'finance'/'life'），須為已在 Domain Registry 完成註冊的值。"
            "已知 domain 清單可透過 list_domains 工具查詢，或從此工具回傳的 "
            "decision=\"requires_registration\" 中的 registered_domains 欄位得知。"
            "⚠️ 硬性規則：收到 requires_registration 後，必須先向使用者說明新 domain 的定位，"
            "取得明確同意後才可呼叫 register_domain，不可自行判斷觸發。"
        ),
    ),
]

SearchMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "領域分區，須為已在 Domain Registry 完成註冊的值。"
            "填入未知 domain 會直接拋出錯誤（不同於 save_memory 回傳 requires_registration），"
            "錯誤訊息中附帶已註冊清單。"
        ),
    ),
]

LoadPinnedMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description="領域分區，須為已在 Domain Registry 完成註冊的值，未知則拋出錯誤。",
    ),
]
```

**Step 2：移除 `mcp_server.py` 中的動態注入機制**

刪除或 no-op 以下部分（保留 class 結構但移除注入邏輯）：

```python
# 可以整個移除的部分：
# - _DomainDescriptionCache class
# - _domain_description_cache 全域實例
# - _render_domain_list() function
# - _with_dynamic_domain_description() function
# - _DOMAIN_PARAM_TOOL_NAMES frozenset

# _MnemosyneMCPServer.list_tools() 回復成直接 return super().list_tools()
class _MnemosyneMCPServer(MCPServer):
    pass  # 不再需要 override list_tools
```

**Step 3：提高 `DOMAIN_LIST_CACHE_TTL_SECONDS`（過渡期保險措施）**

若選擇保留部分動態注入（例如只注入到 `save_memory`），則大幅提高 TTL 降低 schema 變動頻率：

```python
# config.py
DOMAIN_LIST_CACHE_TTL_SECONDS = 86400  # 24 小時，實際上等於 process 重啟才刷新
```

### 2.3 注意事項

移除注入後，`list_domains` 工具的角色從「schema 注入的資料來源」提升為「AI 查詢合法 domain 的主要工具」。`list_domains` 的 description（`mcp_server.py:264-270`）本已說明這是管理用途，必要時可補充：「對話開始時如需確認可用 domain，呼叫此工具一次即可」。

---

## 3. 方案二：壓縮工具 Schema 文字

### 3.1 說明

**影響**: ~360 tokens/turn（無 cache 情境下 10-turn 節省 3,600 tokens；有 cache 情境下只影響首次 turn，節省 ~360 tokens）。  
**改動幅度**: 小——只改 description 文字，不動邏輯。  
**建議時機**: 在方案一完成後執行，兩者疊加效果最佳。

### 3.2 各處可精簡位置

**（A）`SearchMemoriesDomain` 的重複說明**（節省 ~120 tokens）

現況在 `tool_schemas.py` 的 `SearchMemoriesDomain` 幾乎完整複製了 `SaveMemoryDomain` 的規則說明，只是結尾邏輯不同（save 回傳 requires_registration，search 直接拋錯）。精簡方式：只保留差異部分，通用規則以「同 save_memory 的 domain 說明」代替。

若方案一已移除動態注入並同步精簡 description，此處幾乎自動解決。

**（B）`LoadPinnedMemoriesDomain` 的說明**（節省 ~80 tokens）

目前約 160 chars 的說明可縮短為 2 句。見方案一 Step 1 的示範寫法（約 40 chars）。

**（C）`SaveMemoryTags` 的例句**（節省 ~60 tokens）

現況 description（`tool_schemas.py:49-55`）含「媽媽」「牛肉麵」「停損策略」等具體例子，用於引導 AI 提取非技術類主題實體。這些例子有必要性，但可改成更緊湊的格式：

```python
# 現況（~240 chars）
"(array of strings, optional) 關聯的標籤數組。除了精確技術字串（錯誤代碼如 'ERR_CORS'、"
"函式名如 'get_user'、股票代號如 '0056'）外，也務必提取記憶內容中的**核心主題實體**"
"（人名、食物、具體事物、關鍵概念，例如「媽媽」「牛肉麵」「停損策略」）——不要只挑技術字串，"
"這是系統能否偵測到「低相似度但主題相關」的修正型衝突（例如喜好類陳述的修正）的前提。"

# 精簡後（~130 chars）
"(array of strings, optional) 關聯的標籤：精確技術字串（錯誤代碼、函式名、股票代號）"
"+ 核心主題實體（人名、食物、具體事物、關鍵概念）。非技術實體的標籤對衝突偵測至關重要，"
"不可省略。"
```

**（D）`save_memory` tool description 的衝突處理說明**（節省 ~60 tokens）

現況衝突處理說明約 400 chars，其中步驟描述較細。可保留規則核心，縮短步驟說明：

```python
# 可縮短的部分（大意不變，篇幅約減半）
"⚠️ 回傳 decision=\"conflict_detected\" 時，立刻暫停操作，向使用者說明新舊記憶衝突內容，"
"詢問「覆蓋」或「並存」。覆蓋：呼叫 forget_memory(doc_id=...) 後重新呼叫本工具；"
"並存：調整內容後重新呼叫本工具。取得明確回覆前不可自行處理。"
```

**（E）`register_domain` description 的重複警告**（節省 ~40 tokens）

現況 `mcp_server.py:277-284` 中的 `register_domain` description 重複了部分 `SaveMemoryDomain` 的說明（不可在收到 requires_registration 後自行觸發）。若 `SaveMemoryDomain` 已說清楚規則，`register_domain` 只需保留「已取得使用者明確同意後才呼叫」這一條核心即可。

---

## 4. 方案三：移除 MemoryView 中的 `importance_score` 輸出欄位

### 4.1 說明

**影響**: ~3 tokens/筆記憶。一次 search 3 筆 = 9 tokens。影響極小。  
**改動幅度**: 極小——只改 `tool_schemas.py` 的 `MemoryView` 與 `mcp_server.py` 的 `_to_memory_view()`。  
**風險**: 零。

`importance_score` 是系統內部排序用的分數（在 `search_memories_use_case.py` 的 `_rank_top()` 中使用）。回傳給 AI 後，AI 沒有任何應用場景需要用到這個數字——AI 無法透過它做額外決策，因為搜尋結果已經是排序過後的子集。

### 4.2 具體改法

```python
# tool_schemas.py
class MemoryView(BaseModel):
    doc_id: str
    type: str
    title: str
    premise: str
    conclusion: str
    tags: list[str] = Field(default_factory=list)
    # 移除 importance_score: int

# mcp_server.py
def _to_memory_view(memory: Memory) -> tool_schemas.MemoryView:
    return tool_schemas.MemoryView(
        doc_id=memory.id,
        type=memory.type,
        title=memory.title,
        premise=memory.premise,
        conclusion=memory.conclusion,
        tags=list(memory.tags) if memory.tags else [],
        # 移除 importance_score=memory.importance_score,
    )
```

---

## 5. 方案四：Search Brief 模式（新功能）

### 5.1 說明

**影響**: 使用 brief 模式時，每次 search 節省 ~800 tokens（3 筆完整記憶 ~840 tokens → 3 筆 brief 結果 ~30 tokens）。  
**改動幅度**: 中——需新增參數、修改 use case 輸出格式、新增 response model。  
**適用場景**: AI 只需要確認「有沒有這個記憶」或「拿到 doc_id 去操作 forget/pin」，不需要讀完整 premise/conclusion。

### 5.2 設計

在 `search_memories` 加入 `brief: bool = False` 參數：

- `brief=False`（預設）：回傳完整 `MemoryView`（doc_id + type + title + premise + conclusion + tags），約 280 tokens/筆
- `brief=True`：只回傳 `BriefMemoryView`（doc_id + title），約 10 tokens/筆

```python
# tool_schemas.py 新增
class BriefMemoryView(BaseModel):
    doc_id: str
    title: str

class SearchMemoriesResponse(BaseModel):
    memories: list[MemoryView] | list[BriefMemoryView]  # 依 brief 參數決定
```

```python
# mcp_server.py 的 search_memories handler
@mcp_server.tool(description="...")
async def search_memories(
    domain: ...,
    query: ...,
    brief: Annotated[bool, Field(default=False, description=(
        "為 true 時僅回傳 doc_id 與 title，適合只需確認記憶存在或取得 doc_id 的情境，"
        "大幅減少 Token 消耗。需要閱讀完整內容時保持預設 false。"
    ))] = False,
    # ... 其他既有參數
) -> tool_schemas.SearchMemoriesResponse:
    ...
    if brief:
        return SearchMemoriesResponse(
            memories=[BriefMemoryView(doc_id=m.id, title=m.title) for m in result.memories]
        )
    return SearchMemoriesResponse(memories=[_to_memory_view(m) for m in result.memories])
```

### 5.3 注意事項

- `brief=True` 時 AI 仍然要先呼叫 search 才能拿到 doc_id，後續若需要完整內容仍需再次呼叫 `search_memories(brief=False)`。因此 brief 模式適合「查存在性」或「找 doc_id 做後續操作」的場景，不適合「需要理解記憶內容才能回答問題」的場景。
- `load_pinned_memories` 設計上是「帶入 context 用」，不建議加 brief 模式——如果 pinned 記憶不值得完整帶入，應該先 unpin，而非壓縮格式。

---

## 6. 方案五：調整 Search 預設回傳筆數

### 6.1 說明

**影響**: 每次 search 節省 ~280 tokens（少回傳 1 筆記憶）。  
**改動幅度**: 極小——只改 `config.py` 一行。  
**代價**: 第 3 筆記憶的召回率下降，適合記憶庫已有良好分類、命中率高的情境。

```python
# config.py
SEARCH_MEMORIES_DEFAULT_LIMIT = 2  # 從 3 改為 2
```

AI 仍可在呼叫時指定 `limit=3` 覆蓋預設值，因此這只是「預設行為」的調整，不是強制限制。

---

## 7. 補充：Gemini 端成本（非 Claude Session Token）

以上方案聚焦在 Claude 的 Session Token 消耗。另一個獨立的成本來源是每次 `save_memory` 觸發的 Gemini 分類器呼叫（`infrastructure/gemini_gate_classifier.py`）：

| 呼叫內容 | 估算 tokens（Gemini 端） |
|---------|----------------------|
| `_PROMPT_TEMPLATE` 本體（說明 5 種決定值的規則） | ~900 |
| 新記憶（title + premise + conclusion） | ~300-700 |
| 候選記憶清單（最多 3 筆 × premise + conclusion） | ~600-1,500 |
| **合計** | **~1,800-3,100 tokens / 次 save** |

這筆費用走個人 Google AI Studio 訂閱額度（`GEMINI_API_KEY` 模式），不計入 GCP Firestore 帳單，也與 Claude session 費用完全分離。頻繁寫入的 Session 可考慮是否有壓縮 `_PROMPT_TEMPLATE` 冗餘說明的空間，但分類準確性是核心功能，不建議大幅裁剪，評估時須權衡準確率與成本。

---

## 8. 優先序與實施建議

### 8.1 建議執行順序（初版）

> ⚠️ **本節已由第 9 章的三輪收斂方案取代，請直接參考第 9 章。**

| 順序 | 方案 | 狀態 |
|------|------|------|
| 1 | 移除動態 domain 注入 | ✅ 保留，納入最終方案 |
| 2 | 壓縮 schema 文字 | ✅ 升級為英文化 + 雙保險架構 |
| 3 | 移除 importance_score | ✅ 保留 |
| 4 | Brief 模式 | ❌ 捨棄（靜態 schema 稅 > 收益） |
| 5 | limit 3→2 | ✅ 保留 |

### 8.2 為何方案一最關鍵

方案一的節省量遠超其他方案的原因：它不是直接減少 schema 大小，而是**讓 Prompt Cache 得以命中**。Cache hit 後第 2-N turn 的 schema 費率降為 1/10，實際效果等同於讓 schema 從 2,300 tokens/turn 降到 230 tokens/turn（turn 2 起）。這個槓桿效果是其他所有方案加總都無法達到的。

### 8.3 方案一與 Domain Registry UX 的取捨

移除動態注入會讓 AI 在填入 domain 時沒有「即時提示」。實際影響評估：

- **同一個 Session 內**：AI 在第一次 save 或 search 失敗後，錯誤回應中已附帶完整已註冊清單，學習成本是一次工具呼叫失敗。
- **跨 Session**：AI 在新 Session 開始時沒有先驗知識，需要靠第一次失敗或主動呼叫 `list_domains` 獲知清單。
- **新 domain 需求**：原本流程就要求 AI 在收到 `requires_registration` 後暫停並詢問使用者，這個流程不受影響。

---

## 9. 三輪收斂後的最終確認方案

> 本章記錄 Claude + agy 三輪架構討論後達成共識的最終設計，取代第 8 章的初版排序。

### 9.1 核心架構決策：雙保險設計（Belt and Suspenders）

本 MCP 需同時支援 Claude Desktop、Claude Code CLI、Gemini 等多種 client。不同 client 對 MCP 規格的實作完整度不一：

| Client | `InitializeResult.instructions` 支援 |
|--------|--------------------------------------|
| Claude Desktop / Claude Code | ✅ 標準實作，注入 model context |
| Gemini / 第三方（LangChain 等） | ⚠️ 不確定，取決於各自實作程度 |

**解法**：兩層並存，各司其職：

1. **第一層（Server `instructions`）**：完整的全域行為規則，供標準 client 使用。MCP SDK 已確認支援（`mcp.types.InitializeResult.instructions`，`MCPServer.__init__(instructions=...)`）。
2. **第二層（Tool-level 1 行 guardrail）**：在 `save_memory` 與 `register_domain` 保留極簡英文安全約束，確保任何 client 都能拿到最低限度的行為指引。

**重要說明**：把規則移進 `instructions` 不代表 tokens 消失，它們從「工具 schema（每 turn 重複計算）」移到「連線時注入的 context（行為類似 system prompt，可被 cache）」。節省的是每 turn 重複付費的成本，不是總量歸零。

### 9.2 最終執行清單

#### [P0] 移除動態 domain 注入 → 解鎖 Prompt Cache

同第 2 章。預期收益：10-turn 節省 ~18,600 tokens（最大槓桿）。

#### [P0] Schema 全面英文化 + 雙保險精煉

**目標**：schema 從 ~2,300 tokens 降至 ~1,000 tokens。

**`save_memory` description（示範）：**
```
Store long-term memory with causal premise/conclusion. Dedup/merge built-in.
If result is 'conflict_detected' or 'requires_registration', pause and confirm with user before proceeding.
```

**`register_domain` description（示範）：**
```
Register a new domain. Call only after explicit user confirmation.
```

**`search_memories` / `load_pinned_memories` domain 參數**：移除所有 `register_domain` 流程說明（跨工具污染），只保留「未知 domain 直接拋錯，錯誤訊息附帶已註冊清單」這一條。

**清除跨工具描述污染**：
- `SearchMemoriesDomain`：移除 requires_registration 流程說明（search 不會回傳此值）
- `LoadPinnedMemoriesDomain`：同上
- 僅 `save_memory` 需要完整的 requires_registration 處理說明（因為它會觸發）

#### [P0] 配置 Server `instructions`

在 `mcp_server.py` 初始化時注入完整的全域行為規則：

```python
_SERVER_INSTRUCTIONS = """
Mnemosyne is a long-term memory layer. Key behavioral rules:

CONFLICT: If save_memory returns decision='conflict_detected', immediately stop, explain both memories to the user, and ask whether to overwrite or coexist. Never resolve autonomously.

DOMAIN REGISTRATION: If save_memory returns decision='requires_registration', stop and explain the new domain to the user. Call register_domain only after explicit user approval.

SEARCH: search_memories and load_pinned_memories raise an error for unregistered domains — error message includes the registered domain list.

PINNED MEMORIES: Call load_pinned_memories once at session start. Do not repeat in the same session.
"""

mcp_server = _MnemosyneMCPServer("mnemosyne", instructions=_SERVER_INSTRUCTIONS)
```

#### [P1] 整併 `pin_memory` / `unpin_memory`

```python
@mcp_server.tool(description="Pin or unpin a memory. pinned=True to pin (default), False to unpin.")
async def pin_memory(doc_id: str, pinned: bool = True) -> None:
    if pinned:
        await _dependencies().pin_memory.execute(doc_id)
    else:
        await _dependencies().unpin_memory.execute(doc_id)
```

移除完整的 `unpin_memory` 工具物件，省 ~50-100 tokens（視語言）。

#### [P1] Response Payload 瘦身

```python
# tool_schemas.py：移除 importance_score
class MemoryView(BaseModel):
    doc_id: str
    type: str
    title: str
    premise: str
    conclusion: str
    tags: list[str] = Field(default_factory=list)

# mcp_server.py：回傳時排除 null 欄位
return response.model_dump(exclude_none=True)
```

#### [P2] 搜尋預設 limit 調整

```python
# config.py
SEARCH_MEMORIES_DEFAULT_LIMIT = 2  # 從 3 改為 2
```

### 9.3 捨棄的方案

**Brief 模式（原方案四）**：捨棄。

靜態 schema 稅分析：為 brief 參數增加說明 ~30-50 tokens（每 turn 必付），但管理操作佔比 <5%，期望收益僅 ~40 tokens。長期期望值為負，且維護複雜度不值得。

### 9.4 最終效益預估

以 10-turn 對話（1 次 load_pinned + 2 次 search + 1 次 save）為基準：

| 狀態 | Schema tokens/turn | 10-turn 總成本 |
|------|-------------------|---------------|
| 原始（無任何優化） | ~2,300 | ~26,575 |
| 方案一後（解鎖 cache，schema 不變） | ~2,300 → ~230（turn 2+）| ~7,945 |
| **最終方案（cache + 英文化 + 雙保險）** | ~1,000 → ~100（turn 2+）| **~3,700** |

> 最終方案相較原始狀態節省約 **86%**；相較僅解鎖 cache 的中間態再節省約 **53%**。
