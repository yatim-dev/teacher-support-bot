from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_dsn: str
    teacher_tg_id: int

    auto_create_tables: int = 0  # 1 = создать таблицы при старте (для MVP)


settings = Settings()
