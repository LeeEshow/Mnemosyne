import os

GOOGLE_CLOUD_PROJECT_ID = os.environ.get("MNEMOSYNE_GOOGLE_CLOUD_PROJECT_ID", "mnemosyne-cb868")
GOOGLE_CLOUD_LOCATION = os.environ.get("MNEMOSYNE_GOOGLE_CLOUD_LOCATION", "asia-east1")
# Gemini 系列模型在 Vertex AI 的可用區域比 embedding 模型窄很多，asia-east1 不支援，
# 獨立設定避免受限於 Firestore/embedding 共用的 GOOGLE_CLOUD_LOCATION。
GEMINI_CLASSIFIER_LOCATION = os.environ.get("MNEMOSYNE_GEMINI_CLASSIFIER_LOCATION", "us-central1")

# 模型名稱依是否設定 GEMINI_API_KEY 連動：走個人 Google AI Studio 訂閱時，
# 用該平台的向量模型 gemini-embedding-001（原生輸出 3072 維，但 Firestore 向量索引維度上限
# 是 2048，故用 Matryoshka 截斷輸出到 EMBEDDING_DIMENSION=1536，索引也建在這個維度上）；
# 未設定則退回 Vertex AI 的 text-multilingual-embedding-002（固定輸出 768 維，不支援截斷）。
# ⚠️ 索引已改成 1536 維後，Vertex AI 這條路徑目前實際上已不可用（維度對不上），除非之後
# 另外調整索引或改用支援指定輸出維度的 Vertex 模型，見 CLAUDE.md。
EMBEDDING_MODEL = "gemini-embedding-001" if os.environ.get("GEMINI_API_KEY") else "text-multilingual-embedding-002"
EMBEDDING_DIMENSION = 1536
# gemini-2.5-flash 已從 Google AI Studio（Developer API）的新帳號可用清單下架
# （404 "no longer available to new users"），Vertex AI 企業版生命週期是分開的，
# 目前仍可用，所以比照 EMBEDDING_MODEL 依 GEMINI_API_KEY 連動切換。
GATE_CLASSIFIER_MODEL = "gemini-3.6-flash" if os.environ.get("GEMINI_API_KEY") else "gemini-2.5-flash"

FIRESTORE_ARRAY_CONTAINS_ANY_LIMIT = 10

MEMORIES_COLLECTION_NAME = "memories"
DOMAINS_COLLECTION_NAME = "domains"
GLOBAL_DOMAIN = "global"
SERVER_PORT = 8001

DOMAIN_LIST_CACHE_TTL_SECONDS = 300

LOW_THRESHOLD = 0.85
DEFAULT_IMPORTANCE_SCORE = 5

WEIGHT_RELEVANCE = 0.50
WEIGHT_IMPORTANCE = 0.25
WEIGHT_RECENCY = 0.15
WEIGHT_ACCESS_FREQUENCY = 0.10

DECAY_LAMBDA = 0.0077

ACCESS_FREQUENCY_LOG_CAP = 20

VECTOR_SEARCH_K_DOMAIN = 40
VECTOR_SEARCH_K_GLOBAL = 10

WRITE_GATE_CANDIDATE_LIMIT = 3

SEARCH_MEMORIES_DEFAULT_LIMIT = 2
PINNED_MEMORIES_DEFAULT_LIMIT = 5
