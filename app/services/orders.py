import hashlib
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    NotificationOutbox,
    Order,
    OrderItem,
    OrderNumber,
    OrderStatus,
    Product,
)
from app.schemas import CheckoutInput
from app.services.evolution import order_payload


def fingerprint(data: CheckoutInput) -> str:
    return hashlib.sha256(data.model_dump_json().encode()).hexdigest()


async def create_order(
    session: AsyncSession,
    data: CheckoutInput,
    key: UUID,
    currency: str,
) -> Order:
    products = list(
        (
            await session.scalars(
                select(Product)
                .where(Product.id.in_([i.product_id for i in data.items]))
                .order_by(Product.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {product.id: product for product in products}
    number = OrderNumber()
    session.add(number)
    await session.flush()
    order = Order(
        number=number.id,
        idempotency_key=key,
        request_hash=fingerprint(data),
        customer_name=data.customer_name,
        phone=data.phone,
        address=data.address,
        notes=data.notes,
        currency=currency,
        status=OrderStatus.PENDING,
        items=[],
        total=Decimal("0.00"),
    )
    for item in data.items:
        product = by_id.get(item.product_id)
        if product is None or not product.active:
            raise HTTPException(409, "Uno de los productos ya no está disponible")
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_title=product.title,
                unit_price=product.price,
                quantity=item.quantity,
            )
        )
        order.total += product.price * item.quantity
    # Quotations are made to order; legacy inventory fields are not used.
    session.add(order)
    await session.flush()
    session.add(NotificationOutbox(order_id=order.id, payload=order_payload(order)))
    return order


async def change_status(
    session: AsyncSession,
    order_id: UUID,
    target: OrderStatus,
) -> Order:
    """Caller must authenticate admin and wrap this in session.begin()."""
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items))
        .with_for_update()
    )
    if order is None:
        raise HTTPException(404, "Pedido no encontrado")
    if order.status == target:
        return order
    transitions = {
        OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
        OrderStatus.CONFIRMED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
        OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
        OrderStatus.DELIVERED: set(),
        OrderStatus.CANCELLED: set(),
    }
    if target not in transitions[order.status]:
        raise HTTPException(409, "Transición de estado no permitida")
    order.status = target
    await session.flush()
    return order
