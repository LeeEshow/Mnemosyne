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
