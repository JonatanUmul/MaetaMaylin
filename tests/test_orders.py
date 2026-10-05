import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Config
from app.models import (
    NotificationOutbox,
    Order,
    OrderStatus,
    Product,
)
from app.schemas import CheckoutInput
from app.services.evolution import deliver_one
from app.services.orders import change_status, create_order


def checkout_data(quantity=2):
    return CheckoutInput(
        customer_name="Ana Pérez",
        phone="+50255555555",
        address="Calle principal 123, Guatemala",
        items=[{"product_id": 1, "quantity": quantity}],
    )


async def new_order(factory, quantity=2):
    async with factory() as session, session.begin():
        return await create_order(session, checkout_data(quantity), uuid4(), "GTQ")


async def test_quotation_snapshot_and_status_leave_legacy_stock_unchanged(factory):
    order = await new_order(factory)
    assert order.total == Decimal("51.00")
    assert order.status == OrderStatus.PENDING
    async with factory() as session, session.begin():
        product = await session.get(Product, 1)
        assert product.stock == 3
        product.price = Decimal("100.00")
    async with factory() as session, session.begin():
        confirmed = await change_status(session, order.id, OrderStatus.CONFIRMED)
        assert confirmed.items[0].unit_price == Decimal("25.50")
        assert (await session.get(Product, 1)).stock == 3
    async with factory() as session, session.begin():
        await change_status(session, order.id, OrderStatus.CONFIRMED)
        assert (await session.get(Product, 1)).stock == 3
    async with factory() as session, session.begin():
        await change_status(session, order.id, OrderStatus.CANCELLED)
        assert (await session.get(Product, 1)).stock == 3
    async with factory() as session, session.begin():
        await change_status(session, order.id, OrderStatus.CANCELLED)
        assert (await session.get(Product, 1)).stock == 3


async def test_multiple_quotations_confirm_without_stock_limit(factory):
    async with factory() as session, session.begin():
        (await session.get(Product, 1)).stock = 0
    first = await new_order(factory, 10)
    second = await new_order(factory, 20)
    for order in (first, second):
        async with factory() as session, session.begin():
            await change_status(session, order.id, OrderStatus.CONFIRMED)
    async with factory() as session:
        assert (await session.get(Product, 1)).stock == 0
        assert (await session.get(Order, second.id)).status == OrderStatus.CONFIRMED


async def test_rejected_checkout_does_not_persist_order_or_outbox(factory):
    async with factory() as session, session.begin():
        (await session.get(Product, 1)).active = False
    with pytest.raises(HTTPException):
        await new_order(factory, 4)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(NotificationOutbox))
            == 0
        )


def test_strict_inputs():
    data = checkout_data().model_dump()
    data["items"][0]["quantity"] = "2"
    with pytest.raises(ValidationError):
        CheckoutInput.model_validate(data)
    data = checkout_data().model_dump()
    data["total"] = "0.01"
    with pytest.raises(ValidationError):
        CheckoutInput.model_validate(data)
    data = checkout_data().model_dump()
    data["items"] *= 2
    with pytest.raises(ValidationError):
        CheckoutInput.model_validate(data)


@pytest.mark.parametrize("status_code", [201, 503, 401])
async def test_notification_success_retry_and_permanent_failure(factory, status_code):
    order = await new_order(factory)
    config = Config(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
        evolution_enabled=True,
        evolution_url="https://evolution.example.com",
        evolution_instance="shop",
        evolution_api_key="test-secret",
        admin_whatsapp="50255555555",
    )

    def handler(request):
        assert request.url.path == "/message/sendText/shop"
        assert request.headers["apikey"] == "test-secret"
        body = json.loads(request.content)
        assert body["number"] == "50255555555"
        assert "*Folio: MM000001*" in body["text"]
        assert "51.00" not in body["text"]
        assert "*PRODUCTOS SOLICITADOS*" in body["text"]
        return httpx.Response(status_code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await deliver_one(factory, config, client, order.id)
        assert not await deliver_one(factory, config, client, order.id)
    async with factory() as session:
        entry = await session.scalar(select(NotificationOutbox))
        assert entry.attempts == 1
        assert (entry.sent_at is not None) == (status_code == 201)
        assert (entry.failed_at is not None) == (status_code == 401)
        assert (await session.get(Order, order.id)).status == OrderStatus.PENDING


async def test_checkout_route_idempotency(factory, monkeypatch):
    # Configure imports without requiring production credentials or PostgreSQL.
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("EVOLUTION_URL", "https://evolution.example.com")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "shop")
    monkeypatch.setenv("EVOLUTION_API_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_WHATSAPP", "50255555555")
    from app.config import get_config

    get_config.cache_clear()
    from app.api import checkout as endpoint
    from app.db import get_session
    from app.main import app

    async def session_override():
        async with factory() as session:
            yield session

    calls = []

    async def fake_notify(order_id):
        calls.append(order_id)

    monkeypatch.setattr(endpoint, "notify_order", fake_notify)
    app.dependency_overrides[get_session] = session_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            headers = {"Idempotency-Key": str(uuid4())}
            data = checkout_data().model_dump(mode="json")
            first = await client.post("/api/checkout", json=data, headers=headers)
            replay = await client.post("/api/checkout", json=data, headers=headers)
            assert first.status_code == replay.status_code == 201
            assert first.json() == replay.json()
            assert set(first.json()) == {"id", "status", "reference", "message"}
            assert "cotizar" in first.json()["message"]
            data["notes"] = "Cambió el pedido"
            conflict = await client.post("/api/checkout", json=data, headers=headers)
            assert conflict.status_code == 409
            assert len(calls) == 1
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Order)) == 1
            assert (
                await session.scalar(
                    select(func.count()).select_from(NotificationOutbox)
                )
                == 1
            )
    finally:
        app.dependency_overrides.clear()
        get_config.cache_clear()


async def test_storefront_filters_and_public_product_visibility(factory):
    from app.db import get_session
    from app.main import app

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200
            assert "Ramo" in page.text
            assert "/static/css/store.css" in page.text
            filtered = await client.get("/", params={"q": "inexistente"})
            assert filtered.status_code == 200
            assert "¿No encuentras lo que buscas?" in filtered.text
            assert (await client.get("/productos/1")).status_code == 200
            assert (await client.get("/carrito")).status_code == 200
            assert (await client.get("/checkout")).status_code == 200
            data = (await client.get("/api/catalog/products?ids=1")).json()
            assert "price" not in data[0]
            assert "stock" not in data[0]
            for path in ("/", "/productos/1", "/carrito", "/checkout"):
                public = await client.get(path)
                assert "25.50" not in public.text
                assert "Precio máximo" not in public.text
            async with factory() as session, session.begin():
                product = await session.get(Product, 1)
                product.active = False
            assert (await client.get("/productos/1")).status_code == 404
            assert (await client.get("/api/catalog/products?ids=1")).json() == []
    finally:
        app.dependency_overrides.clear()


async def test_short_references_increase_and_notification_has_readable_blocks(factory):
    from app.services.evolution import order_payload, whatsapp_text

    first = await new_order(factory)
    second = await new_order(factory)
    assert first.reference == "MM000001"
    assert second.reference == "MM000002"
    message = whatsapp_text(order_payload(second))
    assert "*Folio: MM000002*\n\n👤" in message
    assert "1. Ramo\n   Cantidad: 2\n\n" in message
    assert "Sin notas adicionales." in message
    assert str(second.id) not in message
    assert "GTQ" not in message
    assert "https://wa.me/50255555555" in message
