import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import seed_development_data
from app.db.session import get_db
from app.main import create_app
from app.models import *  # noqa: F401,F403


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with TestingSessionLocal() as session:
        seed_development_data(session)
        session.commit()
        yield session


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

