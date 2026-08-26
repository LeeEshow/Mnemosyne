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
不綁定特定專案，但為了避免跨領域（如 coding 助理與理財助理）的語意污染，底層資料結構設計有 `domain` 欄位進行**資料庫端硬過濾**（見第 4 章）。這裡刻意選「過濾」而非「排序」——曾經討論過把 `domain` 降級成應用層過濾（全域向量搜尋後再篩選），但這只能**降低**跨領域污染機率、無法**消除**：當某個 domain 資料量遠少於其他 domain 時，稀疏 domain 的真實相關記憶可能被大量資料的 domain 排擠出 Top-K 候選集，排序權重救不回一筆連候選都進不去的記憶。因此 DB 端硬過濾維持不變。

同時，為了解決跨領域通用的個人偏好設定（例如「回覆一律繁體中文」、「不使用表情符號」），特別保留一個 `"global"` 特殊領域，檢索時自動與指定 domain 一併查詢（見 5.2）。

> **範圍界定**：本專案為**個人專屬**記憶體，不考慮多使用者隔離，因此不需要 `user_id` / `app_id`，亦不涉及多租戶層級的權限控制。`domain` 分區與多租戶隔離目的不同——多租戶隔離是為了資料所有權/存取控制，`domain` 純粹是為了避免同一個人的不同助理情境互相干擾檢索結果，兩者不可混為一談。

**v5 定案：DB 過濾與填值權責分離（取代舊版連線層級綁定 + 白名單）**

舊版設計把 `domain` 綁死在 MCP 連線字串上（`?domain=coding&allowed_overrides=...`），實測後發現可用性問題嚴重：每新增一個 domain，都要回頭逐一編輯每條 Client 連線字串的白名單，維護量級是「Client 數 × domain 數」。這個設計用「限制填值來源」換取安全性，但代價與其防範的風險不成比例——domain 誤歸類是個人資料的筆記歸錯類，可事後修正，不是資料外洩給他人，嚴重度其實與現行 `type` 欄位（AI 自由分類、從未設保護機制）同級。

重新設計後，拆成三個獨立負責的層次，不再綁在一起：

* **技術端（Firestore）**：`domain (ASC) + status (ASC) + embedding (Vector)` 複合索引與過濾機制不變（見 4.2），稀疏 domain 的保護持續有效，這一層與「值從哪來」無關。
* **連線端（FastAPI/Client）**：徹底移除連線層級的 domain 綁定與 `allowed_overrides` 白名單。所有 Client 共用**單一條**通用連線字串（只帶 `key` 身分驗證，見 3.3），新增 domain 後不需要修改任何連線設定。
* **決策端（AI/Tool）**：`domain` 改為 `save_memory` / `search_memories` / `load_pinned_memories` 的一般必填參數（見 5 章），AI 每次呼叫時自行決定要讀寫哪個 domain。

**Domain Registry（新增 `domains` 集合，見 4.1）**：為了讓 AI 的自由填值有約束、且「新增 domain」這個影響較大的動作經過人工把關，`domain` 值必須先在 registry 完成註冊才能使用：

1. **選用既有 domain**：AI 可自由選用，無需額外確認，風險等級比照現行 `type` 欄位的自由分類。
2. **新增 domain 需人工確認**：AI 無權在寫入或查詢當下隱式建立新 domain。若填入尚未註冊的值，Server 端一律拒絕，回傳 `status="requires_registration"`（見 5.1/5.2），附帶目前已註冊的 domain 清單。AI 收到後**必須暫停**，向使用者說明這是尚未存在的新領域、其定位為何，取得同意後才呼叫專用工具 `register_domain`（見 5.6）完成註冊，再重新呼叫原本失敗的操作。
3. **動態 Tool Description 注入（輔助，非防線）**：MCP Server 回應 `list_tools` 時即時查詢 `domains` 集合，把已註冊的 domain 名稱與描述拼進 `domain` 參數的 description，輔助 AI 判斷該填哪個既有值。這只是 UX 輔助——不同 MCP Client 對 `list_tools` 的重新拉取時機不一致，中途新增的 domain 未必即時反映在當次對話的 context 裡，**真正的強制力落在 `requires_registration` 的 Server 端攔截與拒絕上**，不能只靠描述注入把關。
4. **名稱正規化**：`domain` 值一律 `strip().lower()` 正規化後再比對/寫入，避免 `"Cooking"` 與 `"cooking"` 被視為不同 domain 而重複註冊。
5. **抑制分類漂移**：`register_domain` 的 description 明訂 AI 呼叫前必須先參考已注入的既有 domain 清單，確認沒有語意高度重疊的既有分類，優先建議使用者沿用既有 domain，而非隨手建立新的。
6. **既有資料遷移（部署前置步驟，見 3.3）**：上線前必須先掃描既有 `memories` 集合中出現過的所有 distinct domain 值，批次寫入 `domains` 完成 seed，否則舊資料會在切換當下被誤判為「未註冊」而全面擋下存取。**`"global"` 這個特殊值必須一併明確 seed**（例如 `description: "全域通用偏好與設定，檢索時會自動與指定領域合併，請勿在此寫入特定技術或專案知識。"`），不能假設它會被既有資料掃描自動涵蓋到——若舊資料庫裡從未真的寫過 `domain="global"` 的記憶，掃描結果就不會包含它，導致 Step 0 驗證誤判 `"global"` 未註冊。

### 2.5 因果記憶模型 (Causal Memory Model)
記憶不是孤立的事實清單，而是有「因為什麼、所以得到什麼結論」的因果關係——上一個結論也可能成為下一次判斷的因，反覆修正、疊代形成價值觀。這個人類認知仿生的觀察，直接影響第 4 章的 Schema 設計：

* 每筆記憶拆成 **`premise`（因，精煉脈絡）** 與 **`conclusion`（果，決策/結論）** 兩個欄位，取代單一的 `context` 自由文本。比起「因」，「果」才是實際會被套用的行為依據，但沒有「因」的脈絡，AI 無法判斷這個結論在新情境下還適不適用。
* 新的因如果命中舊的果 → 沿用（對應寫入閘門的 `NOOP`）；不命中 → 修正，舊的果成為推導新果的脈絡之一（對應 `SUPERSEDE`），舊記憶標記失效但不刪除。
* 因為任務執行的結果本質上也是一組因果記錄（因＝任務過程與成因，果＝經驗教訓），**`reflect_on_task` 不再是獨立工具**，直接併入 `save_memory`（見 5.1），同時也消除了原本一直沒收斂的「AI 該如何判斷該不該呼叫 `reflect_on_task`」這個啟發式規則難題。
* 完整的因果演變歷史不用另外設計圖狀結構儲存——`superseded_by`（見第 4 章）反向查詢即可拼湊：要找「這筆記憶修正了什麼」，查詢「誰的 `superseded_by` 指向這筆」即可。這個欄位因此身兼「稽核軌跡」與「因果鏈的邊」雙重角色。

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
* **協定標準**：Model Context Protocol (MCP) SDK，傳輸方式採 **Streamable HTTP**（單一 `/mcp` 端點，取代原規劃的 SSE transport——實測發現 SSE transport 在 `/mnemosyne/` 路徑前綴的反向代理下會因相對路徑重導向解析錯誤而失敗，詳見 `MCP_Service/CLAUDE.md`），不使用 Stdio。
* **資料庫**：Firebase/Google Cloud Firestore (Spark 免費方案，含 50k 每日免費讀取、20k 寫入、1 GiB 空間)
* **向量索引**：Firestore Native Vector Search (支援 HNSW 演算法、餘弦相似度)
* **Embedding 模型**：**Google `text-multilingual-embedding-002` (768維，Vertex AI) / `text-embedding-004`（個人 Google AI Studio 訂閱）二擇一（定案，依環境變數 `GEMINI_API_KEY` 是否設定切換，見下方待辦與 `MCP_Service/CLAUDE.md`）**。理由：與 Firestore/GCE 同屬 GCP 生態系可共用憑證；對繁體中文的語意理解足夠且經過 multilingual 優化。⚠️ 兩個模型的向量空間不相容，切換後既有記憶的 `embedding` 全部失效，需清空重新寫入。
* **寫入閘門判定 LLM**：**Gemini Flash 系列**（定案，同樣依 `GEMINI_API_KEY` 在 Vertex AI / 個人 Google AI Studio 訂閱間切換）。理由：NOOP/UPDATE/SUPERSEDE/CONFLICT_DETECTED/ADD 五選一分類任務不需要強推理能力，Gemini Flash 速度快、成本低，且同屬 GCP 生態系可共用憑證，不需額外引入 Anthropic/OpenAI API Key 造成跨雲依賴。若日後實測判斷品質不穩定，可再替換，抽換成本低。

> ⚠️ **待辦（部署階段發現，已部分解決）**：Vertex AI（embedding + Gemini Flash 判定）與 Firestore Spark 方案不同，即使用量落在免費額度內，專案本身仍必須掛上有效計費帳戶才能呼叫，否則一律 `403 PERMISSION_DENIED (BILLING_DISABLED)`。已改為優先使用個人 Google AI Studio 訂閱的 `GEMINI_API_KEY`（走個人訂閱額度，不再計入 GCP 帳單），`mnemosyne-cb868` 目前仍掛著帳單帳戶作為 `GEMINI_API_KEY` 未設定時的退路，之後可視情況評估是否要移除。
* **設定管理**：寫入閘門的相似度分段門檻（見 5.1）與衰減排序參數（見 6.1）屬於**模型相依參數**（不同 Embedding 模型的餘弦相似度分佈不同），統一集中於 `config.py`，不寫死在程式邏輯中。起始值見 6.1 與 5.1。

### 3.3 部署架構 (Deployment)
參考既有專案 `NoCode_Project`（`Docs/Cloud-Deployment.md`）已在生產環境驗證過的部署模式，Mnemosyne 直接沿用相同架構，降低重新摸索的成本：

```
MCP Client (Cursor / Claude Desktop / 理財網頁)
  └─ https://<proxy-host>/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>　（所有 Client 共用同一條連線字串，domain 不再綁在連線層級，見 2.3）
       └─ Cloud Run Proxy（Nginx reverse proxy，SSE 長連線 proxy_read_timeout）
            └─ GCE e2-micro（與 NoCode_Project 的 fintarck-backend **共用同一台主機**，另開一個 port 跑獨立的 systemd service，例如 mnemosyne.service）
                 └─ Firestore（**獨立的 GCP 專案**，不與 nocode-finance 共用配額）
```

**關鍵決策與理由**：
* **與 NoCode_Project 共用 GCE 主機（而非獨立主機）**：個人使用、流量低，e2-micro 資源用不滿，多開一個 systemd service 不需要額外月費；若日後流量成長到互相搶資源，再拆分成獨立主機即可，遷移成本不高。
* **Firestore 使用獨立 GCP 專案**：Mnemosyne 設計上是給多個不同專案共用的記憶體後端（見 2.3），若寄生在 `nocode-finance` 專案底下，每日讀寫額度（50k/20k）會被該專案本身的用量排擠，且概念上也不乾淨。獨立開一個 GCP 專案在 Firestore Spark 方案下仍是免費，沒有理由共用。
* **`domain` 改為工具參數，不再透過連線 URL 帶入**：早期設計比照 `?key=<MCP_ACCESS_KEY>` 的模式把 `domain` 也綁進連線 URL，但實測後發現新增 domain 需要逐條連線改設定，摩擦過大（見 2.3 v5 定案）。改為所有 Client 共用同一條連線字串，`domain` 由 AI 在每次工具呼叫時指定，並透過 Domain Registry + `requires_registration` 攔截機制把關（見 2.3、5.1/5.2）。
* **既有資料遷移**：v5 上線前，須先掃描 Firestore `memories` 集合中所有已出現過的 distinct `domain` 值，批次寫入新增的 `domains` 集合完成 seed，避免舊資料在切換當下被誤判為未註冊而擋下存取（見 2.3 第 6 點）。
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

最終 MCP 連線網址：`https://fintarck-proxy-xxx.asia-east1.run.app/mnemosyne/mcp?key=<MNEMOSYNE_MCP_KEY>`（所有 Client 共用同一條，不再帶 `domain`，見 2.3、3.3）。

---

## 4. 資料庫結構設計 (Database Schema)

在 Firestore 中建立名為 `memories` 的集合 (Collection)，每筆 Document 的欄位定義如下（13 欄位，精簡自初版的 14 欄位，見章末異動說明）：

| 欄位名稱 (Field) | 資料型別 (Type) | 必填 | 說明 |
| :--- | :--- | :---: | :--- |
| `type` | `String` | 🟢 | **記憶類別**。例如：`"Notes"`(筆記)、`"DailyReport"`(決策報告)、`"Preference"`(個人喜好)、`"Code"`(代碼知識)。原 `"Reflection"` 類別隨 `reflect_on_task` 併入 `save_memory`（見 2.5）而不再需要獨立列舉。 |
| `domain` | `String` | 🟢 | **領域分區**。由 AI 於工具呼叫時指定（如 `"coding"` / `"finance"` / `"life"`），須先於 Domain Registry 完成註冊（見 2.3、4.1）才能使用，避免跨領域語意污染，DB 端硬過濾（見 4.2）。特殊值 `"global"` 保留給跨領域都適用的通用偏好，查詢時永遠與指定 domain 一併檢索（見 5.2）。 |
| `title` | `String` | 🟢 | **記憶主題簡稱**。人工介入整理資料（如手動封存、Dashboard 瀏覽）時用於快速辨識，即使 `context` 已拆成 `premise`/`conclusion` 仍保留獨立欄位。 |
| `premise` | `String` | 🟢 | **因（精煉脈絡，限制 500 字內）**。促成這筆記憶的情境或前提，取代原本的 `context` 欄位。 |
| `conclusion` | `String` | 🟢 | **果（決策/結論）**。實際會被套用的行為依據，見 2.5 因果記憶模型。`premise`+`conclusion` 合併嵌入向量（見下方 `embedding` 說明）。 |
| `embedding` | `VectorValue` | 🟢 | **向量值**。儲存 `title + premise + conclusion` 的向量嵌入值。若記憶內容經 `UPDATE`/`SUPERSEDE` 而變動，**必須連同重新計算並覆寫此欄位**，否則向量會與文字內容脫鉤。 |
| `created_at` | `Timestamp` | 🟢 | **建立時間**。衰減排序（6.1）`recency` 項直接引用。 |
| `tags` | `Array (String)`| ⚪ | **標籤數組**。除了精確技術字串（錯誤代碼、函式名、股票代號），**也必須**涵蓋記憶內容的核心主題實體（人名、食物、具體事物、關鍵概念）——這是 5.2 精確匹配軌道與 5.1 標籤交集衝突偵測機制能否運作的前提，見 5.0 原則 6。 |
| `importance_score` | `Integer (1-10)` | 🟢 | **重要性評分**。寫入當下由 AI 自評，用於檢索排序加權，避免純靠向量相似度導致重要記憶被稀釋。 |
| `is_pinned` | `Boolean` | 🟢 | **常駐標記**。由 AI 或使用者透過 `pin_memory`（5.4）明確標記，而非僅依賴 `importance_score` 自動判斷，避免高分記憶隨時間累積過多、造成常駐清單膨脹。預設 `false`。 |
| `status` | `String` | 🟢 | **記憶狀態**：`"active"`(生效中) / `"superseded"`(已被取代) / `"archived"`(已固化封存)。取代直接刪除，保留稽核軌跡。預設 `"active"`。 |
| `superseded_by` | `String` | ⚪ | **取代者 ID**。若此記憶已被更新的記憶推翻，記錄新記憶的 `doc_id`。身兼雙重角色：(1) 稽核軌跡；(2) **因果鏈的邊**——要追溯「這筆記憶修正了什麼」，反向查詢「誰的 `superseded_by` 指向這筆」即可，不需要額外的鏈結欄位或圖狀結構（見 2.5）。 |
| `access_count` | `Integer` | ⚪ | **被檢索命中次數**。衰減排序（6.1）`access_frequency` 項直接引用，也是 Phase 4 定期固化的候選判斷依據。 |

**欄位精簡異動說明**（原 14 欄位 → 現 13 欄位）：
* **刪除 `source_id`**：全專案（Phase 1-4 Roadmap）沒有任何機制實際讀寫這個欄位，屬於「以防萬一」的預先設計，不符合 2.4 節不存清單的 YAGNI 精神；原本設想的「因果鏈追溯」用途，改用 `superseded_by` 反向查詢即可滿足，不需要專門欄位。
* **刪除 `last_accessed_at`**：核對 6.1 節衰減排序公式後發現，`recency` 項用的是 `created_at`、`access_frequency` 項用的是 `access_count`，這個欄位從未被任何公式或機制實際引用，是純粹冗餘欄位。
* **`context` 拆分為 `premise`/`conclusion`**：因果記憶模型（2.5）的核心改動，欄位數 +1，但語意更明確，非意外膨脹。

### 4.1 Domain Registry

支撐 2.3 節 v5 設計的新增集合，記錄目前所有已核准使用的 domain 值：

| 欄位名稱 | 資料型別 | 必填 | 說明 |
| :--- | :--- | :---: | :--- |
| `name` | `String` | 🟢 | Domain 名稱，唯一值，寫入/比對前一律 `strip().lower()` 正規化。 |
| `description` | `String` | 🟢 | 該領域的定位與語意邊界描述，供動態注入 `list_tools` 回應（見 2.3 第 3 點）與 AI 判斷選用依據。 |
| `created_at` | `Timestamp` | 🟢 | 建立時間。 |

新增（`register_domain`，見 5.6）需人工確認；查詢/選用（`list_domains`，見 5.7）無限制。實際的攔截流程（`requires_registration`）見 5.1/5.2。

**`list_tools` 動態描述注入的快取**：MCP Client（如 Cursor）在初始化或切換視窗時會高頻率呼叫 `list_tools`，若每次都即時查詢 Firestore，會不必要地消耗讀取配額並增加延遲。Server 端對 `domains` 集合查詢結果加上**記憶體內快取**（TTL 5-10 分鐘），過期才重新查詢。這與 2.3 第 3 點「動態描述只是 UX 輔助、不是唯一防線」的定位一致——快取造成的短暫延遲（新註冊的 domain 最多 5-10 分鐘後才會出現在描述裡）不影響正確性，因為真正的合法性把關在 `requires_registration` 攔截，不依賴描述是否即時。

### 4.2 索引策略 (Indexing Strategy)
Firestore 向量搜尋（`find_neighbors`）與一般 `where` 條件合併查詢時，強制要求為該組合建立複合索引，且對 `IN` 等運算子的支援度有限、容易在動態查詢組合下出錯。為避免索引數量隨查詢條件組合爆炸，採取以下限縮策略：

* **DB 端只固定一組複合索引**：`domain (ASC) + status (ASC) + embedding (Vector)`。所有向量查詢一律走這個索引，不隨 `type`/`tags` 動態變化。`domain` 維持 DB 端硬過濾（見 2.3），不論該值是連線帶入還是工具參數帶入都套用同一個索引，不需要額外索引。
* **`type` 過濾**：不進 DB 查詢，改為向量查詢取回 Top-K（如 K=40）後，於應用層（Python）過濾。
* **`tags` 過濾**：分兩種用途處理——輔助篩選一樣用應用層過濾；但作為「精確匹配軌道」（5.2）或「寫入閘門標籤交集軌道」（5.1）時，改用單欄位 `array-contains-any` 查詢，這是 Firestore 原生支援、不需複合索引的查詢型態；查詢結果的 `domain`/`status` 過濾放在應用層做，避免額外複合索引需求。
* **`domain` 的 `global` 特例不使用 `IN` 運算子**：改為對 `domain == 指定值` 與 `domain == "global"` 各發送一次獨立的向量查詢（`asyncio.gather` 並行），在應用層依 Document ID 去重合併，完全避開向量搜尋結合 `IN` 的相容性風險，且仍只需要上述單一複合索引。

---

## 5. MCP Tool 介面定義 (API Specification)

Mnemosyne MCP Server 將對外暴露**八個**核心工具 (Tools) 供 AI Agent 調用（原 `reflect_on_task` 已依 2.5 節因果模型併入 `save_memory`，不再獨立；v5 新增 `list_domains`、`register_domain`，見 5.6、5.7）。

> **`domain` 是一般必填工具參數，不是連線層級上下文**：依 2.3 節 v5 定案，所有 Client 共用同一條連線字串，`domain` 改由 AI 在每次呼叫 `save_memory` / `search_memories` / `load_pinned_memories` 時明確指定，並須先於 Domain Registry（4.1）完成註冊——填入未註冊的值，Server 端一律拒絕並回傳 `status="requires_registration"`（見 5.1/5.2），不會靜默失敗或誤用預設值。

### 5.0 Tool Description 撰寫原則

MCP Tool 的 `description` 字串會被直接注入 AI 的 context，直接影響 AI「該不該呼叫」「呼叫哪個」的判斷品質，因此撰寫時遵循以下設計原則（各工具收斂後的實際文字定案於 `MCP_Service/Task.md` 2.1 節，此處只記錄原則本身，避免規格文字分散兩處造成不同步）：

1. **開頭寫觸發情境，不是功能敘述**：LLM 決定是否呼叫工具主要看「情境符不符合」，不是「工具做什麼」，第一句話應是「當...時使用」。
2. **`domain` 參數的 description 要引導 AI 先查再填，而非憑空生成**：`domain` 已是自由參數（見 5 章開頭），description 應提示 AI 參考動態注入的既有 domain 清單（見 2.3 第 3 點）選用；填入未註冊的值會被拒絕並收到 `requires_registration`，這件事也要在 description 講清楚，讓 AI 不會誤以為隨便填字串都會成功。
3. **明確揭露 AI 不用自己做的前置檢查，也要反過來明講必要的前置查詢**：`save_memory` 內建寫入閘門會自動去重合併（5.1），不需要 AI 自己先呼叫 `search_memories` 確認重複；但 `forget_memory` / `pin_memory` 需要 `doc_id`，必須先呼叫 `search_memories` 取得，兩種情況都要在描述中寫清楚，不能只強調前者造成誤導。
4. **內部演算法細節不寫進會被注入 context 的 description**：寫入閘門判定邏輯（5.1）、衰減排序公式（6.1）等屬於 Server 內部行為，AI 不需要知道細節就能正確使用工具，寫太細只會浪費注入 context 的 token，並可能誤導 AI 以為自己該介入控制。
5. **參數層級的硬限制與判斷依據要寫在該參數自己的 description，並附具體範例**：例如 `search_memories.exact_tags` 與 `save_memory.tags` 都該明確舉例該填入的情境，而不是籠統帶過；`premise`/`conclusion` 的 500 字限制同理寫在參數說明而非工具總說明。
6. **`tags` 的撰寫指引不能只鎖定技術字串**：除了錯誤代碼、函式名、股票代號等精確技術字串，也**必須**強烈引導 AI 提取記憶內容中的**核心主題實體**（人名、食物、具體事物、關鍵概念），這是 5.1 節「標籤交集衝突偵測」機制能否觸發的前提——如果 AI 不會把一般性主題詞存進 `tags`，衝突偵測從源頭就不會運作。
7. **牽涉不可逆動作或衝突判定的規則，用不可協商的強制語氣**：例如 5.1 節「偵測到衝突必須先問使用者、不可自行判斷執行」這類規則，不能寫成建議語氣，必須用「⚠️ 硬性規則」等明確標記，降低 AI 自行裁量繞過的機率。

### 5.1 `save_memory`
> **功能**：由 AI 助理主動呼叫，將討論精華轉化為向量並存入資料庫。**寫入前會先經過「寫入閘門」，避免重複與衝突資訊持續累積。閘門為同步執行**（非背景任務）——考量個人規模的寫入頻率不高，同步呼叫 LLM 判定的延遲可接受，優先維持實作簡單，不引入額外的任務佇列基礎設施。

* **輸入參數**：
  * `domain` (string, required): 領域分區，須為 Domain Registry（4.1）中已註冊的值（正規化後比對），見 5 章開頭說明。
  * `title` (string, required): 記憶主題。
  * `premise` (string, required): 因——限制 500 字內的精煉脈絡。
  * `conclusion` (string, required): 果——決策/結論，實際會被套用的行為依據（見 2.5）。若這是任務結果的回顧（原 `reflect_on_task` 用途），`premise` 填任務過程與成因、`conclusion` 填經驗教訓，不需要另外呼叫別的工具。
  * `type` (string, required): 記憶類別（如 `Notes`, `DailyReport`, `Preference`）。
  * `tags` (array of strings, optional): 關聯標籤，**除了精確技術字串，也務必包含核心主題實體**（見 4 章 `tags` 說明、5.0 原則 6）。
  * `importance_score` (integer 1-10, optional): AI 自評重要性，未提供則預設中間值。
* **後台邏輯（寫入閘門 Write-time Gate，同步判定）**：
  0. **Domain 驗證（優先於寫入閘門，避免無謂的向量化與 LLM 呼叫成本）**：正規化 `domain` 值後比對 Domain Registry；未註冊則立刻回傳 `decision="requires_registration"`（沿用 `SaveMemoryResponse` 既有的 decision 欄位，與 `conflict_detected` 同一套結構化回應模型，比照寫入閘門其他決定值處理，不額外設計新的錯誤結構），附帶目前已註冊的 domain 清單，不進入下方候選查詢與判定流程（見 2.3、5.6/5.7）。
  1. **雙軌並行候選查詢**（同 domain 內）：
     * **軌道 A（向量）**：將 `title+premise+conclusion` 向量化，查詢同 domain 內最近鄰，取回 Top-3（`WRITE_GATE_CANDIDATE_LIMIT=3`）。
     * **軌道 B（標籤交集）**：對新記憶的 `tags` 執行 `array-contains-any` 查詢，找出同 domain 內有任一標籤重疊的既有記憶，不受向量相似度限制。
     * 兩軌結果依 Document ID 去重合併，作為判定閘門的完整候選名單——**軌道 B 的存在是必要的**：修正型的新資訊（例如推翻舊結論的陳述）用詞常與舊記憶差異很大，向量相似度可能遠低於判定門檻，若只看軌道 A 會直接漏掉這類候選，看不出衝突。
  2. **判定分流**（門檻值定義於 `config.py`，起始值基於 `text-multilingual-embedding-002`，可日後依實測調整）：
     * **字串完全相同（正規化後）**：直接 **NOOP**，不呼叫 LLM，僅更新既有記憶的 `access_count`。
     * **候選名單為空，或所有候選相似度皆低於 `LOW_THRESHOLD = 0.85` 且無標籤交集**：直接 **ADD**，視為全新記憶，不呼叫 LLM。
     * **其餘情況**（相似度 ≥ 0.85，**或**存在標籤交集的候選，不論相似度高低）：一律呼叫 **Gemini Flash** 判定，由其選擇：
       * **NOOP**：語意重複，不寫入。
       * **UPDATE**：屬於既有記憶的補充延伸（例如新增一項並存事實），合併內容並**重新計算 `embedding`**，不可只改文字不更新向量。
       * **SUPERSEDE**：新資訊推翻舊記憶（因果模型的「修正」，見 2.5）。Gemini Flash 在判定的同一次呼叫中，**同時生成新記憶的 `premise`/`conclusion`**——以「重新摘要」而非「逐字串接」的方式，把舊結論、新資訊、修正後結論整合成一段簡潔敘事，維持 `active` 記憶的精確與簡潔；完整的歷史演變過程不寫進文字內容，改由 `superseded_by` 鏈結、需要時由 Python 遞迴查詢還原，避免文字內容隨修正次數滾雪球膨脹。舊記憶 `status` 設為 `"superseded"` 並填入 `superseded_by`。
       * **CONFLICT_DETECTED**：若 Gemini Flash 判定新舊記憶存在**邏輯矛盾**（而非單純的延伸或無關），**拒絕寫入**，回傳 `decision="conflict_detected"`，附帶衝突的舊記憶 `doc_id` 與內容。
       * **ADD**：LLM 判斷雖有相似度或標籤交集但實為獨立事件，視為全新記憶寫入。

  > ⚠️ **硬性規則**：當 `save_memory` 回傳 `decision="conflict_detected"` 時，AI **必須**立刻暫停所有操作，在對話中向使用者詳細說明新舊記憶的衝突內容，並詢問使用者要「覆蓋」還是「並存」。AI **絕對不可**在未取得使用者明確回覆前，擅自呼叫 `forget_memory` 或自行判斷處理。取得回覆後：若選擇覆蓋，AI 呼叫 `forget_memory(doc_id=<舊記憶>)` 封存，再重新呼叫 `save_memory` 存入新記憶；若選擇並存，由 AI 引導使用者或自行調整文字脈絡後重新存入。

### 5.2 `search_memories`
> **功能**：當使用者詢問過去的事情或需要歷史脈絡時，AI 助理調用此工具檢索相似記憶。採用「雙軌並行檢索 + 應用層合併 + 衰減排序」的混合檢索器，彌補純向量搜尋在精確字串比對與 Top-K 截斷上的弱點。

* **輸入參數**：
  * `domain` (string, required): 領域分區，須為 Domain Registry（4.1）中已註冊的值，見 5 章開頭說明。未註冊則拒絕查詢，不用空結果代替（避免 AI 誤判為「使用者從未提過」而產生錯誤斷言）。
  * `query` (string, required): 查詢字句（例如：「上次討論 0056 的停利點設定是什麼？」）。
  * `type` (string, optional): 指定記憶類別進行過濾（應用層過濾，見 4.2）。
  * `exact_tags` (array of strings, optional): 需要精確比對的關鍵字（如錯誤代碼、股票代號、主題實體），觸發精確匹配軌道。
  * `limit` (integer, optional, default=3): 返回的最大記憶數量。
  * `include_superseded` / `include_archived` (boolean, optional, default=false): 是否納入非 `active` 狀態的記憶（供「深度搜尋」挖掘已封存內容或因果演變歷史）。
* **後台邏輯**：
  0. **Domain 驗證（拋出例外，非結構化回應）**：`search_memories`/`load_pinned_memories`（見 5.5）屬於單純的檢索工具，正常回應（`SearchMemoriesResponse`）只是記憶列表，不像 `save_memory` 有現成的 decision 欄位可承載狀態；為避免 Response Schema 混入「這不是搜尋結果」的分支，改在 Use Case/Interface 層 `raise DomainNotRegisteredError`（自訂例外，附帶已註冊 domain 清單），交由 MCP SDK 自動轉換成 `isError=True` 的 MCP 錯誤回應。AI 讀到錯誤訊息後同樣能觸發暫停與人工確認（見 5.7 硬性規則），行為與 `save_memory` 一致，只是承載方式依工具語意分開設計。
  1. **向量軌道（並行）**：將 `query` 向量化，透過 `asyncio.gather` 同時對 `domain == 指定值` 與 `domain == "global"` 各發送一次向量查詢（見 4.2 索引策略），K 值不對稱設定（如指定 domain K=40、global K=10），避免不必要的讀取與運算量。
  2. **精確匹配軌道（並行）**：若提供 `exact_tags`，並行對 `tags array-contains-any exact_tags` 發送查詢，不受向量相似度限制。
  3. **合併與排序**：三路結果依 Document ID 去重合併後，統一套用 **衰減排序公式**（詳見 6.1）重新排序，而非單純以向量相似度排序。預設排除 `status != "active"`，除非 `include_superseded`/`include_archived` 為 `true`。
  4. 回傳 Top-K 的 `premise`/`conclusion` 列表，同步更新命中記憶的 `access_count`。

### 5.3 `forget_memory`
> **功能**：將記憶下架。預設為「軟刪除」（標記封存），僅在明確指定時才真正硬刪除。

* **輸入參數**：
  * `doc_id` (string, required): Firestore 文件 ID。
  * `hard_delete` (boolean, optional, default=false): 為 `true` 時才真正刪除文件；預設僅將 `status` 設為 `"archived"`，保留稽核軌跡。

### 5.4 `pin_memory` / `unpin_memory`
> **功能**：明確標記／取消標記某筆記憶為「常駐記憶」，解決衰減排序（6.1）中極端重要但可能被向量 Top-K 截斷、或因權重不足而排不進結果的問題。採**明確標記制**而非分數自動判斷，避免常駐清單隨 `importance_score` 高分記憶累積而無限膨脹。

* **輸入參數**：
  * `doc_id` (string, required): Firestore 文件 ID。
  * （`unpin_memory` 同參數，將 `is_pinned` 設回 `false`）
* **後台邏輯**：更新該筆記憶的 `is_pinned` 欄位。

### 5.5 `load_pinned_memories`
> **功能**：供 MCP Client 在對話開始時呼叫，取得少量常駐記憶直接帶入 context，不經過向量檢索，避免第 5.2 節混合檢索仍可能漏掉「極端重要」記憶的風險。

* **輸入參數**：
  * `domain` (string, required): 同 5.1 說明，須為已註冊值。
  * `limit` (integer, optional, default=5): 上限筆數，避免常駐清單膨脹造成 context 污染與 Token 成本增加。
* **後台邏輯**：Domain 驗證（拋出例外，同 5.2 第 0 步說明）後，並行查詢 `domain == 指定值` 與 `domain == "global"` 且 `is_pinned == true` 且 `status == "active"` 的記憶，在 Python 端合併後，依 `importance_score` 與 `created_at` 降序排序，最後取前 `limit` 筆回傳。

### 5.6 `list_domains`
> **功能**：查詢 Domain Registry 目前已註冊的所有 domain 及其定位描述。主要供人工檢視/管理使用；AI 判斷該填哪個既有 domain 的依據以動態注入的 `list_tools` 描述（見 2.3 第 3 點）為主，不強制每次呼叫前都先呼叫此工具。

* **輸入參數**：無。
* **後台邏輯**：讀取 `domains` 集合全部文件，回傳 `name`/`description`/`created_at` 列表。

### 5.7 `register_domain`
> **功能**：註冊一個新的 domain。**僅在使用者於對話中明確同意後才可呼叫**，不可由 AI 自行判斷觸發（見 2.3、5.1/5.2 的 `requires_registration` 攔截流程）。

* **輸入參數**：
  * `name` (string, required): 新 domain 名稱，寫入前正規化（`strip().lower()`）並檢查唯一性，重複則回傳既有註冊資訊、不重複建立。
  * `description` (string, required): 該領域的定位與語意邊界描述。
* **後台邏輯**：
  1. **前置引導（寫在 description，非程式強制）**：呼叫前必須先參考已注入的既有 domain 清單，確認沒有語意高度重疊的既有分類；若有，應建議使用者沿用既有 domain 而非新建，降低分類漂移風險（見 2.3 第 5 點）。
  2. 正規化後寫入 `domains` 集合。

  > ⚠️ **硬性規則**：AI 不可在 `save_memory`/`search_memories` 收到 `requires_registration` 後自行呼叫此工具完成註冊。必須先在對話中向使用者說明這是新領域、其定位為何，取得明確同意後才可呼叫，比照 5.1 節 `CONFLICT_DETECTED` 的人工確認硬規則。

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

### 6.2 因果修正迴路 (Causal Revision Loop)
原「經驗學習迴路」隨 `reflect_on_task` 併入 `save_memory`（見 2.5）而重新框架：任務結果本質上就是一組因果記錄（`premise`=過程與成因、`conclusion`=經驗教訓），AI 完成任務後可直接呼叫 `save_memory` 記錄，不需要獨立工具或獨立的觸發時機判斷。下次遇到相似情境時，`search_memories` 會將這類記憶檢索出來作為前車之鑑；若新情境與舊結論矛盾，則透過 5.1 節的寫入閘門走 `SUPERSEDE` 或 `CONFLICT_DETECTED` 流程，形成「因 → 果 → 檢索復用 → 因情境變化而修正」的持續迴圈，而不是單向累積的事實清單。

### 6.3 定期固化 (Periodic Consolidation)
排程（例如每週）掃描符合以下條件的記憶：`created_at` 超過一定期限、`access_count` 偏低、且彼此語意相近（同一批向量分群）。將這群記憶交給 LLM 歸納成一條更高層次的摘要記憶，原始記憶批次標記 `status="archived"`（保留但不再參與主要檢索排序）。這對應人類睡眠固化記憶的機制：細節逐漸退場，留下的是被反覆驗證過的通則。此機制與 6.1 互補——衰減排序處理「排序權重」，定期固化處理「資料庫實際容量」；與 6.2 的差異是，固化處理的是「同一批語意相近的獨立記憶」，因果修正處理的是「同一件事的結論演變」，兩者不衝突。

### 6.4 軟刪除、稽核軌跡與衝突攔截
延續第 5 章 `forget_memory` 與 `save_memory` 寫入閘門的設計：預設不做硬刪除，改以 `status`（`active` / `superseded` / `archived`）與 `superseded_by` 追蹤記憶的生命週期，`superseded_by` 同時是因果鏈的邊（見 2.5、4 章）。好處是即使 AI 判斷錯誤，也能回溯記憶被取代或封存的原因，而不是直接遺失資訊。

在此基礎上，5.1 節的 `CONFLICT_DETECTED` 機制補上了最後一塊拼圖：軟刪除/SUPERSEDE 解決的是「系統判斷出這是修正，該怎麼記錄」，但**系統判斷不出來、或判斷為邏輯矛盾（而非延伸）的情況，不該讓 AI 自己決定要覆蓋還是保留**——這類決策交還給使用者本人確認（見 5.1 硬性規則），系統只負責攔截、不負責自動裁定。

---

## 7. 多專案共享記憶

實際部署架構已定案於 3.3 節（獨立 Firestore 專案 + 共用 GCE 主機），本節只說明各客戶端如何整合：

1. **統一後端**：所有專案皆向同一個 Mnemosyne 專屬 Firestore 專案讀寫（單一使用者，無需租戶隔離，詳見 2.3）。
2. **客戶端整合**：v5 起所有客戶端共用同一條連線 URL（見 3.3），不再各自綁定 `domain`；隔離改由 AI 在每次工具呼叫時指定已註冊的 `domain` 值達成：
   * **Cursor IDE / Claude Desktop（寫程式）**：呼叫時傳入 `domain="coding"`。
   * **本專案理財網頁**：呼叫時傳入 `domain="finance"`，前端在使用者結束對話時觸發後端呼叫 `save_memory`，將理財會議記錄存入。
   * **未來其他生活/日常小工具**：比照上述模式，呼叫時傳入各自的 `domain`（如 `life`）；若為全新領域，先經 5.7 節的人工確認註冊流程，無需修改任何連線設定或 Server 端邏輯。

---

## 8. 專案開發里程碑 (Roadmap)

* [ ] **Phase 1: 基礎建設**（GCP/Firestore、Python 專案骨架、Embedding/LLM 串接、`config.py` 參數）
* [ ] **Phase 2: MCP Server 開發與部署**（八個工具、Domain Registry + `requires_registration` 攔截、寫入閘門雙軌候選與衝突偵測、GCE/Nginx/Cloud Run 部署、既有資料 domain 遷移、整合測試）
* [ ] **Phase 3: 記憶自動精煉**（設計 Agent prompt 範本，引導 AI 判斷「值得存」的時機並精煉成因果結構；原「經驗學習迴路」已因 `reflect_on_task` 併入 `save_memory`（2.5、6.2）而不再是獨立階段性任務）
* [ ] **Phase 4: 記憶治理與跨專案優化**：實作定期固化排程（6.3）與衰減排序公式調校（6.1）；理財專案前端「記憶庫管理」Dashboard；擴展到其他個人專案

> 詳細任務拆解、進度與實作補充說明見 [MCP_Service/Task.md](../MCP_Service/Task.md)，本節只維持階段性總覽，避免與 Task.md 規格文字重複、日後不同步。
