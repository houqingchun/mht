from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403

config = context.config
# `%` 必须转义成 `%%`。alembic 1.20 的 `Config.file_config` 用的是 ConfigParser 的
# `BasicInterpolation`，而 `.env` 里的口令是**百分号编码**过的（`p@ss` → `p%40ss`，
# 见 install.ps1 的 `New-DatabaseUrl`）：不转义就直接抛
# `ValueError: invalid interpolation syntax in 'mysql+pymysql://root:p%40ss@…' at position 22`。
# 它撞的是**任何含符号的 MySQL 口令**（`@ : / # %` 全都编码），不是罕见形状，而报出来的
# 那句话与真正的原因毫无关系。转义之后 `get_main_option` / `get_section` 还原回来的是
# **逐字相同**的连接串，所以下面两处读它的地方一个字都不用改。
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

