"""Вербовка: атомарность бонуса и премиум за порог (handlers/common._process_referral)."""
import asyncio
from datetime import datetime, timedelta

import pytest

from bot import config, db, game
from bot.handlers.common import _process_referral

REFERRER = 1
NEWBIE = 2


class FakeTgUser:
    def __init__(self, uid: int, first_name: str = "Новичок"):
        self.id = uid
        self.first_name = first_name
        self.username = ""


class FakeBot:
    """fail=True эмулирует вербовщика, заблокировавшего бота."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[int, str]] = []
        self.fail = fail

    async def send_message(self, user_id, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append((user_id, text))


class FakeMessage:
    def __init__(self, uid: int = NEWBIE, bot: FakeBot | None = None):
        self.from_user = FakeTgUser(uid)
        self.bot = bot or FakeBot()
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append(text)


async def _make_pair() -> None:
    await db.create_user(REFERRER, "boss", "Вербовщик")
    await db.create_user(NEWBIE, "rookie", "Новичок")


def _premium_dt(value: str) -> datetime:
    return datetime.strptime(value, game.PREMIUM_UNTIL_FMT).replace(tzinfo=config.TZ)


async def test_referral_awards_bonus_once(conn):
    await _make_pair()
    message = FakeMessage()

    await _process_referral(message, NEWBIE, REFERRER)

    assert (await db.get_user(NEWBIE))["referred_by"] == REFERRER
    assert (await db.get_user(REFERRER))["ref_count"] == 1
    assert len(message.answers) == 1
    assert len(message.bot.sent) == 1


async def test_second_start_does_not_award_again(conn):
    """Повторный /start по той же ссылке не должен приносить второй бонус."""
    await _make_pair()
    bot = FakeBot()

    await _process_referral(FakeMessage(bot=bot), NEWBIE, REFERRER)
    second = FakeMessage(bot=bot)
    await _process_referral(second, NEWBIE, REFERRER)

    assert (await db.get_user(REFERRER))["ref_count"] == 1
    assert second.answers == []
    assert len(bot.sent) == 1


async def test_concurrent_starts_award_once(conn):
    """Два одновременных /start по одной ссылке: бонус ровно один."""
    await _make_pair()
    bot = FakeBot()
    first, second = FakeMessage(bot=bot), FakeMessage(bot=bot)

    await asyncio.gather(
        _process_referral(first, NEWBIE, REFERRER),
        _process_referral(second, NEWBIE, REFERRER),
    )

    assert (await db.get_user(REFERRER))["ref_count"] == 1
    assert len(first.answers) + len(second.answers) == 1
    assert len(bot.sent) == 1


async def test_self_referral_ignored(conn):
    await _make_pair()
    message = FakeMessage(uid=REFERRER)

    await _process_referral(message, REFERRER, REFERRER)

    referrer = await db.get_user(REFERRER)
    assert referrer["ref_count"] == 0
    assert referrer["referred_by"] == 0
    assert message.answers == []


async def test_unknown_referrer_ignored(conn):
    await _make_pair()
    message = FakeMessage()

    await _process_referral(message, NEWBIE, 999)

    assert (await db.get_user(NEWBIE))["referred_by"] == 0
    assert message.answers == []


async def test_premium_granted_at_threshold(conn):
    await _make_pair()
    await db.update_user(REFERRER, ref_count=config.REF_PREMIUM_THRESHOLD - 1)

    await _process_referral(FakeMessage(), NEWBIE, REFERRER)

    referrer = await db.get_user(REFERRER)
    assert referrer["ref_count"] == config.REF_PREMIUM_THRESHOLD
    assert game.is_premium(referrer)


async def test_premium_granted_even_if_referrer_blocked_bot(conn):
    """Награда не должна теряться из-за упавшей доставки уведомления."""
    await _make_pair()
    await db.update_user(REFERRER, ref_count=config.REF_PREMIUM_THRESHOLD - 1)

    await _process_referral(FakeMessage(bot=FakeBot(fail=True)), NEWBIE, REFERRER)

    assert game.is_premium(await db.get_user(REFERRER))


async def test_premium_extended_not_overwritten(conn):
    """Оплаченный Монарх продлевается наградой, а не укорачивается до недели."""
    await _make_pair()
    paid_until = datetime.now(config.TZ) + timedelta(days=30)
    await db.update_user(
        REFERRER,
        ref_count=config.REF_PREMIUM_THRESHOLD - 1,
        premium_until=paid_until.strftime(game.PREMIUM_UNTIL_FMT),
    )

    await _process_referral(FakeMessage(), NEWBIE, REFERRER)

    new_until = _premium_dt((await db.get_user(REFERRER))["premium_until"])
    assert new_until > paid_until


async def test_no_premium_below_threshold(conn):
    await _make_pair()
    await db.update_user(REFERRER, ref_count=config.REF_PREMIUM_THRESHOLD - 2)

    await _process_referral(FakeMessage(), NEWBIE, REFERRER)

    assert not game.is_premium(await db.get_user(REFERRER))


async def test_increment_and_get_returns_distinct_values(conn):
    """Каждый параллельный инкремент видит собственное значение.

    Именно на этом держится точное сравнение с порогом премиума.
    """
    await db.create_user(REFERRER, "boss", "Вербовщик")

    values = await asyncio.gather(
        *(db.increment_and_get(REFERRER, "ref_count") for _ in range(5))
    )

    assert sorted(values) == [1, 2, 3, 4, 5]
    assert (await db.get_user(REFERRER))["ref_count"] == 5


async def test_increment_and_get_rejects_unknown_column(conn):
    await db.create_user(REFERRER, "boss", "Вербовщик")

    with pytest.raises(ValueError):
        await db.increment_and_get(REFERRER, "ref_count = 0; DROP TABLE users")
