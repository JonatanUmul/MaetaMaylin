import io
import re

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from test_orders import new_order

from app.config import get_config
from app.models import Order, OrderStatus, Product
from app.security.admin import password_hash
from app.services.orders import change_status


def csrf(html):
    return re.search(r'name="csrf"[^>]*value="([^"]+)"', html).group(1)


@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    for key, value in {
        "DATABASE_URL": "sqlite+aiosqlite://",
        "EVOLUTION_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE": "shop",
        "EVOLUTION_API_KEY": "test-key",
        "ADMIN_WHATSAPP": "50255555555",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": password_hash("test-admin-password"),
        "ADMIN_SESSION_SECRET": "test-session-secret-that-is-long-enough",
        "ADMIN_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_config.cache_clear()
    from app.db import get_session
    from app.main import app

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()
    get_config.cache_clear()


async def login(client):
    page = await client.get("/admin/login")
    response = await client.post(
        "/admin/login",
        data={
            "csrf": csrf(page.text),
            "username": "admin",
            "password": "test-admin-password",
        },
    )
    assert response.status_code == 303


async def test_authentication_and_csrf(client):
    private = await client.get("/admin/orders")
    assert private.status_code == 303
    assert private.headers["location"] == "/admin/login"
    response = await client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": "test-admin-password",
            "csrf": "wrong",
        },
    )
    assert response.status_code == 403
    await login(client)
    page = await client.get("/admin")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert (
        await client.post("/admin/logout", data={"csrf": "wrong"})
    ).status_code == 403
    response = await client.post("/admin/logout", data={"csrf": csrf(page.text)})
    assert response.status_code == 303
    assert (await client.get("/admin/orders")).status_code == 303


async def test_product_edit_archive_and_stale_inventory(client, factory):
    await login(client)
    page = await client.get("/admin/products/1/edit")
    data = {
        "csrf": csrf(page.text),
        "version": "1",
        "title": "Ramo actualizado",
        "description": "Descripción actualizada",
        "price": "30.00",
        "stock": "5",
        "category_id": "1",
        "image_url": "https://example.com/new.jpg",
        "active": "on",
    }
    response = await client.post("/admin/products/1/edit", data=data)
    assert response.status_code == 303
    async with factory() as session:
        product = await session.get(Product, 1)
        assert str(product.price) == "30.00"
        assert product.stock == 3
        version = product.version
    # Reusing an old form must not overwrite a newer inventory value.
    assert (await client.post("/admin/products/1/edit", data=data)).status_code == 409
    listing = await client.get("/admin/products")
    response = await client.post(
        "/admin/products/1/archive",
        data={
            "csrf": csrf(listing.text),
            "version": str(version),
        },
    )
    assert response.status_code == 303
    assert (await client.get("/productos/1")).status_code == 404


async def test_upload_reencodes_photo_and_rejects_non_image(
    client, factory, tmp_path, monkeypatch
):
    from app.services import images

    monkeypatch.setattr(images, "UPLOAD_DIR", tmp_path)
    await login(client)
    page = await client.get("/admin/products/new")
    data = {
        "csrf": csrf(page.text),
        "title": "Producto con foto",
        "description": "Foto real",
        "price": "45.50",
        "stock": "6",
        "category_id": "1",
        "image_url": "",
        "active": "on",
    }
    photo = io.BytesIO()
    Image.new("RGB", (100, 100), "pink").save(photo, "PNG")
    response = await client.post(
        "/admin/products/new",
        data=data,
        files={"photo": ("photo.png", photo.getvalue(), "image/png")},
    )
    assert response.status_code == 303
    async with factory() as session:
        product = await session.scalar(
            select(Product).where(Product.title == data["title"])
        )
        assert product.image_url.startswith("/static/uploads/")
        assert product.image_url.endswith(".jpg")
        with Image.open(tmp_path / product.image_url.split("/")[-1]) as image:
            assert image.format == "JPEG"
    rejected = await client.post(
        "/admin/products/new",
        data=data,
        files={"photo": ("bad.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml")},
    )
    assert rejected.status_code == 422


async def test_order_delivery_workflow_without_inventory(client, factory):
    order = await new_order(factory)
    await login(client)
    assert (await client.get("/admin/orders?q=Ana")).status_code == 200
    for expected, target in [
        ("Pendiente", "Confirmado"),
        ("Confirmado", "Enviado"),
        ("Enviado", "Entregado"),
    ]:
        page = await client.get(f"/admin/orders/{order.id}")
        response = await client.post(
            f"/admin/orders/{order.id}/status",
            data={
                "csrf": csrf(page.text),
                "expected_status": expected,
                "status": target,
            },
        )
        assert response.status_code == 303
    async with factory() as session:
        assert (await session.get(Order, order.id)).status == OrderStatus.DELIVERED
        assert (await session.get(Product, 1)).stock == 3
    page = await client.get(f"/admin/orders/{order.id}")
    rejected = await client.post(
        f"/admin/orders/{order.id}/status",
        data={
            "csrf": csrf(page.text),
            "expected_status": "Entregado",
            "status": "Pendiente",
        },
    )
    assert rejected.status_code == 409


async def test_confirmation_ignores_zero_stock(factory):
    from decimal import Decimal
    from uuid import uuid4

    from app.models import OrderItem

    async with factory() as session, session.begin():
        session.add(
            Product(
                id=2,
                category_id=1,
                title="Segundo producto",
                description="Otro producto",
                image_url="https://example.com/p.jpg",
                price=Decimal("10.00"),
                stock=0,
            )
        )
        order = Order(
            number=1,
            idempotency_key=uuid4(),
            request_hash="x" * 64,
            customer_name="Prueba",
            phone="+50255555555",
            address="Dirección de prueba",
            currency="GTQ",
            total=Decimal("61.00"),
            status=OrderStatus.PENDING,
            items=[
                OrderItem(
                    product_id=1,
                    product_title="Ramo",
                    quantity=2,
                    unit_price=Decimal("25.50"),
                ),
                OrderItem(
                    product_id=2,
                    product_title="Segundo",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                ),
            ],
        )
        session.add(order)
        await session.flush()
        order_id = order.id
    async with factory() as session, session.begin():
        await change_status(session, order_id, OrderStatus.CONFIRMED)
    async with factory() as session:
        assert (await session.get(Product, 1)).stock == 3
        assert (await session.get(Order, order_id)).status == OrderStatus.CONFIRMED
        assert (await session.get(Product, 2)).stock == 0
