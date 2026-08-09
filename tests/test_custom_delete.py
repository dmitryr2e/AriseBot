from bot import db, game
from bot.handlers.custom import cb_delete_custom


class FakeMessage:
    async def answer(self, text, **kwargs):
        self.text = text


class FakeCallback:
    def __init__(self, cq_id):
        self.data = f"cqdel:{cq_id}"
        self.from_user = type("U", (), {"id": 1})()
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


async def test_delete_custom_removes_unfinished_today_copy(user):
    await game.ensure_today(user)
    await db.add_custom_quest(1, "Удаляемый квест", "strength")
    custom = (await db.custom_quests(1))[0]
    today = game.today_str(await db.get_user(1))
    await db.insert_quests([(1, custom["title"], custom["stat"], 30, today, 1)])

    await cb_delete_custom(FakeCallback(custom["id"]))

    assert await db.custom_quests(1) == []
    assert [q for q in await db.quests_for_date(1, today) if q["title"] == "Удаляемый квест"] == []
