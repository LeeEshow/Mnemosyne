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
    ├── mcp_server.py             # tool 註冊、SSE transport
    ├── connection_context.py     # domain 連線層級綁定
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

- **GCP / Firestore**：獨立專案 `mnemosyne-cb868`（Firestore Standard 版、`asia-east1`）；唯一複合索引 `domain (ASC) + status (ASC) + embedding (Vector, 768維, COSINE)`；GCE 服務帳戶 `1077248196503-compute@developer.gserviceaccount.com` 已授予 `Cloud Datastore User` + `Vertex AI User`（免金鑰檔案，走 GCE 附加身分）。
- **Python 環境**：`MCP_Service/` 專案骨架（依上方架構）；`pyproject.toml` 的 `requires-python = ">=3.10"`（`fintarck-backend` 主機 Python **鎖定 3.10.12**，見 PM 記憶 `project_gce_python_pinned`，**不可**為 Mnemosyne 安裝新版本，程式碼須自行相容，例如禁用 `datetime.UTC`，改用 `datetime.timezone.utc`）。
- **部署基礎設施**：`/app-mnemosyne`（GCE 上的 Private repo clone，SSH Deploy Key `~/.ssh/mnemosyne_deploy` + SSH config alias `github-mnemosyne`，唯讀）；`mnemosyne.service`（systemd，`Type=simple`，監聽 `:8001`，`.env` 存 `MNEMOSYNE_MCP_KEY`/專案 ID/區域）；防火牆規則 `allow-uvicorn-8001`；`fintarck-proxy` 的 `nginx.conf` 已加 `/mnemosyne/` location block 轉發至 `:8001`（3.3.1）。連線網址：`https://fintarck-proxy-1077248196503.asia-east1.run.app/mnemosyne/mcp?key=<KEY>&domain=<domain>`。
- **身分驗證機制**：`KeyAuthMiddleware` 包在 `DomainBindingMiddleware` 外層，只在無 `session_id` 的初始 SSE GET 請求比對 `key`（`hmac.compare_digest`，fail-safe：`MNEMOSYNE_MCP_KEY` 未設定或空字串一律拒絕），驗證通過後同連線後續 `POST /messages/?session_id=...` 免比對（`session_id` 為 128-bit UUID4，需先驗證通過才拿得到）。
- **MCP SDK 連線層級狀態綁定機制（重要，避免重踩）**：MCP SSE transport 的初始 GET 請求（帶 query string）與後續每次工具呼叫的 `POST /messages/?session_id=...`（只帶 `session_id`）是分開的請求，`contextvars` 在工具呼叫當下讀到的是 POST 請求自己的 context，**不會**沿用 GET 當下綁定的值。正確作法是用 `MCPServer(lifespan=...)`：lifespan 在連線建立當下（GET 請求）進入一次，其回傳值經由 `ctx.request_context.lifespan_context` 傳給該連線後續所有工具呼叫，不受訊息級 context 覆寫影響。目前 `domain` 綁定即採此機制（`connection_context.DomainBindingMiddleware` 讀 query string → `mcp_server.py` 的 lifespan 讀走並包進 `lifespan_context` → 工具透過 `ctx: Context` 取得），**已用真實 SSE 連線驗證過跨 domain 隔離正確**。這個機制本身**不需要重做**，Phase 2 只需要擴充它去支援 `allowed_overrides` 白名單（見下方 2.2）。

---

## Phase 2：因果模型改版（重新設計後，以下項目需重新實作）

> **改版脈絡**：PM 與 agy 針對「Domain 使用摩擦」「因果記憶模型」「低相似度衝突偵測」三個主題深入討論後，設計有實質變動（詳見 Proposal 2.3、2.5、4、5 章）。以下列出**需要新做或修改**的項目；未列出的既有機制（如衰減排序公式本身、`forget_memory`、`pin_memory`/`unpin_memory`）維持不變。

### 2.1 Schema 調整
- [ ] Firestore `memories` 集合欄位調整：`context` 拆分為 `because`/`then`；刪除 `source_id`、`last_accessed_at`（Proposal 4 章）
- [ ] `domain/models.py` 的 `Memory` 值物件同步調整欄位

### 2.2 連線層級 domain 綁定擴充白名單覆寫
- [ ] `connection_context` 除了 `domain`，同時解析並綁定 `allowed_overrides`（白名單列表，或 `*` 表示不限）
- [ ] `save_memory` / `search_memories` / `load_pinned_memories` 新增可選參數 `domain_override`：Server 端驗證是否在該連線的 `allowed_overrides` 白名單內，通過則以此值取代連線預設 domain 進行 DB 查詢；未通過則忽略、沿用預設值
- [ ] 沿用既有的 lifespan-context 綁定機制（見上方基礎設施說明），不需重新設計連線狀態綁定本身

### 2.3 寫入閘門重構（5.1）
- [ ] 候選查詢改為**雙軌並行**：軌道 A 向量最近鄰（同 domain，取 Top-3，`WRITE_GATE_CANDIDATE_LIMIT` 從 1 改為 3）＋ 軌道 B 標籤交集（`tags array-contains-any`，同 domain，不受相似度限制），Python 端依 ID 去重合併
- [ ] 判定分流邏輯調整：字串完全相同 → `NOOP`；候選集為空或無交集且相似度 < `LOW_THRESHOLD` → `ADD`；其餘（相似度 ≥ 0.85 **或**存在標籤交集）→ 呼叫 Gemini Flash 判定 `NOOP`/`UPDATE`/`SUPERSEDE`/`CONFLICT_DETECTED`/`ADD`
- [ ] `SUPERSEDE` 分支：Gemini Flash 在判定同一次呼叫中，以**重新摘要**（非逐字串接）方式生成新記憶的 `because`/`then`，整合舊結論、新資訊、修正後結論
- [ ] 新增 `CONFLICT_DETECTED` 決定值：判定為邏輯矛盾時拒絕寫入，回傳 `decision="conflict_detected"` + 舊記憶 `doc_id`/內容，不寫入任何資料
- [ ] `save_memory` 回傳結構需能表達 `conflict_detected` 這個新的決定值

### 2.4 Tool Description 全面更新（5.0、5.1、5.2）
- [ ] `reflect_on_task` 移除，其用途併入 `save_memory` 說明（因＝任務過程與成因，果＝經驗教訓）
- [ ] `save_memory`/`search_memories` 的 `tags` 參數說明擴大範圍：除精確技術字串外，**必須**引導 AI 提取核心主題實體（人名、食物、具體事物、關鍵概念）——這是標籤交集衝突偵測能否運作的前提
- [ ] `save_memory` 新增**不可協商的硬性規則**：回傳 `conflict_detected` 時，AI 必須暫停並詢問使用者要覆蓋還是並存，不可自行判斷執行 `forget_memory` 或重新寫入
- [ ] 六個工具（`save_memory`/`search_memories`/`forget_memory`/`pin_memory`/`unpin_memory`/`load_pinned_memories`）的 description 統一過一輪，確認符合 Proposal 5.0 的七項撰寫原則

### 2.5 重新部署與整合測試
- [ ] 上述程式碼變更完成後，`git push` → GCE `git pull` → `systemctl restart mnemosyne`（部署基礎設施已就緒，見上方說明，只需重新部署，不需重建）
- [ ] 整合測試：跨 domain 隔離、`domain_override` 白名單驗證、寫入閘門三種結果（重複/全新/衝突）、`CONFLICT_DETECTED` 觸發後 AI 是否確實暫停詢問使用者、標籤交集能否抓到低相似度但主題相關的衝突案例（例如：喜好類陳述的修正）

### 2.6 CI/CD 自動化（延後，不影響上線）
- [ ] 比照 NoCode_Project `deploy-python-backend.yml` 新增 `deploy-mnemosyne.yml`：偵測 `MCP_Service/**` 變更 → SSH 進 GCE → `git pull` → 安裝依賴 → `systemctl restart mnemosyne`

---

## Phase 3：記憶自動精煉

> ⚠️ **待確認事項**：AI 在對話中如何判斷「值得存」的時機，仍是未經實測驗證的啟發式規則，需在 2.5 整合測試階段實際觀察並調整 Tool Description 措辭。

- [ ] 設計 Agent prompt 範本，引導 AI 在對話中自動判斷「值得存」的時機並精煉成因果結構（`because`/`then`，≤500 字）
- [ ] 實作「會議記錄存檔」與「每日決策報告存檔」自動觸發流程

---

## Phase 4：記憶治理與跨專案優化

> ⚠️ **待確認事項**：定期固化（6.3）的觸發方式（排程自動 vs 手動觸發）與觸發門檻（時間 vs 數量）尚未定案，動工前需先決定。

- [ ] 決定定期固化的觸發機制與門檻條件，實作批次流程（掃描候選 → LLM 歸納摘要 → 原始記憶標記 `archived`）
- [ ] 依實際使用資料回頭調校衰減排序權重與 `λ`（6.1）
- [ ] 理財專案前端（Azure Static Web App）新增「記憶庫管理」Dashboard：檢視記憶列表與狀態、手動封存/硬刪除
- [ ] 評估擴展到其他個人專案（如生活筆記類 `domain="life"`）
