# Mnemosyne MCP 開發項目清單 (Task List)

> 依據 [Mnemosyne_MCP_Proposal.md](../Docs/Mnemosyne_MCP_Proposal.md) 的定案設計展開。每個任務後面的括號標註對應設計文件章節。
> 已完成的 Phase 只保留現況摘要與踩坑重點，詳細變更歷程見 git log；實作細節、程式碼層級的注意事項見 `CLAUDE.md`。

---

## 現況總覽（Phase 1 + 2 + 2.6 + 2.7 已完成部署與修復驗證）

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

---

## 架構提案（待決策，尚未核准）：`UPDATE` 統一改走 `SUPERSEDE` 的儲存路徑

> **狀態**：agy 提出、PM 端評估後認為值得採納的架構提案，**尚未跟你定案，不在 Phase 2.7 的實作範圍內**。列在這裡是為了不遺失這個討論脈絡，決定要不要做、什麼時候做之前不要動工。

**現況**：`UPDATE`（原地覆寫既有 Firestore 文件，舊 `title`/`premise`/`conclusion` 直接消失，`doc_id` 不變）與 `SUPERSEDE`（建立全新文件，舊文件標記 `status="superseded"`，內容保留可查）目前是兩條不同的儲存路徑。

**提案**：把 `UPDATE` 的儲存方式也改成跟 `SUPERSEDE` 一樣——一律建新文件、舊文件標記 superseded，不再有「原地覆寫、舊內容永久消失」的路徑。`decision` 回傳值（`UPDATE` vs `SUPERSEDE`）維持不變，只是持久化機制統一。

**理由**：
1. **資料安全**：`UPDATE` 目前不可逆——如果 Gemini 合併判斷失誤，舊內容沒有任何辦法救回來。改成建新文件後，所有內容變動都自動留下稽核軌跡，也符合 Proposal 2.5/6.4 章「不做硬刪除、用 `status` + `superseded_by` 保留稽核軌跡」的既有設計哲學（目前的 `UPDATE` 其實是這個原則底下唯一的例外）。
2. **會讓 Phase 2.7 的修復更簡單**：Phase 2.7 的「tags 合併」與「SUPERSEDE 繼承 pin/access_count」這兩項修復，如果 `UPDATE` 改走跟 `SUPERSEDE` 一樣的路徑，可以共用同一段程式碼，不用在 `_apply_update`、`_apply_supersede` 各寫一次。
3. **職責分離更乾淨**：「這是什麼類型的變動」（`decision` 語意，仍由 LLM 判斷 UPDATE/SUPERSEDE/CONFLICT_DETECTED）跟「怎麼持久化」（統一走新文件 + 標記 superseded）本來就是兩個不同層次的問題，沒有理由因為前者的判斷不同就一定要用不同的儲存機制。

**取捨與待確認**：
- `UPDATE` 完成後 `doc_id` 會變（現在不變）。如果 AI 之前記住了某個 `doc_id`（例如剛 `pin_memory` 過），這筆記憶被 `UPDATE` 後舊 `doc_id` 就作廢——不過這正是 Phase 2.7 已經要幫 `SUPERSEDE` 處理的「繼承 pin/access_count」邏輯，`UPDATE` 走同一條路等於自動一併解決，不算額外負擔。
- 長期下來文件數量成長速度會變快（同一個主題被反覆修正 10 次，會變成 10 份文件而非 1 份原地編輯的文件）。以目前個人使用規模，Firestore Spark 免費額度完全吃得下，不是問題。

**若要推進，下一步**：跟 PM 確認是否採納，採納的話請 agy 針對這個新的統一儲存路徑再覆議一次（尤其是 `doc_id` 變動對既有呼叫端行為的影響），再排進實作。

---
