"""Слой БД (идемпотентность, право на забвение) и чистый рендер сообщений."""
from bot import config, db, game, render, texts

# ---------- win-back: флаг занимается атомарно ----------

async def test_claim_winback_succeeds_once(user):
    assert await db.claim_winback(1) is True
    assert await db.claim_winback(1) is False, "повторный джоб не должен дублировать рассылку"
    assert (await db.get_user(1))["winback_sent"] == 1


# ---------- платежи: идемпотентность и возвраты ----------

async def test_record_payment_is_idempotent(user):
    assert await db.record_payment("chg_1", 1, "premium_month", 199) is True
    assert await db.record_payment("chg_1", 1, "premium_month", 199) is False, (
        "ретрай апдейта Telegram не должен выдавать товар дважды"
    )
    assert len(await db.user_payments(1)) == 1


async def test_mark_payment_refunded_once(user):
    await db.record_payment("chg_2", 1, "premium_month", 199)

    assert await db.mark_payment_refunded("chg_2") is True
    assert await db.mark_payment_refunded("chg_2") is False
    assert (await db.get_payment("chg_2"))["refunded"] == 1


# ---------- анти-инъекция отчётов: дедуп по fingerprint (AUDIT 2.4) ----------

async def test_add_report_rejects_duplicate_text(user):
    assert await db.add_report(1, game.today_str(), "Отжался 50 раз", 40, "ок") is True
    assert await db.add_report(1, game.today_str(), "Отжался 50 раз", 40, "ок") is False, (
        "повторный отчёт с тем же текстом не должен пройти как новый"
    )
    assert await db.count_where("reports", "user_id = ?", (1,)) == 1


async def test_add_report_duplicate_ignores_case_and_whitespace(user):
    assert await db.add_report(1, game.today_str(), "Отжался 50 раз", 40, "ок") is True
    assert await db.add_report(
        1, game.today_str(), "  отжался   50\nРАЗ  ", 40, "ок"
    ) is False, "отличия только в пробелах/регистре — тот же отчёт"


async def test_add_report_same_text_different_users_does_not_conflict(user, conn):
    await db.create_user(2, "tester2", "Тестер2")

    assert await db.add_report(1, game.today_str(), "Отжался 50 раз", 40, "ок") is True
    assert await db.add_report(2, game.today_str(), "Отжался 50 раз", 40, "ок") is True, (
        "одинаковый текст у разных охотников не должен конфликтовать"
    )
    assert await db.count_where("reports", "1=1") == 2


async def test_report_is_duplicate_pre_check(user):
    assert await db.report_is_duplicate(1, "Отжался 50 раз") is False
    await db.add_report(1, game.today_str(), "Отжался 50 раз", 40, "ок")
    assert await db.report_is_duplicate(1, "отжался 50 РАЗ") is True
    assert await db.report_is_duplicate(1, "Пробежал 5 км") is False


# ---------- право на забвение ----------

async def test_delete_user_data_wipes_user_but_anonymizes_payment(user, conn):
    await game.ensure_today(user)
    await db.add_custom_quest(1, "Медитация", "intelligence")
    await db.add_report(1, game.today_str(), "текст отчёта", 10, "ок")
    await db.unlock_achievement(1, "first_blood")
    await db.record_payment("chg_3", 1, "premium_month", 199)

    await db.delete_user_data(1)

    assert await db.get_user(1) is None
    assert await db.quests_for_date(1, game.today_str()) == []
    assert await db.custom_quests(1) == []
    assert await db.user_achievements(1) == set()
    assert await db.count_where("reports", "user_id = ?", (1,)) == 0

    payment = await db.get_payment("chg_3")
    assert payment is not None, "платёж должен остаться для отчётности"
    assert payment["user_id"] == 0, "но быть обезличен"


# ---------- рендер: регресс на потерянный STREAK_FROZEN ----------

def test_render_reports_frozen_streak():
    """В rollover планировщика этого сообщения не было — заряд списывался молча."""
    events = game.DayEvents(
        new_day=True, quests_issued=4, streak_frozen=True, hp=100, max_hp=100
    )

    messages = render.render_day_events(events)

    assert texts.STREAK_FROZEN in messages
    assert any(texts.NEW_DAY.format(count=4) == m for m in messages)


def test_render_absence_block():
    events = game.DayEvents(
        new_day=True,
        quests_issued=4,
        skipped_days=3,
        streak_reset=True,
        damage=config.SKIPPED_DAY_DAMAGE * 3,
        hp=70,
        max_hp=100,
    )

    messages = render.render_day_events(events)

    assert any("3" in m for m in messages), "число пропущенных дней должно попасть в текст"
    assert len(messages) >= 2


def test_render_returns_nothing_when_day_not_new():
    assert render.render_day_events(game.DayEvents(new_day=False)) == []
