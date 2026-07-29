from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全域設定;值來自環境變數 / backend/.env(見 .env.example)。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 憑證一律由環境提供(backend/.env,見 .env.example);缺值時啟動 fail-fast,NEVER 在原始碼放預設憑證
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # 認證(T1.1;PHASE_1 §5.1)。JWT_SECRET 只從環境讀,缺值 fail-fast,NEVER 硬寫。
    jwt_secret: str
    access_token_ttl_min: int = 20
    refresh_token_ttl_days: int = 14
    cookie_secure: bool = False  # 正式環境 true(refresh cookie 走 HTTPS)

    # LLM 連線(T1.4;PHASE_1 §2.1)。model 名由 config/models.yaml 提供(§R R2,LLM_MODEL 作廢);
    # base_url 即 yaml `${LLM_BASE_URL}` 的來源;api_key/timeout 為連線層設定。
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_timeout_s: float = 120
    chat_system_prompt: str = "你是一個誠實、精簡的中文助理。"
    chat_history_max_messages: int = 20

    # 文件儲存(T2.1;PHASE_2 §2.1)。storage_root 為 local FS adapter 的根目錄;
    # DB 只存 storage key,NEVER 存絕對路徑(§4.1,S3 遷移不動資料)。
    storage_root: str = "/data/storage"
    max_upload_mb: int = 20

    # Chunking(T2.3;PHASE_2 §2.1、§7)。token 估算為自製(§F.5),NEVER tiktoken。
    chunk_target_tokens: int = 450
    chunk_overlap_tokens: int = 80

    # Embedding(T2.4;PHASE_2 §2.1)。開發預設 = Ollama bge-m3(OpenAI-compatible embeddings)。
    # embedding_dim MUST 與 models.py 的 EMBEDDING_DIM 及 migration 常數一致;變更 = 新遷移。
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: str = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_version: str = "bge-m3@1024"
    embedding_batch_size: int = 32
    embedding_timeout_s: float = 60
    # query embedding 快取 TTL(§9.1 D9,1h);chunk embedding 快取已於 v1.2 砍除。
    embedding_query_cache_ttl_s: int = 3600

    # Hybrid 檢索(T2.6;PHASE_2 §2.1、§9)。top_k = vector / fts 各取前 K;
    # top_n = RRF 後進 prompt 的 chunk 數;hnsw_ef_search 為檢索交易的 SET LOCAL 值。
    retrieval_top_k: int = 30
    retrieval_top_n: int = 8
    rrf_k: int = 60
    hnsw_ef_search: int = 60
    rag_context_char_budget: int = 9000


settings = Settings()
