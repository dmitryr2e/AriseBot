import asyncio

from bot import db, game


async def test_rollover_does_not_overwrite_concurrent_xp(user, set_prev_day):
    await game.ensure_today(user)
    await set_prev_day(1, done=True, xp=10, level=1, hp=100)
    fresh = await db.get_user(1)

    await asyncio.gather(
        game.ensure_today(fresh),
        game.grant_xp(fresh, 50, count_quest=False),
    )

    current = await db.get_user(1)
    assert current["last_daily_date"] == game.today_str(current)
    assert current["xp"] == 60
