# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節。
> 已完成的 Phase 只保留現況摘要與踩坑重點，詳細變更歷程見 git log；實作細節、程式碼層級的注意事項見 `CLAUDE.md`。

---

## 現況總覽（Phase 1 + 2 + 2.6 + 2.7 + 2.8 已完成部署與修復驗證）

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
