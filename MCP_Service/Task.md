# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節。
> 已完成的 Phase 只保留現況摘要與踩坑重點，詳細變更歷程見 git log；實作細節、程式碼層級的注意事項見 `CLAUDE.md`。

---

## 現況總覽（Phase 1 + 2 + 2.6 + 2.7 + 2.8 已完成部署與修復驗證；2.9 開發與 code review 已完成，待提交部署）

> 程式碼架構模式（Hexagonal Architecture、目錄結構、三條硬規則）已移至 `CLAUDE.md`「Architecture」章節，不在此重複。

- **GCP / Firestore**：獨立專案 `mnemosyne-cb868`（Firestore Standard 版、`asia-east1`）；向量複合索引 `domain (ASC) + status (ASC) + embedding (Vector, 1536維, COSINE)`。
- **AI 服務**：Embedding 與寫入閘門判定皆優先走個人 Google AI Studio 訂閱（`GEMINI_API_KEY`）。模型：`gemini-embedding-001`（Matryoshka 截斷至 1536 維）+ `gemini-3.6-flash`。
- **Firestore 存取憑證**：專屬服務帳戶 `mnemosyne-db-sa` 的金鑰檔案（`GOOGLE_APPLICATION_CREDENTIALS_JSON`）。
- **MCP Transport**：Streamable HTTP（單一 `/mcp` 端點），非原規劃的 SSE。
- **部署**：GCE `mnemosyne.service`（systemd，`:8001`）+ `fintarck-proxy`（Cloud Run Nginx，`/mnemosyne/` 路徑分流）+ GitHub Actions 自動部署。
- **連線網址**：`https://fintarck-proxy-1077248196503.asia-east1.run.app/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>`。
- **已註冊 domain**：`global`（系統保留）、`dev`、`finance`。

**歷史 Phase 摘要**：
* **Phase 1（基礎建設）與 Phase 2（因果模型改版）**：完成了 Schema 調整、Domain Registry、寫入閘門重構、部署與整合測試，並透過 Claude Desktop 實際連線驗證。
* **Phase 2.6（Token 消耗優化）**：移除動態 domain 描述快取注入解鎖 Prompt Cache，整併 `pin_memory`/`unpin_memory` 為單一工具，將 schema 英文化並大幅簡寫以削減 token，完成 response payload 瘦身並調整搜尋預設 limit 至 2。
* **Phase 2.7（使用回饋問題修復）**：完成了 5 項核心使用回饋問題修復，包含 tags 雙向聯集合併、SUPERSEDE 狀態繼承（is_pinned / access_count）、回傳最終生效記憶預覽、改用手動驗證長度與自訂錯誤（解決 client 端 byte 計算問題）、以及 type 欄位正規化以相容既有資料。
* **Phase 2.8（儲存機制優化與 Bug 修復）**：完成了 `UPDATE` 儲存機制優化（原地覆寫 + 歷史快照以留存審計軌跡，保持 ID 與時效/重要度穩定）、修復了 `SUPERSEDE` 重要度丟失 Bug，並在 `pin`/`forget` 寫入路徑上加入多代遞迴 ID 解析以防過期 ID 懸空。
* **Phase 2.9（Embedding 防呆機制 + 檢索驗收測試集）**：開發與 agy code review 已完成，待提交部署。新增啟動時的
  embedding 模型/維度一致性 fail-fast 檢查（靜態 + 動態兩層）、`search_memories` 內部無副作用開關
  （`record_access`）、以及可重複執行的檢索驗收 benchmark（`scripts/run_retrieval_benchmark.py`，7 筆真實案例）。
  過程中意外發現並記錄了 Gemini embedding API 的每分鐘配額在多人同時測試下會被打滿（持續性配額競爭，非程式碼
  bug）。實作細節與踩坑見 `CLAUDE.md`。

---

## Phase 2.8（已於 2026-08-31 完成並通過覆核驗證）：`UPDATE` 儲存機制調整 + SUPERSEDE 既有 bug 修復

**現況**：`UPDATE`（原地覆寫既有 Firestore 文件，舊 `title`/`premise`/`conclusion` 直接消失，`doc_id`/`created_at`/`importance_score`/`is_pinned`/`access_count` 都不變）與 `SUPERSEDE`（建立全新文件，`doc_id` 改變，舊文件標記 `status="superseded"`，內容保留可查）是兩條不同的儲存路徑。

**原始提案為何被否決（agy 覆議發現的三個盲區，皆已對照程式碼驗證屬實）**：
1. **Stale ID / 懸空參照**：`pin_memory`、`forget_memory` 都是直接對呼叫端傳入的 `memory_id` 操作，沒有查詢該 ID 是否已被 `mark_superseded()` 標記、也不會沿 `superseded_by` 鏈條往後找。若把高頻的 `UPDATE` 也改走「建新 ID、舊 ID 作廢」的模式，AI context 中快取的舊 `doc_id` 會頻繁失效，導致 pin/forget 靜默作用在已作廢的文件上、真正的 active 文件毫無反應。
2. **時效與重要度倒退**：`_build_memory` 建新文件時一律 `created_at=now()`，會讓「只是修個錯字」的 `UPDATE` 在 recency 衰減排序中被人為刷新排到最前面。
3. **回溯語意（fallback semantics）不同**：`_apply_update` 在 LLM 未給出合併欄位時回溯「舊記憶內容」，`_apply_supersede` 回溯「新請求內容」，兩者方向相反，統一儲存路徑時必須避免這層應用邏輯被誤合併。

**額外發現（不屬於原提案範圍，但已完成修復的既有 bug）**：
- **`SUPERSEDE` 的 `importance_score` 退化 bug**：`_apply_supersede` 的 `merged_request` 原先未帶入 `old_memory.importance_score`，導致無明確傳入時會退化為預設值 5。已完成修復，能正確繼承舊值。

**最終實現方案**：
1. **`UPDATE` 原地覆寫 + 歷史快照**：`UPDATE` 繼續原地覆寫 active 文件（所有屬性與 ID 均不變），但在覆寫前**新增一次寫入**，將舊內容存成 `status="superseded"`、`superseded_by` 指向 active 文件 ID 的歷史快照文件，完美留存審計軌跡，且不破壞 `SUPERSEDE` 的因果鏈與 ID 不可變性。
2. **修復 SUPERSEDE 的 `importance_score` 繼承缺失**：`_apply_supersede` 建立新 Memory 時正確繼承舊記憶的 `importance_score`。
3. **`pin_memory`/`forget_memory` 加入遞迴解析**：在操作前呼叫 `resolve_active_memory_id()`，利用 `while` 迴圈與 `visited` 集合循著 `superseded_by` 指針一路解析至真正 active 的文件 ID（解決多代 SUPERSEDE 產生的 stale ID 懸空 Bug）。
4. **防禦性 Pydantic 一致性修復**：修正 `_build_memory` 中對 `importance_score` 的 falsy Coercion 判斷（改用 `is not None`），以確保若傳入 0 分時不會被誤判重設為預設值 5。

**驗證方式**：使用驗證腳本測試了多代鏈 `A→B→C→D` 遞迴解析、快照內容保存與重要度繼承邏輯，已全數通過驗證。

---

## Phase 2.9（開發與 agy code review 已完成，待提交部署）：Embedding 防呆機制 + 檢索驗收測試集

**現況**：`search_memories` 的召回準確度過去完全無法量化驗證（只能人工試打），且 embedding 模型/維度若跟部署環境（`GEMINI_API_KEY` 是否設定）不一致，過去沒有任何檢查會提前示警，只會在 Firestore 層噴出難以定位的錯誤。背景與原始方案為何被否決（agy 覆核發現的三個盲區）詳見對話紀錄；參考來源是同事 Tina27《The RAG Blueprint》簡報。

**最終實現方案**：
1. **Embedding 模型/維度 fail-fast（靜態 + 動態兩層）**：`VertexEmbeddingProvider.__init__` 在退回 Vertex AI fallback 模型時檢查其原生維度是否符合 `config.EMBEDDING_DIMENSION`；`application/startup_checks.py::verify_stored_embedding_dimension()` 在伺服器啟動時（掛在 `MCPServer(lifespan=...)`）抽樣一筆既有記憶比對實際存放維度。兩者不符皆 `raise ConfigurationError`（新增於 `domain/exceptions.py`），不使用 `assert`、不放在 `config.py` 模組層級，避免波及不需要 AI Key 的離線腳本。
2. **`search_memories` 無副作用開關**：`SearchMemoriesRequest` 新增 `record_access: bool = True`，測試集執行時傳 `False` 略過 `access_count` 寫回，避免 benchmark 反覆執行自我墊高衰減公式分數；MCP 對外工具行為不變。
3. **檢索驗收 benchmark**（`scripts/run_retrieval_benchmark.py` + `scripts/retrieval_benchmark_cases.py`，7 筆真實案例）：`domain`/`query`/`expected_order`（doc_id 依預期排序）格式，`limit` 對齊 `config.SEARCH_MEMORIES_DEFAULT_LIMIT`；429 配額限制與其他例外皆獨立分類（`QUOTA_EXCEEDED`/`ERROR`），不會跟真正的排序/召回失敗（`FAIL`）混淆或讓整批測試中斷；案例間有節流。跑法：`python -m scripts.run_retrieval_benchmark`。實作細節、程式碼位置、以及 agy 覆核抓出的 5 項發現（`_DEFAULT_CASE_LIMIT` 與正式環境脫節、`sample_one()` 抽樣無過濾、`bool(api_key)` 空白字元繞過、harness 例外處理會讓整批中斷等，皆已修正）見 `CLAUDE.md`。

**已知限制（留待之後評估，非本次阻塞項）**：
- 7 筆真實案例目前全是單一 doc_id 的語意召回驗證，尚未涵蓋「時效/重要度衝突」的多元素排序驗證——`search_memories` 的 `MemoryView` 不回傳 `importance_score`/`created_at`，需要另外造一組已知數值的專用測試記憶才能建案例。
- `run_retrieval_benchmark.py` 本身尚未端對端執行過（本機無 `GEMINI_API_KEY`/`GOOGLE_APPLICATION_CREDENTIALS_JSON`），目前只驗證過案例資料本身正確（7/7 手動確認命中），以及用 fake port 驗證過腳本邏輯；腳本第一次真正執行留待有本機憑證或部署後。
- 過程中排查 `search_memories` 疑似故障時，確認 Gemini embedding API 的每分鐘配額在多人同時測試下會被打滿（持續性配額競爭，非程式碼 bug），詳見 `CLAUDE.md`「Gemini API」章節；順便也發現並由使用者修正了一筆 `finance` domain 記憶的資料品質問題（`conclusion` 欄位混入工具呼叫殘留文字）。

**驗證方式**：全數以 fake port（`MemoryRepository`/`EmbeddingProvider`/`DomainRepository`）跑過的一次性腳本驗證（維度相符/不符、`record_access=False` 生效、429/其他例外分類、節流生效、空白字元金鑰觸發 `ConfigurationError` 等），未提交進版控；7 筆真實案例的資料正確性另以連線中的 `search_memories` 工具手動驗證（7/7 通過）。

**本次不列入範圍（已與 agy 討論並暫緩）**：Cross-encoder 二次重排、`superseded_by` 因果鏈擴充為依賴圖（Graph DB）。兩者在 Mnemosyne「Agent-Centric」的架構特性下（記憶寫入時已是低雜訊的因果對、接收端是有推理能力的 LLM 而非人類）投入產出比不划算，個人記憶規模（數百~數千筆）用 LLM context 做 in-context 推理即可滿足「記憶關聯推理」的需求，不需要額外的重排模型或圖資料庫。加上重排會在 MCP 同步呼叫路徑上多引入一次 LLM 呼叫延遲，直接影響對話體感。

---
