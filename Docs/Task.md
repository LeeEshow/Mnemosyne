# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](./Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節，實作時應回頭核對該章節的詳細規格。
> 勾選代表完成，未完成項目維持未勾選。

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
- [ ] 初始化 Python 3.14 專案，安裝 `fastapi`, `firebase-admin`, `mcp` 等套件
- [ ] 串接 Google Embedding API（`text-multilingual-embedding-002`）（3.2）
- [ ] 串接 Gemini Flash API（供寫入閘門判定使用）（3.2、5.1）
- [ ] 建立 `config.py`，集中管理以下起始參數（3.2、5.1、6.1）：
  - [ ] `LOW_THRESHOLD = 0.85`（寫入閘門相似度低閾值）
  - [ ] 衰減排序權重 `w1=0.50 / w2=0.25 / w3=0.15 / w4=0.10`
  - [ ] 衰減常數 `λ ≈ 0.0077`（半衰期 90 天，以天為單位）
  - [ ] 向量查詢 K 值：domain 軌道 K=40、global 軌道 K=10（5.2）
  - [ ] 常駐清單預設上限 `limit=5`（5.6）

---

## Phase 2：MCP Server 開發與部署

### 2.1 核心工具實作（5 章）
- [ ] `save_memory`：三段式同步寫入閘門（字串完全相同 → NOOP；相似度 <0.85 → ADD；0.85~1.0 → 呼叫 Gemini Flash 判定 NOOP/UPDATE/SUPERSEDE/ADD）（5.1）
  - [ ] `UPDATE` 分支需重新計算並覆寫 `embedding`
  - [ ] `SUPERSEDE` 分支需寫入 `superseded_by`
- [ ] `search_memories`：雙軌並行檢索 + 衰減排序（5.2、6.1）
  - [ ] 向量軌道：`asyncio.gather` 並行查詢 `domain` 與 `global`
  - [ ] 精確匹配軌道：`tags array-contains-any exact_tags`
  - [ ] 合併去重 → 套用衰減排序公式 → 回傳 Top-K
  - [ ] 支援 `include_superseded` / `include_archived` 深度搜尋開關
  - [ ] 命中記憶更新 `last_accessed_at` / `access_count`
- [ ] `forget_memory`：預設軟刪除（`status="archived"`），`hard_delete=true` 才真刪（5.3）
- [ ] `pin_memory` / `unpin_memory`：更新 `is_pinned`（5.5）
- [ ] `load_pinned_memories`：查詢 `is_pinned==true` 常駐清單（5.6）

### 2.2 連線層級 domain 綁定
- [ ] 實作從 MCP 連線 URL 的 query string 解析 `domain`（3.3）
- [ ] 確保單一連線建立後，該連線後續所有 tool 呼叫自動套用綁定的 `domain`，不需 AI 每次傳入

### 2.3 部署上線
- [ ] GCE 新增 `mnemosyne.service`（systemd），監聽 `:8001`（3.3.1）
- [ ] GCE 新增防火牆規則開放 `tcp:8001`（3.3.1）
- [ ] 修改 `fintarck-proxy` 的 `nginx.conf`，新增 `/mnemosyne/` location block 轉發至 `:8001`（3.3.1）
- [ ] 透過 Cloud Run 重新部署 `fintarck-proxy`
- [ ] 設定 `MNEMOSYNE_MCP_KEY` 存取金鑰（比照既有 `MCP_ACCESS_KEY` 模式）

### 2.4 整合測試
- [ ] 以 `.../mnemosyne/mcp?key=<KEY>&domain=coding` 連線 Cursor，測試 `save_memory`/`search_memories`
- [ ] 以 `.../mnemosyne/mcp?key=<KEY>&domain=finance` 連線 Claude Desktop 或理財網頁，驗證跨 domain 檢索互相隔離
- [ ] 驗證 `global` domain 的記憶能同時被 `coding` 與 `finance` 連線檢索到
- [ ] 驗證寫入閘門三段判定邏輯（完全重複、明顯不同、模糊地帶各測一組案例）

---

## Phase 3：記憶自動精煉與經驗學習

> ⚠️ **待確認事項**：`reflect_on_task` 與「對話中自動判斷該不該存」的具體觸發規則目前仍是「由 AI 自行判斷」，尚未定義成可執行的明確條件。動工前建議先補一輪討論，定義具體觸發時機（例如：每完成一個明確任務就呼叫、或由使用者訊息中的特定信號觸發）。

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
