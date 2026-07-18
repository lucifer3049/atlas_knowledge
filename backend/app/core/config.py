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


settings = Settings()
