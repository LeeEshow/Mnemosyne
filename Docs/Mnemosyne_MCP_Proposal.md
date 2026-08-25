# Mnemosyne MCP: 跨領域 AI 通用長期記憶體方案構想書
> **Mnemosyne**（音譯：姆涅莫辛涅）—— 希臘神話中的記憶女神，九位繆斯女神的母親。象徵為 AI 賦予跨越時空與領域的記憶能力。

---

## 1. 專案願景 (Vision)
在現行的 AI 應用中，AI 助理在對話結束或視窗關閉後即失去上下文脈絡，形成「短期記憶喪失」。本專案旨在建立一個**輕量、通用且跨領域的長期記憶層 (Long-Term Memory Layer)**。

透過 **Model Context Protocol (MCP)** 標準協定，將 Google Cloud Firestore Vector Search（向量搜尋）封裝成 AI 的記憶體插件。不論是用於理財分析、寫程式、生活筆記、抑或是各類日常專案，AI 都能自主調用此 MCP 工具來「寫入記憶」與「檢索歷史經驗」，成為使用者的專屬「第二大腦」。

---

## 2. 核心設計哲學 (Core Philosophy)

### 2.1 高訊雜比 (High Signal-to-Noise Ratio)
傳統 RAG (檢索增強生成) 常直接對話歷史（Chat History）進行切片與向量化，這會導入大量如「哈囉」、「謝謝」、「請幫我」等口語雜訊。
* **做法**：限制記憶文本長度在 **500 字以內**。
* **原則**：不儲存原始對話，只儲存經過 AI 精煉、彙整後的「會議決策」、「事實記錄」或「代碼知識點」。

### 2.2 混合查詢 (Hybrid Query)
純向量搜尋容易在特定條件（如「找上週的報告」、「只找關於 Python 的筆記」）下失焦。
* **做法**：結合 NoSQL 的關聯式欄位（如 `type` 類別、`tags` 標籤、`created_at` 時間戳記）與向量欄位，進行「前置過濾混合搜尋」（Filtered Vector Search）。

### 2.3 跨領域隔離與全域共享 (Domain Namespace)
不綁定特定專案，但為了避免跨領域（如 coding 助理與理財助理）的語意污染，底層資料結構設計有 `domain` 欄位進行剛性分區（見第 4 章）。向量空間是連續的，語意相近但領域無關的記憶（例如「策略」「風險」「優化」在理財與寫程式脈絡都會出現）可能互相污染 Top-K 結果，因此不能只靠 `tags` 這類選填標籤，需要一個必填、由 Client 端固定帶入的分區欄位。

同時，為了解決跨領域通用的個人偏好設定（例如「回覆一律繁體中文」、「不使用表情符號」），特別保留一個 `"global"` 特殊領域。檢索時，系統會自動包含當前 `domain` 與 `"global"` 下的記憶（見 5.2）。

> **範圍界定**：本專案為**個人專屬**記憶體，不考慮多使用者隔離，因此不需要 `user_id` / `app_id`，亦不涉及多租戶層級的權限控制。`domain` 分區與多租戶隔離目的不同——多租戶隔離是為了資料所有權/存取控制，`domain` 純粹是為了避免同一個人的不同助理情境互相干擾檢索結果，兩者不可混為一談。

### 2.4 不存清單 (Exclusion List)
高訊雜比不是只靠「怎麼精煉摘要」達成，更要明確定義「什麼永遠不該存」，否則資料庫會隨時間被大量可從其他管道推導出的資訊淹沒，訊噪比必然隨規模下降。以下內容**禁止**寫入 `memories`：

* **可由其他系統反查的資訊**：程式碼結構、架構慣例、檔案路徑 —— 這些用 `git log` / 讀原始碼就能得到，存了只會製造過期風險。
* **除錯過程本身**：只有「為什麼會發生」「為什麼選這個修法」值得留，修法程式碼本身已經在版控歷史裡。
* **已存在其他文件的內容**：例如專案的 CLAUDE.md、README 已記載的規範，不重複存入向量庫。
* **純對話當下的暫時狀態**：進行中任務的分解步驟、暫存變數 —— 這是任務管理該處理的範疇，不是長期記憶。
* **原始逐字對話紀錄**：延續 2.1 的原則，只存 AI 精煉後的結論，不存 raw chat log 切片。
* **重複或高度相似的既有記憶**：由 5.1 的「寫入閘門」機制在寫入前自動攔截。

---

## 3. 系統架構 (Architecture)

### 3.1 運作流程圖 (Data Flow)

```mermaid
graph TD
    User[使用者] <-->|對話| Agent[AI 助理 / Agent]
    Agent <-->|MCP Protocol| MCP[Mnemosyne MCP Server]
    MCP <-->|REST / SDK| Embed[Embedding API<br/>Gemini / OpenAI]
    MCP <-->|NoSQL & Vector Query| DB[(Cloud Firestore)]
    
    subgraph 記憶寫入 (Ingestion)
        A1[對話結束/觸發存檔] --> A2[AI 彙整為 500 字摘要]
        A2 --> A3[呼叫 Embedding API 轉為向量]
        A3 --> A4[存入 Firestore memories 集合]
    end
    
    subgraph 記憶檢索 (Retrieval)
        B1[使用者提問] --> B2[AI 判斷需要歷史記憶]
        B2 --> B3[將提問向量化]
        B3 --> B4[Firestore 進行混合搜尋]
        B4 --> B5[回傳 Top-K 記憶 Context 給 AI]
    end
```

### 3.2 技術棧建議 (Tech Stack)
* **後端框架**：Python 3.14 + FastAPI
* **協定標準**：Model Context Protocol (MCP) SDK，傳輸方式採 **SSE over HTTP**（沿用既有專案 NoCode_Project 已驗證可行的部署模式，見 3.3），不使用 Stdio。
* **資料庫**：Firebase/Google Cloud Firestore (Spark 免費方案，含 50k 每日免費讀取、20k 寫入、1 GiB 空間)
* **向量索引**：Firestore Native Vector Search (支援 HNSW 演算法、餘弦相似度)
* **Embedding 模型**：**Google `text-multilingual-embedding-002` (768維)**（定案）。理由：與 Firestore/GCE 同屬 GCP 生態系，可共用同一組服務帳號憑證；768 維比 OpenAI `text-embedding-3-small` 的 1536 維更省儲存與運算成本；對繁體中文的語意理解足夠且經過 multilingual 優化。
* **寫入閘門判定 LLM**：**Gemini Flash 系列**（定案）。理由：NOOP/UPDATE/SUPERSEDE/ADD 四選一分類任務不需要強推理能力，Gemini Flash 速度快、成本低，且同屬 GCP 生態系可共用憑證，不需額外引入 Anthropic/OpenAI API Key 造成跨雲依賴。若日後實測判斷品質不穩定，可再替換，抽換成本低。
* **設定管理**：寫入閘門的相似度分段門檻（見 5.1）與衰減排序參數（見 6.1）屬於**模型相依參數**（不同 Embedding 模型的餘弦相似度分佈不同），統一集中於 `config.py`，不寫死在程式邏輯中。起始值見 6.1 與 5.1。

### 3.3 部署架構 (Deployment)
參考既有專案 `NoCode_Project`（`Docs/Cloud-Deployment.md`）已在生產環境驗證過的部署模式，Mnemosyne 直接沿用相同架構，降低重新摸索的成本：

```
MCP Client (Cursor / Claude Desktop / 理財網頁)
  └─ https://<proxy-host>/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>&domain=<coding|finance|life>
       └─ Cloud Run Proxy（Nginx reverse proxy，SSE 長連線 proxy_read_timeout）
            └─ GCE e2-micro（與 NoCode_Project 的 fintarck-backend **共用同一台主機**，另開一個 port 跑獨立的 systemd service，例如 mnemosyne.service）
                 └─ Firestore（**獨立的 GCP 專案**，不與 nocode-finance 共用配額）
```

**關鍵決策與理由**：
* **與 NoCode_Project 共用 GCE 主機（而非獨立主機）**：個人使用、流量低，e2-micro 資源用不滿，多開一個 systemd service 不需要額外月費；若日後流量成長到互相搶資源，再拆分成獨立主機即可，遷移成本不高。
* **Firestore 使用獨立 GCP 專案**：Mnemosyne 設計上是給多個不同專案共用的記憶體後端（見 2.3），若寄生在 `nocode-finance` 專案底下，每日讀寫額度（50k/20k）會被該專案本身的用量排擠，且概念上也不乾淨。獨立開一個 GCP 專案在 Firestore Spark 方案下仍是免費，沒有理由共用。
* **`domain` 透過連線 URL 的 query string 帶入**：比照 NoCode_Project 用 `?key=<MCP_ACCESS_KEY>` 做身分驗證的模式，`domain` 也在 MCP 連線建立時透過 URL 固定帶入（例如 Cursor 固定連到 `...?domain=coding`），Server 端從連線層級讀出 `domain`，該連線之後的所有 tool 呼叫自動套用，不需要每次呼叫都由 AI 額外指定，避免 AI 忘記帶或臨時改動造成跨領域污染。
* **`fintarck-proxy` 服務名稱維持不變**：Cloud Run 服務名稱建立後無法修改（GCP 限制），改名須整台搬遷並更新前端呼叫網址，成本不划算。現階段只是命名跟用途不完全一致（純美觀問題），故決定沿用現有名稱，不特別搬遷。
* **Firestore 存取改用 GCE 附加身分，不用金鑰檔案**：原規劃比照 NoCode_Project 下載服務帳戶 JSON 金鑰（`GOOGLE_APPLICATION_CREDENTIALS_JSON`），但 GCP 組織已啟用 `iam.disableServiceAccountKeyCreation` 政策禁止建立金鑰。改為將 GCE 主機的 Compute Engine 預設服務帳戶（`1077248196503-compute@developer.gserviceaccount.com`）於 `mnemosyne-cb868` 專案 IAM 授予 **Cloud Datastore User** 角色，Firebase Admin SDK 在 GCE 環境下會自動透過 Metadata Server 取得憑證，完全不需要金鑰檔案，安全性優於原規劃（避免金鑰外洩風險），程式端也不需要讀取/設定憑證檔案路徑。

### 3.3.1 Nginx 路徑分流設定

`fintarck-proxy`（Cloud Run 服務）目前的 `nginx.conf` 只有單一 `location /`，全部流量轉發到 GCE 的 `:8000`（既有理財後端）。新增 Mnemosyne 後，改為依路徑分流到不同 port：

```nginx
server {
    listen 8080;
    server_name _;

    location /mnemosyne/ {
        proxy_pass http://35.201.176.69:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }

    location / {
        proxy_pass http://35.201.176.69:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

**對應的 GCE 端調整**：
* 新增 `mnemosyne.service`（systemd），監聽 `:8001`，跑法比照既有 `fastapi.service`（`uvicorn main:app --port 8001`）。
* 新增防火牆規則開放 `tcp:8001`（比照既有的 `allow-uvicorn-8000`）。
* 修改上述 `nginx.conf` 後，透過 Cloud Run「編輯來源」重新部署 `fintarck-proxy`（或 `gcloud run deploy fintarck-proxy --source .`）。

最終 MCP 連線網址：`https://fintarck-proxy-xxx.asia-east1.run.app/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>&domain=<coding|finance|life>`。

---

## 4. 資料庫結構設計 (Database Schema)

在 Firestore 中建立名為 `memories` 的集合 (Collection)，每筆 Document 的欄位定義如下：

| 欄位名稱 (Field) | 資料型別 (Type) | 必填 | 說明 |
| :--- | :--- | :---: | :--- |
| `type` | `String` | 🟢 | **記憶類別**。例如：`"Notes"`(筆記)、`"DailyReport"`(決策報告)、`"Preference"`(個人喜好)、`"Code"`(代碼知識)、`"Reflection"`(任務反思)。 |
| `domain` | `String` | 🟢 | **領域分區**。由 MCP Client 連線設定固定帶入（如 `"coding"` / `"finance"` / `"life"`），避免跨領域語意污染。特殊值 `"global"` 保留給跨領域都適用的通用偏好（如語言/格式偏好），查詢時永遠與指定 domain 一併檢索（見 5.2）。 |
| `title` | `String` | 🟢 | **記憶主題簡稱**。例如：`"FastAPI 解決 CORS 跨網域設定"`。可用於 UI 列表顯示。 |
| `context` | `String` | 🟢 | **精煉內容（限制 500 字內）**。提供給 AI 的實際上下文。建議內部再拆出 `why`（成因/脈絡）與 `how_to_apply`（適用情境）兩個子片段，而非單純自由文本，方便檢索後判斷是否該套用。 |
| `embedding` | `VectorValue` | 🟢 | **向量值**。儲存 `context`（或 `title + context`）的向量嵌入值。若記憶內容經 `UPDATE` 合併而變動，**必須連同重新計算並覆寫此欄位**，否則向量會與文字內容脫鉤。 |
| `created_at` | `Timestamp` | 🟢 | **建立時間**。可用於時間維度的排序或限制（如「只搜尋近三個月的記憶」）。 |
| `tags` | `Array (String)`| ⚪ | **標籤數組**。例如：`["python", "fastapi"]`、`["0056"]`。除了輔助分類，也是 5.2 精確匹配軌道（`array-contains-any`）的查詢依據，寫入時應把精確技術字串（錯誤代碼、函式名、股票代號）也存入 `tags`，彌補向量檢索對精確字串比對能力較弱的問題。 |
| `source_id` | `String` | ⚪ | **溯源 ID**。若來自某個特定對話對話或事件快照，可記錄其 ID，便於 UI 點擊追溯。 |
| `importance_score` | `Integer (1-10)` | 🟢 | **重要性評分**。寫入當下由 AI 自評，用於檢索排序加權，避免純靠向量相似度導致重要記憶被稀釋。 |
| `is_pinned` | `Boolean` | 🟢 | **常駐標記**。由 AI 或使用者透過 `pin_memory`（5.5）明確標記，而非僅依賴 `importance_score` 自動判斷，避免高分記憶隨時間累積過多、造成常駐清單膨脹。預設 `false`。 |
| `status` | `String` | 🟢 | **記憶狀態**：`"active"`(生效中) / `"superseded"`(已被取代) / `"archived"`(已固化封存)。取代直接刪除，保留稽核軌跡。預設 `"active"`。 |
| `superseded_by` | `String` | ⚪ | **取代者 ID**。若此記憶已被更新的記憶推翻，記錄新記憶的 `doc_id`，用於追溯記憶演變過程。 |
| `last_accessed_at` | `Timestamp` | ⚪ | **最近一次被檢索到的時間**。每次 `search_memories` 命中時更新，供衰減排序與固化判斷使用。 |
| `access_count` | `Integer` | ⚪ | **被檢索命中次數**。長期低命中的記憶為「記憶固化」的候選對象。 |

### 4.1 索引策略 (Indexing Strategy)
Firestore 向量搜尋（`find_neighbors`）與一般 `where` 條件合併查詢時，強制要求為該組合建立複合索引，且對 `IN` 等運算子的支援度有限、容易在動態查詢組合下出錯。為避免索引數量隨查詢條件組合爆炸，採取以下限縮策略：

* **DB 端只固定一組複合索引**：`domain (ASC) + status (ASC) + embedding (Vector)`。所有向量查詢一律走這個索引，不隨 `type`/`tags` 動態變化。
* **`type` 過濾**：不進 DB 查詢，改為向量查詢取回 Top-K（如 K=40）後，於應用層（Python）過濾。
* **`tags` 過濾**：分兩種用途處理——輔助篩選一樣用應用層過濾；但作為「精確匹配軌道」時（見 5.2），改用單欄位 `array-contains-any` 查詢，這是 Firestore 原生支援、不需複合索引的查詢型態。
* **`domain` 的 `global` 特例不使用 `IN` 運算子**：改為對 `domain == 指定值` 與 `domain == "global"` 各發送一次獨立的向量查詢（`asyncio.gather` 並行），在應用層依 Document ID 去重合併，完全避開向量搜尋結合 `IN` 的相容性風險，且仍只需要上述單一複合索引。

---

## 5. MCP Tool 介面定義 (API Specification)

Mnemosyne MCP Server 將對外暴露七個核心工具 (Tools) 供 AI Agent 調用。

> **`domain` 不是工具參數，而是連線層級的上下文**：依 3.3 節部署設計，`domain` 在 MCP 連線建立時由 URL query string 固定帶入（如 `...?domain=coding`），Server 端在連線建立時就綁定該值，之後這條連線呼叫的所有工具都自動套用，不需要、也不應該由 AI 在每次呼叫時額外傳入——這樣可以避免 AI 忘記帶或臨時改動造成跨領域污染（見 2.3）。以下各工具的參數列表因此不再列出 `domain`。

### 5.0 Tool Description 撰寫原則

MCP Tool 的 `description` 字串會被直接注入 AI 的 context，直接影響 AI「該不該呼叫」「呼叫哪個」的判斷品質，因此撰寫時遵循以下設計原則（各工具收斂後的實際文字定案於 `MCP_Service/Task.md` 2.1 節，此處只記錄原則本身，避免規格文字分散兩處造成不同步）：

1. **開頭寫觸發情境，不是功能敘述**：LLM 決定是否呼叫工具主要看「情境符不符合」，不是「工具做什麼」，第一句話應是「當...時使用」。
2. **不要求 AI 傳入 `domain`，但要讓 AI 知道自己的範圍有邊界**：描述中不能出現讓 AI 自行生成 `domain` 值傳入的暗示；但反過來要讓 AI 知道檢索/寫入範圍已被連線層級鎖定，避免「查無結果」被誤判成「使用者從未提過」而產生錯誤斷言，而不是誠實地說明自己看不到那個範圍。
3. **容易混淆的工具要互相排除對方負責的情境**：例如 `save_memory`（討論中即時記錄）與 `reflect_on_task`（任務結束後的回顧）用途相近，兩邊描述都需明講「若是另一種情境，請改用另一個工具」。
4. **明確揭露 AI 不用自己做的前置檢查，也要反過來明講必要的前置查詢**：`save_memory` 內建寫入閘門會自動去重合併（5.1），不需要 AI 自己先呼叫 `search_memories` 確認重複；但 `forget_memory` / `pin_memory` 需要 `doc_id`，必須先呼叫 `search_memories` 取得，兩種情況都要在描述中寫清楚，不能只強調前者造成誤導。
5. **內部演算法細節不寫進會被注入 context 的 description**：寫入閘門三段式判定邏輯（5.1）、衰減排序公式（6.1）等屬於 Server 內部行為，AI 不需要知道細節就能正確使用工具，寫太細只會浪費注入 context 的 token，並可能誤導 AI 以為自己該介入控制。
6. **參數層級的硬限制與判斷依據要寫在該參數自己的 description，並附具體範例**：例如 `search_memories.exact_tags` 與 `save_memory.tags` 都該明確舉例「錯誤代碼、函式名、股票代號」等精確技術字串該填入的情境，而不是籠統帶過；`context` 的 500 字限制同理寫在參數說明而非工具總說明。

### 5.1 `save_memory`
> **功能**：由 AI 助理主動呼叫，將討論精華轉化為向量並存入資料庫。**寫入前會先經過「寫入閘門」，避免重複與衝突資訊持續累積。閘門為同步執行**（非背景任務）——考量個人規模的寫入頻率不高，同步呼叫 LLM 判定的延遲可接受，優先維持實作簡單，不引入額外的任務佇列基礎設施。

* **輸入參數**：
  * `title` (string, required): 記憶主題。
  * `context` (string, required): 限制 500 字內的摘要內容，建議內部區分 `why` 與 `how_to_apply`。
  * `type` (string, required): 記憶類別（如 `Notes`, `DailyReport`, `Preference`）。
  * `tags` (array of strings, optional): 關聯標籤，含精確技術字串（見 4 章 `tags` 說明）。
  * `source_id` (string, optional): 原始資料/對話關聯 ID。
  * `importance_score` (integer 1-10, optional): AI 自評重要性，未提供則預設中間值。
* **後台邏輯（寫入閘門 Write-time Gate，三段式同步判定）**：
  1. 將 `context` 向量化，在同一 `domain` 內查詢既有記憶的最近鄰相似度。
  2. 依相似度分三段處理（門檻值定義於 `config.py`，起始值基於 `text-multilingual-embedding-002`，可日後依實測調整）：
     * **字串完全相同（正規化後）**：直接 **NOOP**，不呼叫 LLM，僅更新既有記憶的 `last_accessed_at`。
     * **相似度低於低閾值（`LOW_THRESHOLD = 0.85`）**：直接 **ADD**，視為全新記憶，不呼叫 LLM。低於此值代表用詞與結構已有明顯差異，混淆風險低，不需要耗費 LLM 呼叫。
     * **相似度介於 0.85 與 1.0 之間（含高相似度區間）**：一律呼叫 **Gemini Flash** 判定，由其選擇：
       * **NOOP**：語意重複，不寫入。
       * **UPDATE**：屬於既有記憶的補充延伸（例如新增一項並存事實），合併內容並**重新計算 `embedding`**，不可只改文字不更新向量。
       * **SUPERSEDE**：新資訊推翻舊記憶，將舊記憶 `status` 設為 `"superseded"` 並填入 `superseded_by`，再寫入新記憶。
       * **ADD**：LLM 判斷雖相似度高但實為獨立事件，視為全新記憶寫入。

  > 高相似度不代表「語意重複」，可能只是「舊記憶的超集合」（例如新句子在舊句子基礎上多了一項資訊）——這種情況必須交由 LLM 判斷合併方式，不能單純以相似度分數直接 NOOP，否則會遺失新增資訊。

### 5.2 `search_memories`
> **功能**：當使用者詢問過去的事情或需要歷史脈絡時，AI 助理調用此工具檢索相似記憶。採用「雙軌並行檢索 + 應用層合併 + 衰減排序」的混合檢索器，彌補純向量搜尋在精確字串比對與 Top-K 截斷上的弱點。

* **輸入參數**：
  * `query` (string, required): 查詢字句（例如：「上次討論 0056 的停利點設定是什麼？」）。
  * `type` (string, optional): 指定記憶類別進行過濾（應用層過濾，見 4.1）。
  * `exact_tags` (array of strings, optional): 需要精確比對的關鍵字（如錯誤代碼、股票代號），觸發精確匹配軌道。
  * `limit` (integer, optional, default=3): 返回的最大記憶數量。
  * `include_superseded` / `include_archived` (boolean, optional, default=false): 是否納入非 `active` 狀態的記憶（供「深度搜尋」挖掘已封存內容）。
* **後台邏輯**：
  1. **向量軌道（並行）**：將 `query` 向量化，透過 `asyncio.gather` 同時對 `domain == 指定值` 與 `domain == "global"` 各發送一次向量查詢（見 4.1 索引策略），K 值不對稱設定（如指定 domain K=40、global K=10），避免不必要的讀取與運算量。
  2. **精確匹配軌道（並行）**：若提供 `exact_tags`，並行對 `tags array-contains-any exact_tags` 發送查詢，不受向量相似度限制。
  3. **合併與排序**：三路結果依 Document ID 去重合併後，統一套用 **衰減排序公式**（詳見 6.1）重新排序，而非單純以向量相似度排序。預設排除 `status != "active"`，除非 `include_superseded`/`include_archived` 為 `true`。
  4. 回傳 Top-K 的 `context` 列表，同步更新命中記憶的 `last_accessed_at` 與 `access_count`。

### 5.3 `forget_memory`
> **功能**：將記憶下架。預設為「軟刪除」（標記封存），僅在明確指定時才真正硬刪除。

* **輸入參數**：
  * `doc_id` (string, required): Firestore 文件 ID。
  * `hard_delete` (boolean, optional, default=false): 為 `true` 時才真正刪除文件；預設僅將 `status` 設為 `"archived"`，保留稽核軌跡。

### 5.4 `reflect_on_task`
> **功能**：任務／討論告一段落時，由 AI 主動呼叫，針對剛完成的工作做自我反思，產出「這次做對/做錯在哪」的經驗記錄，對應第 6 章的經驗學習機制。屬於 `save_memory` 的特化用法（固定 `type="Reflection"`），差異在於觸發時機是任務結束後的自我檢視，而非討論中即時判斷。

* **輸入參數**：
  * `task_summary` (string, required): 任務內容簡述。
  * `outcome` (string, required): `"success"` / `"failure"` / `"partial"`。
  * `lesson` (string, required): 精煉後的經驗教訓（限制 500 字內），沿用 `why` / `how_to_apply` 結構。
  * `tags` (array of strings, optional)。
* **後台邏輯**：同 `save_memory` 的寫入閘門流程，`type` 固定為 `"Reflection"`。

### 5.5 `pin_memory` / `unpin_memory`
> **功能**：明確標記／取消標記某筆記憶為「常駐記憶」，解決衰減排序（6.1）中極端重要但可能被向量 Top-K 截斷、或因權重不足而排不進結果的問題。採**明確標記制**而非分數自動判斷，避免常駐清單隨 `importance_score` 高分記憶累積而無限膨脹。

* **輸入參數**：
  * `doc_id` (string, required): Firestore 文件 ID。
  * （`unpin_memory` 同參數，將 `is_pinned` 設回 `false`）
* **後台邏輯**：更新該筆記憶的 `is_pinned` 欄位。

### 5.6 `load_pinned_memories`
> **功能**：供 MCP Client 在對話開始時呼叫，取得少量常駐記憶直接帶入 context，不經過向量檢索，避免第 5.2 節混合檢索仍可能漏掉「極端重要」記憶的風險。

* **輸入參數**：
  * `limit` (integer, optional, default=5): 上限筆數，避免常駐清單膨脹造成 context 污染與 Token 成本增加。
* **後台邏輯**：並行查詢 `domain == 指定值` 與 `domain == "global"` 且 `is_pinned == true` 且 `status == "active"` 的記憶，在 Python 端合併後，依 `importance_score` 與 `created_at` 降序排序，最後取前 `limit` 筆回傳。

---

## 6. 記憶治理機制 (Memory Governance)

隨著記憶量隨時間增加，訊噪比會自然下降——即使每筆寫入當下都經過精煉，大量陳舊、低價值記憶累積後仍會稀釋檢索品質。本章定義三層治理機制，對應第 5 章工具的後台邏輯。

### 6.1 衰減排序 (Decay Scoring)
由於 Firestore 向量搜尋（`find_neighbors`）無法在資料庫端執行自訂的數學加權公式，本系統將此機制設計為**「DB 檢索候選集 -> Python 重新加權排序」**兩階段流程。
在 `search_memories` 中，系統會先取得 5.2 節三路查詢（`domain` 向量軌道、`global` 向量軌道、`tags` 精確匹配軌道）合併去重後的候選集，接著在 Python 應用層依據下列公式重新計算分數：

```
score = w1 · relevance(向量相似度)
      + w2 · importance_score(自評重要性，正規化至 0~1)
      + w3 · recency(時間衰減分數)
      + w4 · access_frequency(存取頻率分數)
```

其中：
* **時間衰減分數 (recency)**：採用指數衰減公式 \(recency = e^{-\lambda \cdot \Delta t}\)，其中 \(\Delta t\) 為 `created_at` 到當前時間的時間差（以天為單位），\(\lambda\) 為衰減常數。
* **存取頻率分數 (access_frequency)**：以 `access_count` 進行對數縮放（Log-scaling）與正規化，避免高存取次數的記憶無限制主導排序。

**權重與衰減常數起始值（定案，集中設定於 `config.py`，日後依實測調整）**：

| 參數 | 起始值 | 理由 |
| :--- | :---: | :--- |
| `w1`（relevance） | 0.50 | 搜尋本質仍以相關性為主，其餘權重只做微調，不能喧賓奪主 |
| `w2`（importance） | 0.25 | 使用者/AI 明確標註的價值判斷，次於相關性 |
| `w3`（recency） | 0.15 | 讓新記憶有適度優勢，但不該蓋過真正相關或重要的舊記憶 |
| `w4`（access_frequency） | 0.10 | 最弱訊號，只作最後微調 |
| `λ`（衰減常數） | `ln(2)/90 ≈ 0.0077`（以天為單位） | 對應約 90 天（3 個月）的半衰期，呼應 `project` 類型記憶「衰減快」的設計共識 |

經 Python 重新計算分數並排序後，最後僅返回 Top-L（如 L=3）的記憶給 AI 助理。此機制能有效讓低重要性、長期未被存取的記憶自然沉底。

### 6.2 經驗學習迴路 (Experience Loop)
對應 `reflect_on_task` 工具：AI 在完成一項任務後，不等待使用者糾正，主動產出「這次做對/做錯在哪」的反思記錄（`type="Reflection"`）。下次遇到相似任務時，`search_memories` 會將這類記憶檢索出來作為前車之鑑，形成「執行 → 反思 → 檢索復用」的閉環，讓記憶庫從單純的事實/偏好記錄，進一步累積可重用的「經驗法則」。

### 6.3 定期固化 (Periodic Consolidation)
排程（例如每週）掃描符合以下條件的記憶：`created_at` 超過一定期限、`access_count` 偏低、且彼此語意相近（同一批向量分群）。將這群記憶交給 LLM 歸納成一條更高層次的摘要記憶，原始記憶批次標記 `status="archived"`（保留但不再參與主要檢索排序）。這對應人類睡眠固化記憶的機制：細節逐漸退場，留下的是被反覆驗證過的通則。此機制與 6.1 互補——衰減排序處理「排序權重」，定期固化處理「資料庫實際容量」。

### 6.4 軟刪除與稽核軌跡
延續第 5 章 `forget_memory` 與 `save_memory` 寫入閘門的設計：預設不做硬刪除，改以 `status`（`active` / `superseded` / `archived`）與 `superseded_by` 追蹤記憶的生命週期。好處是即使 AI 判斷錯誤，也能回溯記憶被取代或封存的原因，而不是直接遺失資訊。

---

## 7. 多專案共享記憶

實際部署架構已定案於 3.3 節（獨立 Firestore 專案 + 共用 GCE 主機），本節只說明各客戶端如何整合：

1. **統一後端**：所有專案皆向同一個 Mnemosyne 專屬 Firestore 專案讀寫（單一使用者，無需租戶隔離，詳見 2.3）。
2. **客戶端整合**：各客戶端連線 URL 固定帶入各自的 `domain`（見 3.3），Server 端依連線層級的 `domain` 自動隔離檢索範圍：
   * **Cursor IDE / Claude Desktop（寫程式）**：MCP 設定固定連到 `...?domain=coding`。
   * **本專案理財網頁**：MCP 設定固定連到 `...?domain=finance`，前端在使用者結束對話時觸發後端呼叫 `save_memory`，將理財會議記錄存入。
   * **未來其他生活/日常小工具**：比照上述模式，固定連到各自的 `domain`（如 `life`），無需修改 Server 端邏輯。

---

## 8. 專案開發里程碑 (Roadmap)

* [ ] **Phase 1: 基礎 API 開發**
  * 初始化 Python 專案，安裝 `fastapi`, `firebase-admin`, `mcp` 等套件。
  * 串接 OpenAI / Gemini Embedding API。
  * 申請獨立的 GCP 專案並啟用 Firestore（Spark 免費方案），與 `nocode-finance` 分開（見 3.3）。
  * 建立 `memories` 集合完整 Schema（含 `domain`、`is_pinned`、`importance_score`、`status`、`superseded_by`、`last_accessed_at`、`access_count` 等第 4 章全部欄位——這些欄位是寫入閘門與衰減排序的前提，不隨定期固化一起延後）。
  * 建立第 4.1 節唯一的複合向量索引：`domain (ASC) + status (ASC) + embedding (Vector)`。
  * 集中設定寫入閘門相似度門檻、衰減排序權重與 `λ` 衰減常數於 `config.py`。
* [ ] **Phase 2: MCP Server 封裝與部署**
  * 將 API 封裝成 MCP 標準協定的 SSE over HTTP Server（沿用 NoCode_Project 已驗證的模式，見 3.3）。
  * 實作連線層級的 `domain` 綁定（從 MCP 連線 URL 的 query string 讀取，見 3.3），取代逐次呼叫傳參數。
  * 實作 `save_memory` 三段式同步寫入閘門（5.1，含 `UPDATE` 分支的 embedding 重算）。
  * 實作 `search_memories` 雙軌並行檢索（domain/global 向量軌道 + tags 精確匹配軌道）與衰減排序合併（5.2、6.1）。
  * 實作 `forget_memory` 軟刪除語意（5.3）、`pin_memory` / `unpin_memory` / `load_pinned_memories`（5.5、5.6）。
  * 部署至既有 GCE 主機（與 `fintarck-backend` 共用，另開 systemd service 與 port）+ Cloud Run Proxy，設定 `MNEMOSYNE_MCP_KEY` 存取金鑰。
  * 分別以 `...?domain=coding` 與 `...?domain=finance` 兩條連線在 Cursor / Claude Desktop 進行整合測試，驗證跨領域隔離是否生效。
* [ ] **Phase 3: 記憶自動精煉與經驗學習**
  * 設計 Agent prompt 範本，確保 AI 會自動將冗長對話精煉成小於 500 字的 structured summary（含 `why` / `how_to_apply`）。
  * 實作「會議記錄存檔」與「每日決策報告存檔」自動觸發流程。
  * 實作 `reflect_on_task`（5.4）經驗學習迴路（6.2）。
* [ ] **Phase 4: 記憶治理與跨專案優化**
  * 實作定期固化排程（6.3）與衰減排序公式調校（6.1）。
  * 在理財專案的前端實作「記憶庫管理」Dashboard，允許檢視記憶狀態變化（`active`/`superseded`/`archived`）、手動封存或硬刪除。
  * 嘗試擴展到個人的其他開發、日常小工具中。
