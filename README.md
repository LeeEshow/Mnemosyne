# Mnemosyne — 跨領域 AI 長期記憶體

個人專屬的長期記憶層，透過 Model Context Protocol (MCP) 讓不同 AI 助理（Cursor、Claude Desktop、理財網頁等）共用同一份記憶庫，解決「對話結束即失憶」的問題。以領域（`domain`）區隔不同情境的記憶，同時保留跨領域共享的全域偏好設定。

---

## 核心設計

- **高訊雜比**：只存 AI 精煉後的因果結構（`premise`/`conclusion`，各限 500 字），不存原始逐字對話
- **因果記憶模型**：每筆記憶是「因為什麼、所以得到什麼結論」，新舊記憶矛盾或修正時走寫入閘門判定，而非單向累積事實清單
- **寫入閘門（Write Gate）**：新記憶寫入前，雙軌候選查詢（向量最近鄰 + 標籤交集）+ LLM 判定 `NOOP`/`UPDATE`/`SUPERSEDE`/`CONFLICT_DETECTED`/`ADD` 五選一，避免重複與衝突資訊持續累積；偵測到邏輯矛盾時**強制**暫停詢問使用者，不可由 AI 自行覆蓋
- **Domain Registry**：`domain` 是自由的工具參數（不綁定連線層級），但新增 domain 需經人工確認註冊，避免 AI 隨手建立分類造成漂移
- **跨領域隔離 + 全域共享**：`domain` 於 DB 端硬過濾，避免稀疏領域的記憶被排擠；保留 `"global"` 特殊領域供跨領域通用偏好使用

詳細設計脈絡見 [`Docs/Mnemosyne_MCP_Proposal.md`](Docs/Mnemosyne_MCP_Proposal.md)。

---

## 技術架構

```
MCP Client (Cursor / Claude Desktop / 理財網頁)
  └─ https://fintarck-proxy-*.asia-east1.run.app/mnemosyne/mcp?key=<KEY>
       └─ Cloud Run Proxy（Nginx reverse proxy，Streamable HTTP）
            └─ GCE e2-micro（與 NoCode_Project 共用主機，獨立 systemd service :8001）
                 └─ Firestore（獨立 GCP 專案 mnemosyne-cb868，Native Vector Search）
                      + Gemini（embedding + 寫入閘門判定，個人 Google AI Studio 訂閱）

GitHub Actions
  └─ deploy-mnemosyne.yml → 推送 MCP_Service/** 自動 SSH 部署至 GCE
```

| 技術 | 用途 |
|------|------|
| Python 3.10 + FastAPI | 後端框架（受限於部署主機鎖定版本） |
| MCP SDK（Streamable HTTP） | AI 助理串接協定 |
| Firebase / Google Cloud Firestore | 記憶儲存 + Native Vector Search |
| `gemini-embedding-001` | 向量嵌入（個人 Google AI Studio 訂閱） |
| `gemini-3.6-flash` | 寫入閘門判定（NOOP/UPDATE/SUPERSEDE/CONFLICT_DETECTED/ADD） |

後端採 **Hexagonal Architecture（Ports & Adapters）**，把業務邏輯與 Python/GCP 框架細節隔離，方便日後遷移到其他語言/框架。架構細節、目錄結構、開發規範見 [`MCP_Service/CLAUDE.md`](MCP_Service/CLAUDE.md)。

---

## 專案結構

```
Mnemosyne/
├── Docs/
│   └── Mnemosyne_MCP_Proposal.md   # 權威設計文件
├── MCP_Service/                    # MCP Server 原始碼
│   ├── domain/                     # 零框架依賴的業務邏輯
│   ├── application/                # Use case 協調層
│   ├── infrastructure/             # Firestore/Gemini 具體實作
│   ├── interface/                  # MCP tool 註冊、Pydantic schema
│   ├── scripts/                    # 部署輔助腳本（如 domain registry 遷移）
│   ├── Task.md                     # 開發任務清單與現況
│   └── CLAUDE.md                   # 開發規範（供 AI 輔助開發）
└── .github/workflows/              # GitHub Actions CI/CD
```

---

## 本地開發

```bash
cd MCP_Service
.venv/Scripts/python.exe -m pip install -e .

# 執行 MCP Server
.venv/Scripts/python.exe -m interface.mcp_server
```

必要環境變數：`MNEMOSYNE_MCP_KEY`（連線驗證金鑰）。AI 服務憑證與其他選填變數見下方。

---

## 環境變數

| 變數 | 說明 |
|------|------|
| `MNEMOSYNE_MCP_KEY` | 連線驗證金鑰，必填，未設定則服務拒絕所有連線 |
| `GEMINI_API_KEY` | 個人 Google AI Studio API Key；設定後 embedding 與寫入閘門判定改走個人訂閱額度，不計入 GCP 帳單 |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Firestore 存取用的服務帳戶金鑰（base64） |
| `MNEMOSYNE_GOOGLE_CLOUD_PROJECT_ID` | GCP 專案 ID（預設 `mnemosyne-cb868`） |
| `MNEMOSYNE_GOOGLE_CLOUD_LOCATION` | Firestore/embedding 所在區域（預設 `asia-east1`） |
| `MNEMOSYNE_GEMINI_CLASSIFIER_LOCATION` | Vertex AI 模式下 Gemini 分類器的區域（預設 `us-central1`） |

完整清單與各變數的注意事項見 [`MCP_Service/CLAUDE.md`](MCP_Service/CLAUDE.md)。

---

## 部署

比照既有專案 `NoCode_Project` 的部署模式：GCE e2-micro（共用主機）+ Cloud Run Nginx Proxy + Firestore Spark。完整部署架構、環境變數設定與部署過程中排查出的環境/函式庫層級問題，詳見：

| 文件 | 說明 |
|------|------|
| [`Docs/Mnemosyne_MCP_Proposal.md`](Docs/Mnemosyne_MCP_Proposal.md) | 完整設計文件（願景、架構、Schema、API 規格、記憶治理機制） |
| [`MCP_Service/Task.md`](MCP_Service/Task.md) | 開發任務清單、現況總覽 |
| [`MCP_Service/CLAUDE.md`](MCP_Service/CLAUDE.md) | 架構、開發規範、部署踩坑紀錄（供 AI 輔助開發） |

**CI/CD**：推送至 `main` 分支且變更 `MCP_Service/**` 會自動觸發 GitHub Actions 部署至 GCE。
