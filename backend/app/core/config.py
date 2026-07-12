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


settings = Settings()
