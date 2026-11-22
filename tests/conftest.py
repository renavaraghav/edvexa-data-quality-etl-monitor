import datetime
import sqlite3
import os
import socket
import threading
import time

# Set before importing the app so no operational engine can be created.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "synthetic-pytest-signing-key"
os.environ["ADMIN_ALLOWED_ORIGINS"] = "http://localhost"
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

with patch("dotenv.load_dotenv", return_value=False):
    from app.config import settings
from app.database import Base, get_db, SessionLocal
from app import database
from app.services import storage
from app.main import app
from app.routers import auth as auth_router
from app.services.auth import SESSION_COOKIE_NAME, create_admin_session_token

# Register sqlite3 ISO date/datetime adapters to eliminate Python 3.12 deprecation warnings
sqlite3.register_adapter(datetime.date, lambda val: val.isoformat())
sqlite3.register_adapter(datetime.datetime, lambda val: val.isoformat())

# Configure dedicated test admin secret token for deterministic test suite execution
settings.ADMIN_RESET_TOKEN = "test-secret-token-for-pytest"
settings.ADMIN_LOGIN_PASSWORD = "synthetic-pytest-login-password"
settings.SECRET_KEY = "test-session-secret-key-for-pytest"

# In-memory SQLite database isolated for tests
SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Create a fresh database session for each test function."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient with database dependency overridden to test in-memory DB."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), headers={"Origin": "http://localhost"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def auth_client(client: TestClient) -> TestClient:
    """Provide a TestClient pre-authenticated with a signed admin session cookie."""
    token = create_admin_session_token()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setitem(SessionLocal.kw, "bind", test_engine)
    monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(settings, "SURVEYS_DIR", tmp_path / "surveys")
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "ADMIN_ALLOWED_ORIGINS", [])
    monkeypatch.setattr(auth_router, "_login_attempts", {})
    monkeypatch.setattr(storage, "_storage_instance", None)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def live_test_server(isolated_app, monkeypatch, tmp_path):
    import uvicorn

    # Browser requests run concurrently. Give each request its own SQLite connection.
    browser_engine = create_engine(f"sqlite:///{(tmp_path / 'browser.sqlite').as_posix()}")
    monkeypatch.setattr(database, "engine", browser_engine)
    monkeypatch.setitem(SessionLocal.kw, "bind", browser_engine)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.025)
            assert server.started, "Isolated test server failed to start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            browser_engine.dispose()
