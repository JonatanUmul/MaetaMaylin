from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models import DailyTraffic
from app.services import traffic
from app.services.social import SocialSettings


def test_social_links_validate_destinations():
    assert (
        SocialSettings(whatsapp="50253234824").links()["WhatsApp"]
        == "https://wa.me/50253234824"
    )
    for data in [
        {"whatsapp": "502 53234824"},
        {"facebook": "https://evil.example/facebook.com"},
        {"instagram": "javascript:alert(1)"},
    ]:
        with pytest.raises(ValidationError):
            SocialSettings(**data)


@pytest.mark.asyncio
async def test_daily_counter_increments_without_identifying_visitors(
    factory, monkeypatch
):
    monkeypatch.setattr(traffic, "Session", factory)
    await traffic.record_pageview()
    await traffic.record_pageview()
    async with factory() as session:
        row = await session.scalar(select(DailyTraffic))
        assert row.views == 2
        assert row.day == datetime.now(ZoneInfo("America/Guatemala")).date()
