# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節。
> 勾選代表完成，未完成項目維持未勾選。

---

## 0. 程式碼架構模式（Architecture Pattern）

> 討論脈絡：技術棧定案為 Python + FastAPI（見 Proposal 3.2），主要理由是與既有 `NoCode_Project`（fintarck-backend）共用 GCE 主機、部署模式一致，而非 Python 本身在 MCP 生態系上佔優勢。為了不把「未來若要換成 Node.js」的成本鎖死在框架選擇上，開發時採用 **Hexagonal Architecture（Ports & Adapters）**，把業務邏輯與 Python/GCP 框架細節實體隔離。

```
MCP_Service/
├── domain/                     # 零框架依賴，未來逐檔翻譯成 TS 即可
│   ├── models.py                # Memory 等不可變值物件（frozen dataclass）
│   ├── scoring.py                # 衰減排序公式（6.1）—— 純函式
│   ├── write_gate_policy.py      # 寫入閘門判定規則（5.1）—— 純函式 + 門檻常數
│   └── ports/                    # 抽象介面（typing.Protocol）
│       ├── memory_repository.py
│       ├── embedding_provider.py
│       └── gate_classifier.py
├── application/                # Use case 協調層，依賴注入 port，無框架細節
├── infrastructure/              # 具體實作，綁死 Python/GCP —— 未來遷移只重寫這層
│   ├── firestore_memory_repository.py
│   ├── vertex_embedding_provider.py
│   └── gemini_gate_classifier.py
└── interface/                   # MCP/FastAPI 入口，只做 I/O 轉換
    ├── mcp_server.py             # tool 註冊、SSE transport、動態 domain 描述注入
    ├── key_auth_middleware.py    # MNEMOSYNE_MCP_KEY 驗證
    └── tool_schemas.py           # Pydantic model 僅存在這一層
```

**三條硬規則**：
1. `domain/` 底下**禁止 import** FastAPI、Pydantic、firebase-admin、google-genai 等任何第三方框架/SDK，只能用標準庫。
2. Port 一律用 `typing.Protocol` 定義（結構化介面）。
3. `interface/` 層（MCP tool handler）只做「解析輸入 → 呼叫 use case → 格式化輸出」，不寫任何業務判斷。

---

## 已就緒的基礎設施（Phase 1 完成，不受本輪 Schema/因果模型改版影響）

以下為已完成並驗證的環境/部署基礎設施，本身不受這次因果模型改版牽動，僅供參考不需重做：

- **GCP / Firestore**：獨立專案 `mnemosyne-cb868`（Firestore Standard 版、`asia-east1`）；唯一複合索引 `domain (ASC) + status (ASC) + embedding (Vector, 1536維, COSINE)`（原為 768 維搭配 Vertex AI 的 `text-multilingual-embedding-002`，切換為個人 Gemini API Key 後改用 `gemini-embedding-001` 並 Matryoshka 截斷至 1536 維，詳見 `CLAUDE.md`）；GCE 服務帳戶 `1077248196503-compute@developer.gserviceaccount.com` 已授予 `Cloud Datastore User` + `Vertex AI User`（免金鑰檔案，走 GCE 附加身分；`mnemosyne-db-sa` 專屬服務帳戶另外持有金鑰檔案供 Firestore 存取，見 CLAUDE.md 部署踩坑章節）。
- **Python 環境**：`MCP_Service/` 專案骨架（依上方架構）；`pyproject.toml` 的 `requires-python = ">=3.10"`（`fintarck-backend` 主機 Python **鎖定 3.10.12**，見 PM 記憶 `project_gce_python_pinned`，**不可**為 Mnemosyne 安裝新版本，程式碼須自行相容，例如禁用 `datetime.UTC`，改用 `datetime.timezone.utc`）。
- **部署基礎設施**：`/app-mnemosyne`（GCE 上的 Private repo clone，SSH Deploy Key `~/.ssh/mnemosyne_deploy` + SSH config alias `github-mnemosyne`，唯讀）；`mnemosyne.service`（systemd，`Type=simple`，監聽 `:8001`，`.env` 存 `MNEMOSYNE_MCP_KEY`/專案 ID/區域）；防火牆規則 `allow-uvicorn-8001`；`fintarck-proxy` 的 `nginx.conf` 已加 `/mnemosyne/` location block 轉發至 `:8001`（3.3.1）。連線網址（v5 起不再帶 `domain`，見下方 Phase 2）：`https://fintarck-proxy-1077248196503.asia-east1.run.app/mnemosyne/mcp?key=<KEY>`。
- **身分驗證機制**：`KeyAuthMiddleware` 只在無 `session_id` 的初始 SSE GET 請求比對 `key`（`hmac.compare_digest`，fail-safe：`MNEMOSYNE_MCP_KEY` 未設定或空字串一律拒絕），驗證通過後同連線後續 `POST /messages/?session_id=...` 免比對（`session_id` 為 128-bit UUID4，需先驗證通過才拿得到）。此機制與 domain 無關，v5 改版不受影響。

> **v5 異動說明**：舊版曾用 `MCPServer(lifespan=...)` + `connection_context.DomainBindingMiddleware` 把 `domain` 綁在連線層級（見 git 歷史），因可用性摩擦過大（見 Proposal 2.3 v5 定案）已整段移除，改為 `domain` 一般工具參數 + Domain Registry 攔截，見下方 Phase 2.2。

---

## Phase 2：因果模型改版（重新設計後，以下項目需重新實作）

> **改版脈絡**：PM 與 agy 針對「Domain 使用摩擦」「因果記憶模型」「低相似度衝突偵測」三個主題深入討論後，設計有實質變動（詳見 Proposal 2.3、2.5、4、5 章）。Domain 治理機制另經過 v5 一輪修訂（連線層級綁定 + 白名單 → Domain Registry + 工具參數）。以下列出**需要新做或修改**的項目；未列出的既有機制（如衰減排序公式本身、`forget_memory`、`pin_memory`/`unpin_memory`）維持不變。

### 2.1 Schema 調整
- [x] Firestore `memories` 集合欄位調整：`context` 拆分為 `because`/`then`（**已於命名收斂後改名，見下方新項**）；刪除 `source_id`、`last_accessed_at`（Proposal 4 章）
- [x] `domain/models.py` 的 `Memory` 值物件同步調整欄位
- [x] 新增 Firestore `domains` 集合（`name`/`description`/`created_at`，Proposal 4.1；**原欄位名 `describe` 已於 code review 後改名為 `description`，見下方新項**）；`domain/models.py` 新增對應值物件
- [x] **欄位改名（追加，命名收斂後定案）**：`because`/`then` → `premise`/`conclusion`（Proposal 2.5、4 章）。原因：`because`/`then` 是連接詞借用當欄位名，語法不自然；且 `then` 容易讓人聯想成流程控制關鍵字。新名稱為正常名詞、語意對應「前提→結論」的邏輯論證框架，且不與既有的 `SaveMemoryResponse.decision`（寫入閘門判定值）撞名。**影響範圍**：Firestore 既有文件的欄位（若已有測試資料）、`domain/models.py` 的 `Memory` 值物件、`write_gate_policy.py` 內任何直接引用欄位名的邏輯、`tool_schemas.py` 的 Pydantic 欄位
- [x] **欄位改名（追加，第二輪 code review 提出、PM 確認後定案）**：`Domain.describe` → `Domain.description`（Proposal 4.1）。原因：`describe` 是動詞原形當名詞欄位名，且與 `tool_schemas.py` 大量使用的 pydantic `Field(description=...)` 併存，同一概念出現兩種拼法，屬於同 codebase 內部命名不一致；趁尚未部署、Firestore 沒有既有文件之前改名零相容性成本。**影響範圍**：`domain/models.py`、`application/register_domain_use_case.py`、`interface/tool_schemas.py`（含 `RegisterDomainDescribe` → `RegisterDomainDescription`）、`interface/mcp_server.py`、`infrastructure/firestore_domain_repository.py`（Firestore 文件欄位 key）、`scripts/seed_domain_registry.py`。`Docs/Mnemosyne_MCP_Proposal.md` 4.1 章節表格仍寫 `describe`，屬 PM 職掌文件，SE 不代為修改，待 PM 側同步。

### 2.2 Domain Registry 與 requires_registration 攔截（取代舊版連線層級綁定，Proposal 2.3 v5）
- [x] 移除 `connection_context.py`（`DomainBindingMiddleware`）與 `mcp_server.py` 的 `_bind_connection_domain` lifespan、`_resolve_domain`；`app = key_auth_middleware.KeyAuthMiddleware(mcp_server.sse_app(), ...)` 直接掛，不再包 `DomainBindingMiddleware`
- [x] `save_memory` 的 `domain` 改為一般必填參數，於 use case 開頭做正規化（`strip().lower()`）+ Registry 存在性驗證，未註冊直接回傳 `decision="requires_registration"`（沿用既有 decision 欄位，同 `conflict_detected` 走結構化回應，不拋例外）+ 已註冊 domain 清單，不進入後續向量化/寫入閘門邏輯（Proposal 5.1 第 0 步）
- [x] `search_memories` / `load_pinned_memories` 的 `domain` 同樣改為一般必填參數 + 正規化 + Registry 驗證，但未註冊時改為 **`raise DomainNotRegisteredError`**（自訂例外，附帶已註冊 domain 清單）而非回傳結構化欄位——這兩個工具的 Response Schema（`SearchMemoriesResponse`）單純是記憶列表，不像 `save_memory` 有 decision 欄位可承載狀態，讓 MCP SDK 自動轉換成 `isError=True` 錯誤回應，避免 Response Schema 混入非搜尋結果的分支（Proposal 5.2 第 0 步，agy 建議採納）。**實作細節**：`interface/mcp_server.py` 的工具 handler 捕捉 `DomainNotRegisteredError` 後改拋 `mcp.server.mcpserver.exceptions.ToolError`（而非讓例外原樣往上跑）——實測發現 MCP SDK 對未預期的裸 `Exception` 會包成 `UnexpectedToolError` 並把訊息文字整個抹除、只留下 `"Error executing tool <name>"`，`ToolError` 才是 SDK 設計上「有預期的失敗」，訊息文字才會保留給 AI 讀到
- [x] 新增工具 `list_domains`（讀取 `domains` 集合全部文件，見 Proposal 5.6）
- [x] 新增工具 `register_domain`（`name`/`description`，正規化後檢查唯一性再寫入，見 Proposal 5.7）；description 需引導 AI 呼叫前先比對既有 domain，避免分類漂移
- [x] `mcp_server.py` 的 `list_tools` 回應動態查詢 `domains` 集合，把清單與描述拼進 `domain` 參數的 description（Proposal 2.3 第 3 點；僅為 UX 輔助，非唯一防線，實際把關靠上面的 `requires_registration`/`DomainNotRegisteredError` 攔截）；**查詢結果加記憶體內快取，TTL 5-10 分鐘**，避免 Client 高頻呼叫 `list_tools`（如 Cursor 初始化/切換視窗）時每次都打 Firestore（Proposal 4.1，agy 建議採納）。**實作細節**：以子類別 `_MnemosyneMCPServer` 覆寫 `list_tools()`，每次都對原始 schema 深拷貝後才附加動態文字，避免直接改到 `ToolManager` 內部共用的 schema dict 造成文字隨呼叫次數累加
- [x] **部署前置步驟（阻塞性，見 Proposal 3.3）**：上線前寫一次性遷移腳本，掃描既有 `memories` 集合中所有 distinct `domain` 值，批次寫入 `domains` 完成 seed，避免舊資料在切換當下被誤判未註冊而擋下存取；**`"global"` 必須明確一併 seed**（不能依賴掃描結果自動涵蓋，若舊資料庫從未真的寫過 `domain="global"` 的記憶就會漏掉），描述範例：`"全域通用偏好與設定，檢索時會自動與指定領域合併，請勿在此寫入特定技術或專案知識。"`（Proposal 2.3 第 6 點，agy 建議採納）。**實作位置**：`scripts/seed_domain_registry.py`，可重複執行（已註冊的 domain 會跳過）

### 2.3 寫入閘門重構（5.1）
- [x] 候選查詢改為**雙軌並行**：軌道 A 向量最近鄰（同 domain，取 Top-3，`WRITE_GATE_CANDIDATE_LIMIT` 從 1 改為 3）＋ 軌道 B 標籤交集（`tags array-contains-any`，同 domain，不受相似度限制），Python 端依 ID 去重合併
- [x] 判定分流邏輯調整：字串完全相同 → `NOOP`；候選集為空或無交集且相似度 < `LOW_THRESHOLD` → `ADD`；其餘（相似度 ≥ 0.85 **或**存在標籤交集）→ 呼叫 Gemini Flash 判定 `NOOP`/`UPDATE`/`SUPERSEDE`/`CONFLICT_DETECTED`/`ADD`
- [x] `SUPERSEDE` 分支：Gemini Flash 在判定同一次呼叫中，以**重新摘要**（非逐字串接）方式生成新記憶的 `premise`/`conclusion`，整合舊結論、新資訊、修正後結論
- [x] 新增 `CONFLICT_DETECTED` 決定值：判定為邏輯矛盾時拒絕寫入，回傳 `decision="conflict_detected"` + 舊記憶 `doc_id`/內容，不寫入任何資料
- [x] `save_memory` 回傳結構需能表達 `conflict_detected` 與 `requires_registration`（見 2.2）兩種新的決定值。**實作細節**：候選名單改為多筆（`GateCandidate` 清單），`GateClassifier.classify` 介面同步改為一次帶入整份候選清單（而非單一候選）讓 Gemini Flash 一次判斷要匹配哪一筆（`matched_memory_id`），較貼合 Proposal「一律呼叫 Gemini Flash 判定」的單次呼叫語意，也避免逐一候選輪詢 LLM 的延遲與成本；已用假物件跑過 8 種情境（去重、標籤命中強制升級 LLM、精確比對短路、SUPERSEDE/CONFLICT_DETECTED/UPDATE 命中回填、LLM 回傳不存在的 id 時視為異常直接拋錯而非靜默誤判）全數通過

### 2.4 Tool Description 全面更新（5.0、5.1、5.2、5.6、5.7）
- [x] `reflect_on_task` 移除，其用途併入 `save_memory` 說明（因＝任務過程與成因，果＝經驗教訓）
- [x] `save_memory`/`search_memories` 的 `tags` 參數說明擴大範圍：除精確技術字串外，**必須**引導 AI 提取核心主題實體（人名、食物、具體事物、關鍵概念）——這是標籤交集衝突偵測能否運作的前提
- [x] `save_memory` 新增**不可協商的硬性規則**：回傳 `conflict_detected` 時，AI 必須暫停並詢問使用者要覆蓋還是並存，不可自行判斷執行 `forget_memory` 或重新寫入
- [x] `save_memory`/`search_memories`/`load_pinned_memories` 的 `domain` 參數 description 需說明：填未註冊值會收到 `requires_registration`，此時 AI 必須暫停並徵得使用者同意才可呼叫 `register_domain`（不可自行判斷觸發，比照 `CONFLICT_DETECTED` 等級的硬性規則）
- [x] `register_domain` description 明訂：呼叫前必須先參考已注入的既有 domain 清單，若語意重疊應建議使用者沿用既有 domain，而非新建
- [x] 八個工具（`save_memory`/`search_memories`/`forget_memory`/`pin_memory`/`unpin_memory`/`load_pinned_memories`/`list_domains`/`register_domain`）的 description 統一過一輪，確認符合 Proposal 5.0 的七項撰寫原則。已驗證動態 domain 清單注入機制在改寫後仍正常運作

> **agy code review 修正（2.2/2.3 範圍內）**：獨立驗證後採納全部 4 項發現並修正——
> 1. `find_pinned` 原本拉取整個 domain 全部記憶到記憶體再過濾 `is_pinned`/`status`，改為三個等值過濾下沉到 Firestore 查詢（`domain`/`is_pinned`/`status` 皆為等值過濾，Firestore 原生支援索引合併，不需新建複合索引），移除因此變成無用程式碼的 `_query_by_domain`。
> 2. **（真實邏輯 bug）** `save_memory` 候選合併邏輯用 `merged.setdefault(...)` 合併標籤軌道結果，若某候選同時被向量軌道（低相似度）與標籤軌道命中，會靜默遺失「這筆也是標籤命中」的資訊，導致 `_all_below_threshold_without_tag_hit` 誤判為可跳過 LLM 直接 `ADD`，完全繞過 Proposal 5.1 軌道 B 設計要防範的「低相似度但主題相關的修正型衝突」偵測。修法：`GateCandidate` 新增 `is_tag_hit` 旗標，合併時若候選已存在改用 `dataclasses.replace` 標記 `is_tag_hit=True`（保留原向量相似度），而非用 `setdefault` 靜默跳過。已補上重疊情境的迴歸測試。
> 3. `ensure_domain_registered` 原本每次呼叫都 `list_all()` 拉取全部 domain 文件比對，改為先用 `find_by_name`（O(1) 單文件查詢，因為 Firestore 文件 ID 即正規化後的 domain 名稱）查詢 happy path，只在未註冊時才 `list_all()` 組錯誤訊息用的清單。
> 4. `_query_by_tags` 的 `array-contains-any` 未限制陣列長度，AI 若一次帶入超過 Firestore 限制的標籤數會讓查詢直接崩潰；改為用具名常數 `config.FIRESTORE_ARRAY_CONTAINS_ANY_LIMIT`（保守值 10）截斷。
>
> 全部修正已用假物件（fake repository/embedding/gate classifier）驗證，含針對 #2 的重疊情境迴歸測試；`find_pinned`/`_query_by_tags` 兩項因需要真實 Firestore 才能驗證查詢語法本身是否可執行，留給 2.5 整合測試階段確認。

> **第二輪 agy code review 修正**：獨立驗證後採納 2 個 Critical、5 個 Warning、2 個 Suggestion（1 個 Suggestion 因報告本身建議「優先度低、不影響正確性」而不動；1 個 Suggestion 報告本身也建議不合併，維持現狀）——
> 1. **（Critical，真實邏輯 bug）** `_apply_supersede` 完全沒套用 `verdict.merged_title/merged_premise/merged_conclusion`，直接把使用者這次的原始輸入存成新記憶，連呼叫 LLM 前算好的舊 embedding 都原封不動沿用——對照 `_apply_update` 有正確套用 `verdict.merged_*`，確認是實作遺漏而非設計選擇，違反本節「SUPERSEDE 以重新摘要方式生成新記憶」的規格。修法：`_apply_supersede` 改吃 `verdict`，套用 `merged_*`（無則退回原始 request 內容）後才重新計算 embedding 並寫入；`_apply_verdict` 呼叫處同步改傳 `verdict`。
> 2. **（Critical）** `_find_candidate` 在 LLM 漏填/虛構 `matched_memory_id` 時會 `raise ValueError`（`gemini_gate_classifier.py` 的 `_RESPONSE_SCHEMA` 只把 `decision` 列為 required，`matched_memory_id` 並非強制），但 `save_memory` 的工具 handler 沒有 try/except 包住 `execute()`，會被 MCP SDK 包成 `UnexpectedToolError` 抹除訊息（同 2.2 節記載過的情境）。修法：`save_memory`/`register_domain` 兩個工具 handler 都加上 `except ValueError as error: raise ToolError(str(error)) from error`，比照 `search_memories`/`load_pinned_memories` 既有的 `DomainNotRegisteredError` 轉包模式。
> 3. `interface/mcp_server.py` 有一個未使用的 `ensure_domain_registered` import（三個 use case 內部各自呼叫，interface 層沒有直接用到），直接刪除。
> 4. **（與 #2「setdefault 靜默丟失軌道命中資訊」同一類 bug，換了個位置）** `search_memories_use_case.py` 的 `_merge` 對精確標籤命中一樣用 `merged.setdefault(...)`，若某記憶同時被向量軌道（低相似度）與 `exact_tags` 命中，會保留較低的向量分數而非精確命中應有的 1.0，可能被衰減排序擠出 `limit` 之外，違背 `exact_tags`「確保字面精確命中」的設計目的。修法：改成直接覆寫 `merged[memory.id] = ScoredMemory(memory, _EXACT_TAG_MATCH_SIMILARITY)`。
> 5. `domain`/`name` 相關參數沒有邊界檢查，空字串或純空白正規化後會變成 `""`，一路傳到 Firestore `document("")` 才拋出非預期例外（不是 `DomainNotRegisteredError`，不在既有例外轉換保護範圍內，一樣會被 SDK 抹除訊息）。修法：`tool_schemas.py` 相關欄位加 `min_length=1`（擋純空字串），`ensure_domain_registered`/`RegisterDomainUseCase` 加正規化後的空白守衛（擋純空白字串），`register_domain` handler 同步補上 #2 的 `ValueError` → `ToolError` 轉包。
> 6. `firebase_admin` 的 `try: get_app() except ValueError: initialize_app()` 初始化區塊在兩個 repository 與遷移腳本共重複 3 次，抽成 `infrastructure/firebase_app.py` 的 `ensure_initialized()` 共用函式。
> 7. `_apply_verdict` 的 NOOP/UPDATE/SUPERSEDE/CONFLICT_DETECTED 四分支原本各自重複呼叫 `_find_candidate`；改為先判斷 `ADD` 提早 return，其餘四種分支共用一次 `_find_candidate` 呼叫後再分派（與 #1 的修法一併調整）。
> 8. `SaveMemoryDecision` enum 大小寫混用（舊值大寫、`conflict_detected`/`requires_registration` 小寫）是刻意逐字對齊 Proposal 定案字面值，非疏漏，加一行註解避免被誤當疏漏「順手」改掉；`SaveMemoryDomain`/`SearchMemoriesDomain` 的參數說明比照 `LoadPinnedMemoriesDomain` 已有的作法，改用「同 xxx 說明」引用手法降低重複文字。
>
> 全部修正已用假物件驗證（含 SUPERSEDE 正確套用/退回 merged 內容、embedding 確實用合併後文字重算、ValueError 確實被包成 ToolError 且訊息保留、`_merge` 精確命中正確覆寫為 1.0、純空白 domain 在打到 Firestore 前就被擋下）。**未採納**：`_DomainDescriptionCache` 加鎖（報告本身標註優先度低、不影響正確性）；合併三處 domain+global 的 `asyncio.gather` 樣板（報告本身評估後不建議）。**`Domain.describe` → `description` 改名**（報告中另一項 Warning）經 PM 確認後採納，見上方 2.1 節新增項目。

### 2.5 重新部署與整合測試
- [x] 上述程式碼變更完成後（含 2.2 的 domain 遷移腳本先執行一次），`git push` → GCE `git pull` → `systemctl restart mnemosyne`（部署基礎設施已就緒，見上方說明，只需重新部署，不需重建）

> 首次真實部署疊了五個環境/函式庫層級的問題（跟程式碼邏輯無關），逐一排查後全部解決：`google-api-core==2.35.0` regression 導致 Firestore 查詢全面 400（鎖版 2.34.0）、GCE VM access scope 與 IAM 角色是獨立限制（補上 `cloud-platform` scope）、跨專案需明確指定 `firebase_admin` 的 `projectId`（見 2.2 節）、MCP SDK `sse_app` 預設 DNS Rebinding 防護擋掉 Cloud Run 代理轉發的請求（新增 `MNEMOSYNE_DISABLE_DNS_REBINDING_PROTECTION`/`MNEMOSYNE_ALLOWED_HOSTS` 環境變數）、`fintarck-proxy` 的 `nginx.conf` 缺少 `/mnemosyne/` 分流且 `proxy_pass` 需補斜線去除路徑前綴。排查細節與各問題的診斷方式記錄於 `CLAUDE.md`「Deployment gotchas」章節，不重複寫在這裡。連線驗證：`curl .../mnemosyne/sse?key=<KEY>` 已確認回 `200`。

- [x] 整合測試：跨 domain 隔離、`save_memory` 未註冊 domain 回傳 `requires_registration`、`search_memories`/`load_pinned_memories` 未註冊 domain 觸發 `DomainNotRegisteredError`（確認 MCP Client 收到 `isError=True`）、`register_domain` 人工確認流程、正規化去重（大小寫/空白）、寫入閘門三種結果（重複/全新/衝突）、`CONFLICT_DETECTED` 觸發後 AI 是否確實暫停詢問使用者、標籤交集能否抓到低相似度但主題相關的衝突案例（例如：喜好類陳述的修正）、既有資料遷移後可正常存取（含 `"global"` seed 是否生效）、`list_tools` 快取是否確實降低 Firestore 讀取次數

> **實測過程額外發現並修正的問題**（連線層/基礎設施，非邏輯 bug）：① `mcp.server` 的舊版 `sse_app()` 在 `/mnemosyne/` 路徑前綴代理下會壞（相對路徑重導向解析錯誤，導致 Claude Desktop 誤判為需要登入），改用 `streamable_http_app()`（單一 `/mcp` 端點）解決；② `mnemosyne-cb868` 專案初期未掛計費帳戶，Vertex AI 呼叫一律 403 BILLING_DISABLED（Firestore Spark 方案不需要，但 Vertex AI 需要），已掛上帳單帳戶，Proposal 3.2 節已記錄待評估免計費替代方案；③ `gemini-2.5-flash` 在 `asia-east1` 無法使用（Gemini 系列模型可用區域比 embedding 窄很多），新增獨立的 `MNEMOSYNE_GEMINI_CLASSIFIER_LOCATION`（預設 `us-central1`），embedding 維持 `asia-east1` 不受影響。
>
> **透過 Claude Desktop 實際串接 MCP Client 跑完全部整合測試項目，結果**：跨 domain 隔離 ✅、未註冊 domain 兩種攔截形狀（`requires_registration`/拋例外）✅、`register_domain` 正規化去重（冪等、不覆蓋既有描述）✅、寫入閘門 ADD/NOOP/UPDATE ✅（UPDATE 合併品質佳，是真正重新摘要而非串接）、pin/unpin/load_pinned_memories ✅、`exact_tags` 精確命中（語意完全無關的查詢也能靠標籤命中）✅、`list_tools` 動態注入已註冊 domain 清單 ✅。
>
> ⚠️ **發現問題並已修正**：`CONFLICT_DETECTED` 分支第一輪實測**沒有觸發成功**——餵入邏輯矛盾案例（先前記錄「愛吃牛肉麵」，後餵入「宗教因素完全禁食牛肉」，標籤刻意重疊以確保進入候選名單）時，LLM 判成 `SUPERSEDE`（自動覆蓋，附重新摘要說明推翻原因）而非 `CONFLICT_DETECTED`（暫停詢問使用者）。程式碼邏輯本身正確執行了 SUPERSEDE 分支，問題出在 prompt 對「單方說法更新」與「需要人工確認的矛盾」邊界判斷偏向前者，且 `SUPERSEDE` 範例清單（電話/地址/版本號）沒有明確排除「人員/職務歸屬」「偏好/能力/允許狀態」這類高風險欄位。
>
> **已與 agy 討論定案並修改 `gemini_gate_classifier.py` 的 prompt**：`SUPERSEDE` 收斂為僅限「純技術/聯絡類中繼資料的無爭議更迭」（電話、地址、Email、軟體版本號、序號）；只要涉及人員/職務/責任歸屬，或使用者的偏好、能力、允許狀態被否定/推翻，一律歸類 `CONFLICT_DETECTED`，不因表面上「像是單一值被取代」就自動放行。**尚待重新部署後用實際案例（含 agy 提供的 4 組對照測試）重新驗證行為符合預期**，見下方部署清單。

### 2.6 CI/CD 自動化（延後，不影響上線）
- [ ] 比照 NoCode_Project `deploy-python-backend.yml` 新增 `deploy-mnemosyne.yml`：偵測 `MCP_Service/**` 變更 → SSH 進 GCE → `git pull` → 安裝依賴 → `systemctl restart mnemosyne`

---

## Phase 3：記憶自動精煉

> ⚠️ **待確認事項**：AI 在對話中如何判斷「值得存」的時機，仍是未經實測驗證的啟發式規則，需在 2.5 整合測試階段實際觀察並調整 Tool Description 措辭。

- [ ] 設計 Agent prompt 範本，引導 AI 在對話中自動判斷「值得存」的時機並精煉成因果結構（`premise`/`conclusion`，≤500 字）
- [ ] 實作「會議記錄存檔」與「每日決策報告存檔」自動觸發流程

---

## Phase 4：記憶治理與跨專案優化

> ⚠️ **待確認事項**：定期固化（6.3）的觸發方式（排程自動 vs 手動觸發）與觸發門檻（時間 vs 數量）尚未定案，動工前需先決定。

- [ ] 決定定期固化的觸發機制與門檻條件，實作批次流程（掃描候選 → LLM 歸納摘要 → 原始記憶標記 `archived`）
- [ ] 依實際使用資料回頭調校衰減排序權重與 `λ`（6.1）
- [ ] 理財專案前端（Azure Static Web App）新增「記憶庫管理」Dashboard：檢視記憶列表與狀態、手動封存/硬刪除
- [ ] 評估擴展到其他個人專案（如生活筆記類 `domain="life"`）
