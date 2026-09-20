"""测试夹具：一套跑在**真 MySQL** 上的、每个用例都干净的库。

2026-09-19 之前这里是内存 SQLite + `Base.metadata.create_all`（CLAUDE.md 已知缺口 3）。
表由**模型**建出来，于是「模型与迁移漂移」永远测不到——2026-09-19 那天，
开发库是 V1.2 的 35 张表、代码是 V1.0，学生开卷子直接 MySQL 1364，而 523 个测试全绿。

现在表由 `alembic upgrade head` 建（`mysql_support.run_migrations`），
所以**模型少一列而迁移没少，测试会当场红**。这是这一层全部的产出。

## 隔离方式：外层事务 + savepoint，不是 truncate

服务层到处 `db.commit()`，所以「用例结束时把写入撤销掉」不能靠拦截 commit。
SQLAlchemy 2.0 的 `join_transaction_mode="create_savepoint"` 正是为这个场景准备的：
session 把自己的 commit 变成**释放一个 savepoint**，而外面那层
`transaction.rollback()` 在用例结束时把整场写入一次撤销——
不需要 truncate，也不需要知道有哪些表。

于是语义与改动前**逐字相同**：每个用例拿到一份刚 `seed_development_data` 过的库，
用例之间互不可见。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.seed import seed_development_data
from app.db.session import get_db
from app.main import create_app
from app.models import *  # noqa: F401,F403
from app.tests.mysql_support import (
    engine_for,
    ensure_database,
    resolve_test_database_url,
    run_migrations,
)


@pytest.fixture(scope="session")
def test_engine():
    """整个 session 共用一台引擎，库**从零建**。

    先 `DROP DATABASE` 再建、再 `alembic upgrade head`：刻意不复用上一次留下的库，
    因为「迁移链能不能从零建出这套结构」这个问题的答案一旦被缓存下来，
    它就再也不会被重新问一遍了。
    """
    url = resolve_test_database_url()
    ensure_database(url)
    run_migrations(url)
    engine = engine_for(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def seeded_engine(test_engine):
    """种子数据**提交一次**，之后每个用例都在它的基础上开 savepoint。

    「每个用例各 `seed_development_data` 一次」这条最直觉的写法在 MySQL 上是错的：
    **`AUTO_INCREMENT` 计数器不参与回滚**。用例 1 把学校插成 `id=1`、结束时回滚，
    用例 2 再 seed 拿到的是 `id=2`——而 `{"scope_type": "SCHOOL", "scope_id": 1}`
    这种硬编码在测试里到处都是（`test_account_admin_api.py` 的 `_create` 默认参数
    就是它）。症状是**该文件第一个用例通过、其余全挂**，看起来像接口坏了。

    提交一次之后种子行的 id 就钉死了（学校永远是 1），而用例自己插入的行仍然
    活在 savepoint 里、结束时被撤销。两边都拿到想要的东西。

    代价：凡是断言「我自己刚插的那一行 id 是多少」的用例会看到漂移——那是正常的，
    `AUTO_INCREMENT` 本来就不保证连续，断言它等于把测试绑死在「没有别人用过这个库」上。
    """
    with Session(test_engine) as session:
        seed_development_data(session)
        session.commit()
    return test_engine


@pytest.fixture()
def db_session(seeded_engine):
    connection = seeded_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()  # 整场用例的写入在这里一次撤销，种子基线原样留下
        connection.close()


@pytest.fixture()
def client(db_session):
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def auth_headers(client: TestClient, role: str, account: str, password: str = "123456") -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"role": role, "account": account, "password": password})
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
