import sys
from pathlib import Path

# Ensure root workspace is on python sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.db.models import Base
from src.main import app
from src.db.session import get_db

TEST_DB_URL = "sqlite+aiosqlite:///./data/test.db"

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    Path("./data").mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    test_db_file = Path("./data/test.db")
    if test_db_file.exists():
        test_db_file.unlink(missing_ok=True)

@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine):
    """Provides an isolated async session that clears data after test."""
    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        # Truncate / clean tables for isolation
        async with test_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())

@pytest_asyncio.fixture(scope="function")
async def async_client(db_session):
    """Provides an HTTPX AsyncClient configured with test database session overrides."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
