"""`scripts/run_retrieval_benchmark.py` 的測試案例資料（見該檔案開頭的格式說明）。

用 .py 而非 .json 存放：專案根目錄 `.gitignore` 對 `*.json` 有全域封鎖（防止密鑰檔案誤入版控，
只留 `package*.json` 例外），這份資料需要被版控保留、跨次調整權重時重複使用，用 .json 會靜默
被 git 忽略掉。

案例僅存 domain / 改寫過的查詢字串 / doc_id / 簡短情境描述，不存原始 premise/conclusion 全文——
避免把（尤其 finance domain）記憶的完整內容複製進版控的測試檔案。doc_id 取自 2026-09-01/02
以 `search_memories` 對正式環境 `dev`/`finance` domain 實測撈到的真實記憶（見 Task.md Phase 2.9.2）。

目前只涵蓋單一 doc_id 的語意召回驗證（`expected_order` 皆為單元素）；「時效與重要度衝突案例」
（`expected_order` 多元素、驗證相對排序）尚未涵蓋。注意：`run_retrieval_benchmark.py` 是直接呼叫
`SearchMemoriesUseCase.execute()`，拿到的是完整的 domain `Memory`（含 `importance_score`/
`created_at`），MCP 對外閹割成 `MemoryView` 只發生在 `interface/mcp_server.py` 序列化給 client
時，不是這支 harness 的限制。真正卡住的是本機沒有 `GEMINI_API_KEY`/`GOOGLE_APPLICATION_CREDENTIALS_JSON`，
沒辦法直接執行這支 harness／寫個小腳本連正式 Firestore 查出既有記憶的這兩個欄位；等有本機憑證或
部署後再挑選既有案例、或另外造一組已知數值的專用測試記憶，見 Task.md 的待辦記錄。
"""

CASES: list[dict] = [
    {
        "domain": "dev",
        "query": "GEMINI_API_KEY 沒設定時，embedding 輸出維度會跟 Firestore 向量索引不合嗎？",
        "expected_order": ["lOqdmt0ACmDcqjDdqzUv"],
        "description": "Gemini API Key 切換後的維度與模型退役問題 lesson 的語意召回",
    },
    {
        "domain": "dev",
        "query": "GCE 部署到 Firestore 存取一直失敗，是不是 access scopes 或 projectId 沒設對？",
        "expected_order": ["2rOonDIXaS27vqFSEKoX"],
        "description": "GCE 部署 Firestore 存取三重坑 lesson 的語意召回",
    },
    {
        "domain": "dev",
        "query": "fintarck-proxy 的 Nginx 要怎麼設定才能正確把 /mnemosyne/ 前綴轉發到後端？",
        "expected_order": ["xf0cc0JkZcbHnefkUOQr"],
        "description": "fintarck-proxy Nginx 路由設定 lesson 的語意召回",
    },
    {
        "domain": "dev",
        "query": "MCP 用 sse_app 透過反向代理連線時一直被拒絕，是不是 DNS rebinding 防護的問題？",
        "expected_order": ["lkFR4umDEQHm4PqxJA3w"],
        "description": "MCP Transport DNS Rebinding／sse_app 誤用 lesson 的語意召回",
    },
    {
        "domain": "finance",
        "query": "0056 跟 00918 這組高股息 ETF 的分批加碼門檻是怎麼訂的？",
        "expected_order": ["8kmbpKVXF75exf2izv9w"],
        "description": "高股息組兩階段加碼架構的語意召回",
    },
    {
        "domain": "finance",
        "query": "0050 跟 00891 這組大盤型 ETF 的分批加碼門檻是怎麼訂的？",
        "expected_order": ["sHW1jHlo1wPj0RtlRozg"],
        "description": "大盤型組兩階段加碼架構的語意召回",
    },
    {
        "domain": "finance",
        "query": "定期定額的執行紀律，什麼情況下要暫停或加速？",
        "expected_order": ["RbMFRhgB74FkcxpxsaV6"],
        "description": "DCA 執行紀律規則 v2 的語意召回",
    },
]
