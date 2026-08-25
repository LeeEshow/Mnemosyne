# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節，實作時應回頭核對該章節的詳細規格。
> 勾選代表完成，未完成項目維持未勾選。

---

## 0. 程式碼架構模式（Architecture Pattern）

> 討論脈絡：技術棧定案為 Python + FastAPI（見 Proposal 3.2），主要理由是與既有 `NoCode_Project`（fintarck-backend）共用 GCE 主機、部署模式一致，而非 Python 本身在 MCP 生態系上佔優勢——事實上 MCP 官方 TypeScript SDK 才是生態系主流。為了不把「未來若要換成 Node.js」的成本鎖死在框架選擇上，開發時採用 **Hexagonal Architecture（Ports & Adapters）**，把業務邏輯與 Python/GCP 框架細節實體隔離。此決策同時符合 dev-core-hub 團隊 Layer 1 開發原則中「核心業務邏輯內聚於 Domain 層」「Repository 介面宣告於 Domain 層」「依賴方向單向指向 Domain 核心」等規範。

**目錄結構**：

```
MCP_Service/
├── domain/                     # 零框架依賴，未來逐檔翻譯成 TS 即可
│   ├── models.py                # Memory 等不可變值物件（frozen dataclass）
│   ├── scoring.py                # 衰減排序公式（6.1）—— 純函式
│   ├── write_gate_policy.py      # 三段式判定規則（5.1）—— 純函式 + 門檻常數
│   └── ports/                    # 抽象介面（typing.Protocol）
│       ├── memory_repository.py  # save / find_nearest / update 等介面
│       ├── embedding_provider.py # embed(text) -> vector 介面
│       └── gate_classifier.py    # classify(candidates) -> Decision 介面
│
├── application/                # Use case 協調層，依賴注入 port，無框架細節
│   ├── save_memory_use_case.py
│   ├── search_memories_use_case.py
│   └── ...
│
├── infrastructure/              # 具體實作，綁死 Python/GCP —— 未來遷移只重寫這層
│   ├── firestore_memory_repository.py
│   ├── vertex_embedding_provider.py
│   └── gemini_gate_classifier.py
│
└── interface/                   # MCP/FastAPI 入口，只做 I/O 轉換
    ├── mcp_server.py             # tool 註冊、SSE transport
    └── tool_schemas.py           # Pydantic model 僅存在這一層
```

**三條硬規則**：
1. `domain/` 底下**禁止 import** FastAPI、Pydantic、firebase-admin、google-genai 等任何第三方框架/SDK，只能用標準庫。
2. Port 一律用 `typing.Protocol` 定義（結構化介面），對應到 TS 就是直接寫成 `interface`。
3. `interface/` 層（MCP tool handler）只做「解析輸入 → 呼叫 use case → 格式化輸出」，不寫任何業務判斷。

---

## Phase 1：基礎建設（資料庫 + 專案骨架）

### 1.1 GCP / Firestore 建置
- [x] 申請獨立的 GCP 專案（不與 `nocode-finance` 共用）（3.3）—— 專案 `mnemosyne-cb868`
- [x] 在新專案啟用 Firestore（Spark 免費方案）與 Native Vector Search —— Standard 版、`asia-east1`
- [ ] 建立 `memories` 集合，實作完整 Schema（4 章）：（Firestore 無結構描述，欄位會在第一次寫入時自動產生，待 Phase 2 程式串接後驗證）
  - [ ] 必填欄位：`type`、`domain`、`title`、`context`、`embedding`、`created_at`、`importance_score`、`is_pinned`、`status`
  - [ ] 選填欄位：`tags`、`source_id`、`superseded_by`、`last_accessed_at`、`access_count`
- [x] 建立唯一複合索引：`domain (ASC) + status (ASC) + embedding (Vector, 768維, COSINE)`（4.1）
- [x] 設定服務帳戶存取權（**改用 GCE 附加身分，非金鑰檔案**）：組織政策 `iam.disableServiceAccountKeyCreation` 禁止建立金鑰，改為將 GCE Compute Engine 預設服務帳戶（`1077248196503-compute@developer.gserviceaccount.com`）於 `mnemosyne-cb868` 專案 IAM 中授予 **Cloud Datastore User** 角色，跨專案存取 Firestore，免金鑰檔案，安全性優於原規劃

### 1.2 Python 專案初始化
- [x] 建立專案資料夾 `MCP_Service`（與 `Docs/` 平行）
- [x] 依「0. 程式碼架構模式」建立 `domain/` / `application/` / `infrastructure/` / `interface/` 資料夾骨架
- [x] 初始化 Python 專案，安裝 `fastapi`, `firebase-admin`, `mcp` 等套件（`pyproject.toml` + `.venv`）—— 開發機僅有 Python 3.13.1，非規劃的 3.14，`pyproject.toml` 已改為 `requires-python = ">=3.13"`，待正式環境確認 3.14 可用性後再評估是否升級
- [x] 串接 Google Embedding API（`text-multilingual-embedding-002`）（3.2）—— `infrastructure/vertex_embedding_provider.py`，透過 `google-genai` SDK 以 `vertexai=True` 模式呼叫，沿用 1.1 已設定的 GCE 附加身分（ADC），程式碼已完成並通過 import 驗證，但**尚未實測真正呼叫**（本機非 GCE 環境無憑證）；正式測試需等 2.3 部署上線後進行，且**待確認**服務帳戶是否已授予 Vertex AI 存取角色（1.1 目前僅授權 Cloud Datastore User）
- [x] 串接 Gemini Flash API（供寫入閘門判定使用）（3.2、5.1）—— `infrastructure/gemini_gate_classifier.py`，同樣透過 `google-genai` + ADC 呼叫，內含基礎判定 prompt（NOOP/UPDATE/SUPERSEDE/ADD），限制與待確認事項同上一項；prompt 細節與門檻整合留待 Phase 2（2.1 save_memory）依實測調整
- [x] 建立 `config.py`，集中管理以下起始參數（3.2、5.1、6.1）：
  - [x] `LOW_THRESHOLD = 0.85`（寫入閘門相似度低閾值）
  - [x] 衰減排序權重 `w1=0.50 / w2=0.25 / w3=0.15 / w4=0.10`
  - [x] 衰減常數 `λ ≈ 0.0077`（半衰期 90 天，以天為單位）
  - [x] 向量查詢 K 值：domain 軌道 K=40、global 軌道 K=10（5.2）
  - [x] 常駐清單預設上限 `limit=5`（5.6）

---

## Phase 2：MCP Server 開發與部署

### 2.1 核心工具實作（5 章）

> **Tool Description 定稿**（撰寫原則見 Proposal 5.0，直接供 `interface/tool_schemas.py` 使用；措辭如有調整以此處為準）：
>
> - **`save_memory`**：「當對話中出現值得長期保存的新事實、重要決策、個人偏好或代碼知識點時呼叫。已內建重複偵測與合併更新機制，若只是要新增資訊，呼叫前不需要先以 search_memories 確認是否重複。如果是任務完成後的經驗反思，請改用 reflect_on_task。注意：寫入範圍將自動被綁定在當前連線的領域下。」
>   - `tags` 參數：「(array of strings, optional) 關聯的標籤數組。如果記憶內容涉及特定的技術術語、錯誤代碼（如 'ERR_CORS'）、函式名（如 'get_user'）、股票代號（如 '0056'）等精確字串，務必將這些關鍵字也作為獨立的標籤存入此陣列，以利日後進行精確匹配檢索。」
> - **`search_memories`**：「當使用者的提問涉及過去的討論、決策、偏好，或你需要當前對話沒有的歷史脈絡時呼叫。注意：你的檢索範圍已自動鎖定於當前連線的領域與全域通用偏好（例如回覆語言格式），查無結果不代表使用者從未提過，可能只是超出目前可讀取的範圍。若查詢包含特定的錯誤代碼、函式名、股票代號等精確字串，請務必填入 exact_tags 參數以確保字面精確命中。」
>   - `exact_tags` 參數：「(array of strings, optional) 需要精確比對的關鍵字，用於觸發精確匹配軌道，彌補向量檢索對精確字串比對能力較弱的問題。當查詢中包含精確的技術字串（如錯誤代碼 'ERR_404'、函式名稱 'calculate_tax'、股票代號 '0056' 等）時，務必將這些關鍵字傳入此參數。」
> - **`reflect_on_task`**：「當一項任務取得明確結果（測試通過、部署成功、修法失敗需回退等），且該結果帶有可類推的經驗教訓時，於回報結果的同一回合呼叫；或當使用者切換到全新話題、而前一個話題確實是一項已收斂的任務或決策時，先呼叫此工具收尾再回覆新話題。純粹的問答、閒聊、或沒有意外/教訓價值的例行成功動作不需要呼叫。與 save_memory 的差異：這是任務執行結果的回顧，不是討論中即時記錄的事實或偏好。」（觸發時機仍屬未驗證的啟發式規則，見下方 Phase 3 待確認事項）
> - **`forget_memory`**：「將某筆不再正確或已無用的記憶進行封存或刪除。你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫此工具。」
> - **`pin_memory`**：「將某筆記憶標記為常駐記憶，確保其之後一定會出現在對話開頭的常駐清單（load_pinned_memories）中。只在該記憶被判定為『極端重要、不能被一般排序稀釋』時使用，避免濫用造成常駐清單膨脹。你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫。」
> - **`unpin_memory`**：「取消某筆記憶的常駐標記。你必須先知道該記憶的 doc_id（可透過 search_memories 或既有的常駐清單得知）才能呼叫。」
> - **`load_pinned_memories`**：「對話開始時呼叫一次，取得少量常駐記憶直接帶入上下文；不需要在同一次對話中重複呼叫多次。」

- [x] `save_memory`：三段式同步寫入閘門（字串完全相同 → NOOP；相似度 <0.85 → ADD；0.85~1.0 → 呼叫 Gemini Flash 判定 NOOP/UPDATE/SUPERSEDE/ADD）（5.1）—— `domain/write_gate_policy.py`（純函式）+ `application/save_memory_use_case.py`（協調）
  - [x] `UPDATE` 分支需重新計算並覆寫 `embedding`
  - [x] `SUPERSEDE` 分支需寫入 `superseded_by`
- [x] `search_memories`：雙軌並行檢索 + 衰減排序（5.2、6.1）—— `domain/scoring.py` + `application/search_memories_use_case.py`
  - [x] 向量軌道：`asyncio.gather` 並行查詢 `domain` 與 `global`
  - [x] 精確匹配軌道：`tags array-contains-any exact_tags`
  - [x] 合併去重 → 套用衰減排序公式 → 回傳 Top-K
  - [x] 支援 `include_superseded` / `include_archived` 深度搜尋開關
  - [x] 命中記憶更新 `last_accessed_at` / `access_count`
- [x] `forget_memory`：預設軟刪除（`status="archived"`），`hard_delete=true` 才真刪（5.3）
- [x] `pin_memory` / `unpin_memory`：更新 `is_pinned`（5.5）
- [x] `load_pinned_memories`：查詢 `is_pinned==true` 常駐清單（5.6）

> **實作補充說明**：以上 5 個工具已在 `interface/mcp_server.py` 完整註冊（含 Tool Description 與參數 schema），並以假物件（fake repository/embedding/classifier）跑過端到端整合測試驗證全部分支（ADD/NOOP/雙軌合併/exact_tags/pin/unpin/軟刪除/硬刪除），管線邏輯正確；但**尚未對接真實 GCP**（本機無 ADC，同 1.2 的已知限制）。過程中補了幾個 Proposal 未明訂細節的實作決策，記錄於此供之後檢視：
> - `GateClassifier.classify()` 改為回傳 `GateVerdict`（決定 + 合併後標題/內容），而非單純決定值——因為 `UPDATE` 分支需要「合併後的內容」才能重新計算 embedding，設計為 Gemini 在判定 UPDATE 的同一次呼叫中一併輸出合併內容（`infrastructure/gemini_gate_classifier.py` 用 `response_schema` 結構化輸出），避免多一次 LLM 呼叫。
> - 寫入閘門查詢最近鄰數量取 `WRITE_GATE_CANDIDATE_LIMIT=1`（僅取單一最近鄰），對應 5.1「最近鄰相似度」的字面單數解讀；`config.py` 已留常數，之後若發現需要比對多個候選可再調整。
> - 字串完全相同的「正規化」規則（5.1 未定義細節）暫定為 trim + 轉小寫 + 摺疊空白（`normalize_for_exact_match`）。
> - 衰減排序中 `importance_score`（1-10）正規化為 `(score-1)/9`；`access_frequency` 採 `log1p(count)/log1p(cap)` 對數縮放，`cap` 新增常數 `ACCESS_FREQUENCY_LOG_CAP=20`（6.1 僅說明「對數縮放與正規化」未給精確公式/上限，此為暫定值，待實測調整）。
> - 精確匹配軌道（`exact_tags`）比照向量軌道延伸為 domain + global 雙軌並行查詢（5.2 原文未明講是否比照雙軌，此處基於 domain/global 隔離設計精神統一），命中僅靠標籤、無向量相似度者，衰減公式的 relevance 分量以 `1.0` 代入。
> - Firestore 查詢策略：`find_nearest` 依 `status_filter` 展開的每個狀態各發一次向量查詢再合併（對應 4.1 唯一複合索引 `domain+status+embedding`，避開 `status IN [...]` 與向量查詢混用的相容性風險）；`find_by_tags`／`find_pinned` 則刻意只用單欄位查詢（`tags array-contains-any` / `domain ==`），`domain`/`status`/`is_pinned` 過濾全部下放到 Python 端做，避免額外複合索引需求（比照 4.1 對 tags track 的設計精神）。**這個假設（多重 equality 過濾在應用層而非 DB 端做）待 2.4 實際打 Firestore 時應一併驗證是否真的完全不需要新建索引。**
> - `interface/mcp_server.py` 目前用環境變數 `MNEMOSYNE_DOMAIN` 暫代 `domain` 綁定（`_resolve_domain()`），這是 2.2 尚未實作前的權宜作法，2.2 完成後需替換為真正的連線層級 URL 解析。
> - `reflect_on_task` 未實作，維持 Task.md 既有安排歸入 Phase 3。
> - 同 1.2：服務帳戶是否已有 Vertex AI 存取權限仍待確認（目前僅授權 Cloud Datastore User）。

### 2.2 連線層級 domain 綁定
- [x] 實作從 MCP 連線 URL 的 query string 解析 `domain`（3.3）—— `interface/connection_context.py`
- [x] 確保單一連線建立後，該連線後續所有 tool 呼叫自動套用綁定的 `domain`，不需 AI 每次傳入

> **實作補充說明**：這個功能比預期棘手，記錄一下走過的彎路避免以後重踩。原本想法很直覺——用一個 `contextvars.ContextVar` 在 ASGI middleware 讀 query string 綁一次，以為後續同連線的 tool 呼叫都在同一個 asyncio task 底下執行就能一路讀到；實測發現完全不成立，追進 MCP SDK（`mcp==2.1.0`）原始碼後確認是刻意設計如此：SSE transport 建立連線時的 GET 請求（帶完整 query string，含 `domain`）之後，同一連線的每一次工具呼叫其實是走另一支 `POST /messages/?session_id=...`（只帶 `session_id`，不帶 `domain`），而 SDK 內部（`mcp.server.runner._sender_context`）會用「訊息送入當下」的 context 覆寫掉執行環境，所以工具呼叫當下讀到的 contextvars 其實是那次 POST 請求自己的（`domain` 必為空），不是原本 GET 連線時綁的值。
> 最後採用的正確機制是 `MCPServer(lifespan=...)`：這個 lifespan 是在每次 SSE 連線建立（也就是那次帶 `domain` 的 GET 請求）當下進入一次，其回傳值會經由 `ctx.request_context.lifespan_context` 傳給該連線後續所有工具呼叫，不受上述訊息級 context 覆寫影響。所以最終設計是：`connection_context.DomainBindingMiddleware`（ASGI 層）在請求進來時把 query string 的 `domain` 綁進 `contextvars`，`mcp_server.py` 的 `_bind_connection_domain`（lifespan）在連線建立當下讀走這個值並包進 `lifespan_context`，各工具再透過 `ctx: Context` 參數的 `_resolve_domain(ctx)` 取得。
> **已用真實 HTTP/SSE 驗證**（非假物件）：起一個真的 `uvicorn` server，用 `mcp.client.sse` 開兩條並行連線（`?domain=coding` / `?domain=finance`），互相呼叫 `load_pinned_memories`，確認彼此只看得到自己 domain 的資料、完全沒有互相污染；另外重複建立/關閉連線多次確認新連線不會殘留舊連線的綁定。這是目前整個專案裡驗證得最扎實的一塊。
> 副作用：`interface/mcp_server.py` 的 ASGI 進入點也一併定案為 `app`（`DomainBindingMiddleware` 包住 `mcp_server.sse_app()`），`main()` 改用 `uvicorn.run(app, port=config.SERVER_PORT)`（對應 3.3.1 的 `:8001`），2.3 的 systemd 設定可以直接指向這個 `app`。
> 待確認：`key`（`MNEMOSYNE_MCP_KEY`）身分驗證完全還沒做，仍屬 2.3 範圍。

### 2.3 部署上線

> ⚠️ **待 SE 補做的前置項**：`interface/mcp_server.py` / `interface/connection_context.py` 目前只有 `domain` 綁定的 middleware，**完全沒有 `MNEMOSYNE_MCP_KEY` 的驗證邏輯**——任何人知道網址就能連線，無存取控制。PM 與使用者已確認順序：**先由 SE 補上金鑰驗證，再一起部署**，避免部署兩次（先上無驗證版本、之後又要為了加驗證重新 redeploy）。以下 GCP 側步驟（systemd/防火牆/Nginx/Cloud Run）與金鑰驗證程式碼可以並行準備，但**正式上線（Cloud Run 重新部署）需等驗證邏輯合併後**才進行，避免服務曝露在無驗證狀態。
>
> 建議實作方式（供 SE 參考，非強制）：在 `DomainBindingMiddleware` 同一層或新增一個 ASGI middleware，於請求進入時比對 query string 的 `key` 是否等於環境變數 `MNEMOSYNE_MCP_KEY`，不符則回應 401/403 並中斷，不進入後續 domain 綁定與工具邏輯。

- [x] **[SE]** 實作 `MNEMOSYNE_MCP_KEY` 存取金鑰驗證邏輯（比照既有 `MCP_ACCESS_KEY` 模式，query string 比對）—— `interface/key_auth_middleware.py`
  - [ ] **[SE][待修]** PM Review 發現：`_is_authorized` 只擋 `expected_key is None`，未擋空字串——若 `MNEMOSYNE_MCP_KEY` 被誤設為 `""`，`?key=`（空值）會通過 `hmac.compare_digest("", "")` 比對成功，跟文件聲稱的 fail-safe 語意不符。修正為 `if not self._expected_key or not provided_key: return False`。非阻斷性，可跟其他小修一起處理。
- [ ] **[PM]** 於 GCE 主機建立專屬 SSH Deploy Key（唯讀），供拉取 Mnemosyne Private repo（NoCode_Project 為 Public repo 免驗證，Mnemosyne 需另外設定）
- [ ] **[PM]** GCE 新增 `mnemosyne.service`（systemd，`Type=simple`——與 `fastapi.service` 的 `Type=notify` 不同，因程式碼未整合 sd_notify），監聽 `:8001`（3.3.1）
- [ ] **[PM]** GCE 新增防火牆規則開放 `tcp:8001`（3.3.1）
- [ ] **[PM]** 修改 `fintarck-proxy` 的 `nginx.conf`，新增 `/mnemosyne/` location block 轉發至 `:8001`（3.3.1）
- [ ] **[PM]** 透過 Cloud Run 重新部署 `fintarck-proxy`（待金鑰驗證邏輯合併後才執行——**SE 端已完成，可以進行**）

> **範圍界定**：本次先完成**手動初次部署**（確認服務能實際跑起來），比照 NoCode_Project `deploy-python-backend.yml` 的 GitHub Actions CI/CD 自動化**延後到 2.5**，避免同時處理太多變數難以排查問題。

> **[SE] 實作補充說明**：`KeyAuthMiddleware` 包在 `DomainBindingMiddleware` 外層（`app = KeyAuthMiddleware(DomainBindingMiddleware(mcp_server.sse_app()), ...)`），驗證失敗直接回 401、不進入 domain 綁定與工具邏輯。比對邏輯延續 2.2 已確認的限制：只在「還沒有 `session_id`」的請求（也就是建立 SSE 連線的 GET 請求，唯一會帶 `key` 的請求）上比對金鑰；`POST /messages/?session_id=...` 本身不帶 `key` 是 transport 本身的正常行為（同 domain 綁定），但因為 `session_id` 是 128-bit UUID4、且只有先通過驗證才拿得到，所以放行有 `session_id` 的請求不會開後門。用 `hmac.compare_digest` 比對避免時序攻擊；環境變數 `MNEMOSYNE_MCP_KEY` 未設定時走「fail-safe」——直接全部拒絕，不是當機也不是誤放行（刻意不在 import 當下拋錯，避免影響本機測試/未來的自動化測試）。
> **已用真實 HTTP/SSE 驗證**：起真的 `uvicorn` server，分別用「不帶 key」「帶錯誤 key」「帶正確 key」三種情況實測連線，前兩者確認收到 401 且連線失敗，後者確認能完整走完 `initialize` 並成功呼叫工具；另外單獨驗證了 `MNEMOSYNE_MCP_KEY` 未設定時的 fail-safe 行為。

### 2.4 整合測試
- [ ] 以 `.../mnemosyne/mcp?key=<KEY>&domain=coding` 連線 Cursor，測試 `save_memory`/`search_memories`
- [ ] 以 `.../mnemosyne/mcp?key=<KEY>&domain=finance` 連線 Claude Desktop 或理財網頁，驗證跨 domain 檢索互相隔離
- [ ] 驗證 `global` domain 的記憶能同時被 `coding` 與 `finance` 連線檢索到
- [ ] 驗證寫入閘門三段判定邏輯（完全重複、明顯不同、模糊地帶各測一組案例）

### 2.5 CI/CD 自動化（延後至手動部署驗證完成後）
- [ ] 比照 `deploy-python-backend.yml` 新增 `deploy-mnemosyne.yml`：偵測 `MCP_Service/**` 變更 → SSH 進 GCE → `git pull`（沿用 2.3 建立的 Deploy Key）→ 安裝依賴 → `systemctl restart mnemosyne`
- [ ] 設定對應 GitHub Secrets（可能沿用既有 `GCE_HOST` / `GCE_USER` / `GCE_SSH_KEY`，或視情況新增專屬金鑰）

---

## Phase 3：記憶自動精煉與經驗學習

> ⚠️ **待確認事項**：2.1 節已為 `reflect_on_task` 定稿了具體的操作時機（結果驅動 + 話題轉換前收尾兩種 hook），但這仍是**未經實測驗證的啟發式規則**，高度依賴實際運作時 AI 的敏感度——可能發生「過度反思」（把瑣碎成功也當作教訓寫入）或「忘記反思」（AI 沒抓到 hook 時機）。動工後需在 2.4 整合測試階段實際觀察觸發頻率與品質，回頭調整措辭。

- [ ] 設計 Agent prompt 範本，引導 AI 在對話中自動判斷「值得存」的時機並精煉成 ≤500 字摘要（含 `why` / `how_to_apply` 結構）
- [ ] 實作「會議記錄存檔」自動觸發流程
- [ ] 實作「每日決策報告存檔」自動觸發流程
- [ ] 實作 `reflect_on_task` 工具（5.4）
- [ ] 驗證經驗學習迴路（6.2）：任務反思記錄能在後續相似任務的 `search_memories` 中被檢索到

---

## Phase 4：記憶治理與跨專案優化

> ⚠️ **待確認事項**：定期固化（6.3）的觸發方式（排程自動 vs 手動觸發）與觸發門檻（時間 vs 數量）尚未定案，動工前需先決定。

- [ ] 決定定期固化的觸發機制（排程/手動）與門檻條件
- [ ] 實作定期固化批次流程：掃描候選記憶 → LLM 歸納摘要 → 原始記憶標記 `archived`
- [ ] 依實際使用資料回頭調校衰減排序權重與 `λ`（6.1）
- [ ] 在理財專案前端（Azure Static Web App，沿用既有網域）新增「記憶庫管理」Dashboard
  - [ ] 檢視記憶列表與狀態（`active`/`superseded`/`archived`）
  - [ ] 手動封存 / 硬刪除操作
- [ ] 評估擴展到其他個人專案（如生活筆記類 `domain="life"`）
