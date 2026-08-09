from datetime import datetime, timedelta

from bot import config, db, game


async def test_legacy_premium_flag_does_not_override_empty_expiry(user):
    await db.update_user(1, is_premium=1, premium_until="")

    assert game.is_premium(await db.get_user(1)) is False


async def test_expired_premium_is_inactive_even_if_legacy_flag_is_set(user):
    expired = (datetime.now(config.TZ) - timedelta(days=1)).strftime(game.PREMIUM_UNTIL_FMT)
    await db.update_user(1, is_premium=1, premium_until=expired)

    assert game.is_premium(await db.get_user(1)) is False
