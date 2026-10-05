import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.db import Session
from app.models import DailyTraffic

logger = logging.getLogger(__name__)


async def record_pageview():
    """Aggregate public page loads; never store IPs, cookies or identities."""
    try:
        async with Session() as session, session.begin():
            insert = (
                pg_insert
                if session.bind.dialect.name == "postgresql"
                else sqlite_insert
            )
            statement = insert(DailyTraffic).values(
                day=datetime.now(ZoneInfo("America/Guatemala")).date(), views=1
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[DailyTraffic.day],
                    set_={"views": DailyTraffic.views + 1},
                )
            )
    except (SQLAlchemyError, OSError):
        logger.warning("No se pudo registrar el contador de visitas")
