from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "心晴心理测评与关怀平台"
    api_prefix: str = "/api/v1"
    database_url: str = "mysql+pymysql://root:password@127.0.0.1:3306/xinliceping"
    jwt_secret: str = "dev-only-change-me-dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    password_lock_threshold: int = 5
    password_lock_minutes: int = 30

    # 部署参数（CLAUDE.md §18）。默认值全是**开发与测试原样**的那一套：
    # `web_dir=""` 表示不托管前端，所以 vite 与 pytest/e2e 一个字的行为都不变。
    host: str = "127.0.0.1"
    port: int = 8000
    web_dir: str = ""
    log_dir: str = ""
    log_level: str = "INFO"
    # `/docs`、`/redoc`、`/openapi.json` 是**开发期的便利**：它们把整套接口形状
    # （含权限依赖的名字与参数）公开给任何能访问到端口的人。开发机上无所谓，
    # 学校里那台机器上没必要——那一整个网段的人都能拉到。安装脚本写 0 关掉它。
    docs_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="XLP_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
