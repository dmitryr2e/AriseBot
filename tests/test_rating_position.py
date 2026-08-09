from bot import db


async def test_weekly_position_excludes_hidden_players(user):
    await db.update_user(1, weekly_xp=100)
    await db.create_user(2, "hidden", "Скрытый")
    await db.update_user(2, weekly_xp=200, hide_in_rating=1)
    await db.create_user(3, "public", "Публичный")
    await db.update_user(3, weekly_xp=50)

    assert await db.weekly_position(1) == (2, 2)
