from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Category, Product


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(Category(id=1, name="Limpiapipas", slug="limpiapipas"))
        await session.flush()
        session.add(
            Product(
                id=1,
                category_id=1,
                title="Ramo",
                description="Ramo artesanal",
                image_url="https://example.com/ramo.jpg",
                price=Decimal("25.50"),
                stock=3,
            )
        )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def traffic_database(factory, monkeypatch):
    from app.services import traffic

    monkeypatch.setattr(traffic, "Session", factory)
