# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節。
> 勾選代表完成，未完成項目維持未勾選。已完成的 Phase 只保留現況摘要與踩坑重點，詳細變更歷程見 git log；實作細節、程式碼層級的注意事項見 `CLAUDE.md`。

---

## 現況總覽（Phase 1 + 2 已完成部署並實測驗證）

> 程式碼架構模式（Hexagonal Architecture、目錄結構、三條硬規則）已移至 `CLAUDE.md`「Architecture」章節，不在此重複。

- **GCP / Firestore**：獨立專案 `mnemosyne-cb868`（Firestore Standard 版、`asia-east1`）；向量複合索引 `domain (ASC) + status (ASC) + embedding (Vector, 1536維, COSINE)`。
- **AI 服務**：Embedding 與寫入閘門判定皆優先走個人 Google AI Studio 訂閱（`GEMINI_API_KEY`），Vertex AI 僅作為未設定時的退路（因索引維度已改動，目前實際上不可用）。模型：`gemini-embedding-001`（Matryoshka 截斷至 1536 維）+ `gemini-3.6-flash`。
- **Firestore 存取憑證**：專屬服務帳戶 `mnemosyne-db-sa` 的金鑰檔案（`GOOGLE_APPLICATION_CREDENTIALS_JSON`），優先於 GCE 附加身分（後者與 Firestore 查詢 API 有相容性問題，見 `CLAUDE.md`）。
- **MCP Transport**：Streamable HTTP（單一 `/mcp` 端點），非原規劃的 SSE。
- **部署**：GCE `mnemosyne.service`（systemd，`:8001`）+ `fintarck-proxy`（Cloud Run Nginx，`/mnemosyne/` 路徑分流）+ GitHub Actions 自動部署（`.github/workflows/deploy-mnemosyne.yml`，push 到 `MCP_Service/**` 或手動觸發）。
- **連線網址**：`https://fintarck-proxy-1077248196503.asia-east1.run.app/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>`（所有 Client 共用同一條，`domain` 為工具參數而非連線層級，見 Proposal 2.3 v5）。
- **已註冊 domain**：`global`（系統保留）、`dev`、`finance`。

**Phase 1（基礎建設）與 Phase 2（因果模型改版：Schema 調整、Domain Registry、寫入閘門重構、Tool Description、部署與整合測試、CI/CD）已全部完成**，八個工具、寫入閘門五種決定值（`NOOP`/`UPDATE`/`SUPERSEDE`/`CONFLICT_DETECTED`/`ADD`）皆已透過 Claude Desktop 實際連線驗證。部署過程中排查出的環境/函式庫層級問題（`google-api-core` regression、GCE VM scope、Firestore 憑證相容性、MCP transport 選擇、Nginx 設定、Gemini 模型/區域可用性等）與寫入閘門 prompt 調整的完整脈絡，記錄於 `CLAUDE.md`「Deployment gotchas」與「Gemini API」章節，不在此重複。

---

## Phase 2.6：Token 消耗優化

> 設計依據與完整分析見 [`../Docs/Token_Optimization.md`](../Docs/Token_Optimization.md)（含三輪 Claude × agy 架構收斂過程）。
> 目標：schema 從 ~2,300 tokens/turn 降至 ~1,000 tokens/turn，10-turn session 總消耗減少約 86%。
> **核心架構決策**：雙保險設計（Belt and Suspenders）——Server `instructions` 承載完整全域規則，Tool description 保留 1 行精簡 guardrail，確保不支援 instructions 的 client（如部分 Gemini 整合）仍有最低安全保障。

### [P0] 移除動態 Domain 注入 → 解鎖 Prompt Cache

- [x] 刪除 `mcp_server.py` 中的 `_DomainDescriptionCache` class、`_domain_description_cache` 實例、`_render_domain_list()`、`_with_dynamic_domain_description()`、`_DOMAIN_PARAM_TOOL_NAMES`
- [x] `_MnemosyneMCPServer` subclass 整體移除，直接使用 `MCPServer("mnemosyne", instructions=_INSTRUCTIONS)`
- [x] `config.py`：`DOMAIN_LIST_CACHE_TTL_SECONDS` 保留作歷史紀錄

**驗收**：連續兩次 `list_tools` 呼叫回傳的 schema 位元完全一致，Prompt Cache 可正常命中。

### [P0] 配置 Server `instructions` + Schema 英文化 + 清除跨工具描述污染

> 三項合為一個任務，因為都在同一批檔案（`mcp_server.py`、`tool_schemas.py`）上進行，拆開執行會導致中間狀態語意不一致。

- [x] `mcp_server.py`：`_INSTRUCTIONS` 字串傳入 `MCPServer` constructor，涵蓋 CONFLICT / DOMAIN REGISTRATION / SEARCH / LOAD_PINNED 四條規則
- [x] `mcp_server.py` 各工具 description 全面改寫為精煉英文，`save_memory` 與 `register_domain` 保留 1 行 guardrail
- [x] `tool_schemas.py` 所有 `Field(description=...)` 改為精煉英文，清除跨工具描述污染（`SearchMemoriesDomain`、`LoadPinnedMemoriesDomain` 移除 `requires_registration` 流程說明；`SaveMemoryTags` 移除例句）

**驗收**：用英文 query 呼叫工具正常運作；用 Gemini client 連線時，收到 `conflict_detected` 後 AI 行為正確（停下詢問使用者）；schema 大小 ≤ 1,100 tokens。

### [P1] 整併 `pin_memory` / `unpin_memory`

- [x] `mcp_server.py`：`pin_memory` handler 接受 `pinned: bool = True` 參數，`True` 時 pin，`False` 時 unpin
- [x] 刪除 `unpin_memory` handler 及 `UnpinMemoryUseCase` 注入（`_Dependencies` 與 `_dependencies()`）
- [x] `tool_schemas.py`：新增 `PinMemoryPinned` 型別；`UnpinMemoryUseCase` class 保留在 `application/pin_memory_use_case.py`，`PinMemoryUseCase.execute()` 改為接受 `pinned: bool = True` kwarg

**驗收**：`pin_memory(doc_id="...", pinned=False)` 成功解除釘選；`list_tools` 回傳工具數量為 7（原 8 個）。

### [P1] Response Payload 瘦身

- [x] `tool_schemas.py`：`MemoryView` 移除 `importance_score: int` 欄位
- [x] `mcp_server.py`：`_to_memory_view()` 移除 `importance_score=memory.importance_score`
- [x] `mcp_server.py`：`save_memory` handler 改用 `model.model_dump(exclude_none=True)` 序列化，排除 `null` 欄位

**驗收**：`save_memory` 成功回傳的 JSON 只含 `decision` 與 `doc_id`，無 null 欄位；`MemoryView` 不含 `importance_score`。

### [P2] 搜尋預設 limit 調整

- [x] `config.py`：`SEARCH_MEMORIES_DEFAULT_LIMIT = 2`（從 3 改為 2）
- [x] `mcp_server.py`：`search_memories` handler 預設 `limit=2`；`tool_schemas.py`：`SearchMemoriesLimit` Field default 改為 2

**驗收**：`search_memories` 不帶 `limit` 參數時預設回傳 2 筆；仍可透過明確傳入 `limit=3` 覆蓋。

---

## Phase 3：記憶自動精煉

> ⚠️ **待確認事項**：AI 在對話中如何判斷「值得存」的時機，仍是未經實測驗證的啟發式規則，需實際觀察並調整 Tool Description 措辭。

- [ ] 設計 Agent prompt 範本，引導 AI 在對話中自動判斷「值得存」的時機並精煉成因果結構（`premise`/`conclusion`，≤500 字）
- [ ] 實作「會議記錄存檔」與「每日決策報告存檔」自動觸發流程

---

## Phase 4：記憶治理與跨專案優化

> ⚠️ **待確認事項**：定期固化（6.3）的觸發方式（排程自動 vs 手動觸發）與觸發門檻（時間 vs 數量）尚未定案，動工前需先決定。

- [ ] 決定定期固化的觸發機制與門檻條件，實作批次流程（掃描候選 → LLM 歸納摘要 → 原始記憶標記 `archived`）
- [ ] 依實際使用資料回頭調校衰減排序權重與 `λ`（6.1）
- [ ] 理財專案前端（Azure Static Web App）新增「記憶庫管理」Dashboard：檢視記憶列表與狀態、手動封存/硬刪除
- [ ] 評估擴展到其他個人專案（如生活筆記類 `domain="life"`）
