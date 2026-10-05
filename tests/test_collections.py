from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import Category, Product


@pytest.mark.asyncio
async def test_collection_filter_and_contact_keep_products_separate(factory):
    async with factory() as session, session.begin():
        flower = await session.scalar(select(Category))
        flower.slug = "limpiapipas"
        teaching = Category(name="Material didáctico", slug="material-didactico")
        session.add(teaching)
        await session.flush()
        session.add(
            Product(
                title="Tarjetas educativas",
                description="Material para aprender",
                price=Decimal(35),
                stock=3,
                image_url="https://example.com/photo.jpg",
                category_id=teaching.id,
            )
        )

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            teaching = await client.get("/", params={"section": "teaching"})
            assert teaching.status_code == 200
            assert "Tarjetas educativas" in teaching.text
            assert 'data-add="1"' not in teaching.text
            flowers = await client.get("/", params={"section": "flowers"})
            assert 'data-add="1"' in flowers.text
            assert "Tarjetas educativas" not in flowers.text
            assert 'id="personalizado"' in flowers.text
            assert (
                await client.get("/", params={"section": "invalid"})
            ).status_code == 422
    finally:
        app.dependency_overrides.clear()
