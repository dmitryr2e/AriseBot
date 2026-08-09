from datetime import UTC, datetime, timedelta

from bot import config, db, scheduler
from tests.test_scheduler_queries import FakeBot


async def test_legacy_user_is_not_sent_onboarding(conn, user, monkeypatch):
    created = datetime.now(config.TZ) - timedelta(days=scheduler.ONBOARDING_MAX_AGE_DAYS + 1)
    await db.update_user(1, created_at=created.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    monkeypatch.setattr(scheduler, "_SEND_INTERVAL", 0)
    bot = FakeBot()

    await scheduler.onboarding_chain(bot)

    assert bot.sent == []
    assert (await db.get_user(1))["onboarding_day"] == 0
