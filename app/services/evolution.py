import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Config
from app.models import NotificationOutbox, Order

logger = logging.getLogger(__name__)


def order_payload(order: Order) -> dict:
    return {
        "order_id": str(order.id),
        "reference": order.reference,
        "status": order.status.value,
        "customer": {
            "name": order.customer_name,
            "phone": order.phone,
            "address": order.address,
            "notes": order.notes,
        },
        "items": [
            {
                "product_id": item.product_id,
                "title": item.product_title,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
            }
            for item in order.items
        ],
        "total": str(order.total),
        "currency": order.currency,
    }


def whatsapp_text(payload: dict) -> str:
    customer = payload["customer"]
    reference = payload.get("reference", "Solicitud anterior")
    lines = [
        "🌷 *MAETA MAILYN*",
        "*Nueva solicitud de cotización*",
        f"📋 *Folio: {reference}*",
        "",
        "👤 *DATOS DEL CLIENTE*",
        f"Nombre: {customer['name']}",
        f"WhatsApp: {customer['phone']}",
        "",
        "📍 *DIRECCIÓN DE ENTREGA*",
        customer["address"],
        "",
        "🎁 *PRODUCTOS SOLICITADOS*",
    ]
    for index, item in enumerate(payload["items"], start=1):
        lines.extend(
            [
                f"{index}. {item['title']}",
                f"   Cantidad: {item['quantity']}",
                "",
            ]
        )
    lines.extend(
        [
            "📝 *NOTAS Y PERSONALIZACIÓN*",
            customer.get("notes") or "Sin notas adicionales.",
            "",
            "🕒 *Estado: Pendiente de cotización*",
            "Trabajo por encargo. Contacta al cliente para acordar precio y tiempos de elaboración.",
            "",
            "💬 *Responder al cliente*",
            f"https://wa.me/{customer['phone'].lstrip('+')}",
        ]
    )
    return "\n".join(lines)


async def deliver_one(
    factory: async_sessionmaker,
    config: Config,
    client: httpx.AsyncClient,
    order_id: UUID | None = None,
) -> bool:
    if not config.evolution_enabled:
        return False
    # Lock remains held until HTTP completes; short timeout bounds lock duration.
    # Multiple workers may process different events, never the same locked row.
    async with factory() as session, session.begin():
        query = (
            select(NotificationOutbox)
            .where(
                NotificationOutbox.sent_at.is_(None),
                NotificationOutbox.failed_at.is_(None),
                NotificationOutbox.available_at <= datetime.now(UTC),
            )
            .order_by(NotificationOutbox.available_at)
            .limit(1)
        )
        if order_id is not None:
            query = query.where(NotificationOutbox.order_id == order_id)
        event = await session.scalar(query.with_for_update(skip_locked=True))
        if event is None:
            return False
        event.attempts += 1
        url = (
            f"{str(config.evolution_url).rstrip('/')}"
            f"/message/sendText/{config.evolution_instance}"
        )

        try:
            response = await client.post(
                url,
                headers={"apikey": config.evolution_api_key.get_secret_value()},
                json={
                    "number": config.admin_whatsapp,
                    "text": whatsapp_text(event.payload),
                },
                timeout=httpx.Timeout(10, connect=3),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            # Never log customer data, response bodies, URLs or API credentials.
            event.last_error = type(error).__name__
            now = datetime.now(UTC)
            permanent = isinstance(error, httpx.HTTPStatusError) and (
                400 <= error.response.status_code < 500
                and error.response.status_code not in {408, 429}
            )
            if permanent or event.attempts >= 8:
                event.failed_at = now
                logger.error("Notification requires review: event=%s", event.id)
            else:
                event.available_at = now + timedelta(
                    seconds=min(3600, 2**event.attempts),
                )
        else:
            event.sent_at = datetime.now(UTC)
            event.last_error = None
        return True


async def notify_order(order_id: UUID) -> None:
    from app.config import get_config
    from app.db import Session

    try:
        async with httpx.AsyncClient() as client:
            await deliver_one(Session, get_config(), client, order_id)
    except SQLAlchemyError:
        logger.error("Immediate notification deferred to worker: order=%s", order_id)


async def run_worker() -> None:
    from app.config import get_config
    from app.db import Session

    async with httpx.AsyncClient() as client:
        while True:
            try:
                handled = await deliver_one(Session, get_config(), client)
            except SQLAlchemyError:
                logger.error("Outbox worker database/processing failure")
                handled = False
            if not handled:
                await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
