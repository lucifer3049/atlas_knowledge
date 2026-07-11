from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全域設定;值來自環境變數 / backend/.env(見 .env.example)。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 憑證一律由環境提供(backend/.env,見 .env.example);缺值時啟動 fail-fast,NEVER 在原始碼放預設憑證
    database_url: str
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
