from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.db import get_session
from app.models import Order
from app.schemas import CheckoutInput, OrderReceipt
from app.services.evolution import notify_order
from app.services.orders import create_order, fingerprint

router = APIRouter(prefix="/api", tags=["Cliente"])


def check_replay(order: Order, data: CheckoutInput) -> Order:
    if order.request_hash != fingerprint(data):
        raise HTTPException(409, "Clave de idempotencia utilizada con otros datos")
    return order


@router.post("/checkout", response_model=OrderReceipt, status_code=201)
async def checkout(
    data: CheckoutInput,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderReceipt:
    try:
        async with session.begin():
            existing = await session.scalar(
                select(Order).where(Order.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return OrderReceipt.model_validate(check_replay(existing, data))
            order = await create_order(
                session,
                data,
                idempotency_key,
                get_config().currency,
            )
    except IntegrityError:
        # A simultaneous request may have committed the same key first.
        async with session.begin():
            existing = await session.scalar(
                select(Order).where(Order.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return OrderReceipt.model_validate(check_replay(existing, data))
    # Only schedule after COMMIT. Use fresh sessions, never request-scoped objects.
    background_tasks.add_task(notify_order, order.id)
    return OrderReceipt.model_validate(order)
