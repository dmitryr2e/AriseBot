"""Фоновые задачи: напоминания, смена дня, недельный отчёт, win-back, угрозы серии."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import config, db, game, keyboards, monitoring, render, share, texts, timeutil
from bot.safehtml import display_name

log = logging.getLogger(__name__)

# Глобальный троттлер массовых отправок: ~20 msg/sec (лимит Telegram — 30).
_SEND_INTERVAL = 0.05
_send_lock = asyncio.Lock()


async def _safe_send(
    bot: Bot,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Отправка с троттлингом и обработкой 429 (TelegramRetryAfter).

    `reply_markup=None` — это отправка без клавиатуры, поэтому апселл можно
    передавать напрямую результатом `keyboards.upsell()`, без ветвлений.
    """
    async with _send_lock:
        for attempt in range(2):
            try:
                await bot.send_message(user_id, text, reply_markup=reply_markup)
                break
            except TelegramRetryAfter as exc:
                if attempt == 0:
                    log.warning("429 от Telegram, ждём %s с", exc.retry_after)
                    await asyncio.sleep(exc.retry_after)
                else:
                    log.warning("Повторный 429, пропускаем сообщение %s", user_id)
            except Exception as exc:  # заблокировал бота и т.п.
                log.warning("Не удалось отправить сообщение %s: %s", user_id, exc)
                break
        await asyncio.sleep(_SEND_INTERVAL)


async def send_reminders(bot: Bot) -> None:
    """Каждую минуту: напоминания тем, у кого наступило их локальное время.

    Обход идёт по поясам, а не по пользователям: `reminder_time` — это время
    на часах охотника, поэтому «20:00» для Москвы и для Владивостока
    наступает в разные моменты. Локальные HH:MM и дата считаются один раз на
    пояс, дальше фильтрует SQL.
    """
    for tz_name in await db.distinct_timezones():
        local = timeutil.now_in(tz_name)
        hhmm = local.strftime("%H:%M")
        date = local.strftime("%Y-%m-%d")
        # Один запрос вместо «выбрать пользователей → quests_for_date на каждого»:
        # джоб крутится каждую минуту, N+1 здесь обходился дороже всего.
        for user in await db.users_with_reminder_progress(hhmm, date, tz_name):
            total = user["quests_total"]
            pending = total - user["quests_done"]
            if total and pending == 0:
                text = texts.REMINDER_ALL_DONE.format(streak=user["streak"])
            else:
                text = texts.REMINDER.format(pending=pending or "—", streak=user["streak"])
            await _safe_send(bot, user["user_id"], text)


async def daily_rollover(bot: Bot) -> None:
    """Смена дня для тех, у кого локальная полночь уже прошла.

    Раньше джоб запускался раз в сутки в 00:05 по серверу и обрабатывал всю
    базу — то есть закрывал день охотнику из Владивостока в 19:05 его времени
    (AUDIT 7.1). Теперь он крутится каждые 15 минут и берёт только тех, чей
    локальный день действительно сменился (`last_daily_date != локальная дата`).

    Выборка самоочищается: если день уже закрыл хендлер, пользователь в неё не
    попадёт, поэтому частый запуск не создаёт двойной работы. Ошибка на одном
    пользователе не должна ронять весь джоб.
    """
    processed = failed = 0
    for tz_name in await db.distinct_timezones():
        local_date = timeutil.today_in(tz_name)
        for user in await db.users_needing_rollover(tz_name, local_date):
            try:
                await _rollover_user(bot, user)
                processed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                log.exception("daily_rollover: ошибка на пользователе %s", user["user_id"])
    if processed or failed:
        log.info("daily_rollover: обработано %s, ошибок %s", processed, failed)
    await monitoring.note_rollover(processed, failed)


async def _rollover_user(bot: Bot, user) -> None:
    events = await game.ensure_today(user)
    # Единый рендер (bot/render.py) — тот же, что используют хендлеры.
    # Раньше здесь была своя копия логики, в которой отсутствовал
    # STREAK_FROZEN: заряд заморозки списывался молча.
    for msg in render.render_day_messages(events):
        await _safe_send(
            bot,
            user["user_id"],
            msg.text,
            keyboards.message_markup(
                upsell_key=msg.upsell,
                share_for=(user["user_id"], msg.share) if msg.share else None,
            ),
        )


async def distribute_boss_rewards(bot: Bot) -> None:
    """Награды за побеждённого босса недели.

    Вызывается сразу после добивания (см. spawn_boss_rewards) и повторно перед
    недельным отчётом как страховка. Идемпотентна: право на выдачу занимает
    атомарно через db.claim_boss_rewarded, поэтому второй вызов выйдет молча.
    """
    from bot import boss as boss_mod

    boss = await db.get_boss(boss_mod.week_key())
    if boss is None or not boss["defeated"] or boss["rewarded"]:
        return
    # Флаг занимаем ДО начисления: два таска (мгновенный и воскресный) могли
    # добраться сюда одновременно, а двойной XP уже не откатить.
    if not await db.claim_boss_rewarded(boss["id"]):
        return
    top = await db.boss_top_damagers(boss["id"], limit=3)
    top_ids = {row["user_id"] for row in top}
    participants = await db.boss_participants(boss["id"])
    rewarded = 0
    for row in participants:
        try:
            user = await db.get_user(row["user_id"])
            if user is None:
                continue
            is_top = row["user_id"] in top_ids
            reward = config.BOSS_REWARD_XP + (config.BOSS_TOP_REWARD_XP if is_top else 0)
            await game.grant_xp(user, reward, count_quest=False)
            # Это же сообщение и есть broadcast «босс повержен»: раньше о смерти
            # босса узнавал только добивший (AUDIT 1.6), остальные — в воскресенье.
            await _safe_send(
                bot,
                row["user_id"],
                texts.BOSS_REWARD.format(
                    name=boss["name"],
                    reward=reward,
                    damage=row["damage"],
                    bonus=texts.BOSS_REWARD_TOP_BONUS if is_top else "",
                ),
            )
            rewarded += 1
        except Exception:  # noqa: BLE001
            log.exception("distribute_boss_rewards: ошибка на пользователе %s", row["user_id"])
    log.info(
        "Награды за босса %s выданы: %s из %s участников",
        boss["name"],
        rewarded,
        len(participants),
    )


# Ссылки на живые таски по неделе босса. Нужны сразу для двух целей:
# (1) без сильной ссылки сборщик мусора может убить fire-and-forget задачу
# до её завершения — asyncio держит только слабые ссылки;
# (2) дедуп: пока выдача по этому боссу идёт, второй таск не создаётся.
_reward_tasks: dict[str, asyncio.Task] = {}


def spawn_boss_rewards(bot: Bot, week_key: str | None = None) -> asyncio.Task:
    """Раздать награды за босса в фоне, не блокируя хендлер добившего.

    Рассылка идёт по всем участникам рейда с троттлингом, поэтому ждать её
    в ответе на сообщение нельзя — пользователь смотрел бы на «печатает…».

    Повторный вызов по тому же боссу возвращает уже запущенный таск. Это не
    замена идемпотентности (её обеспечивает db.claim_boss_rewarded), а защита
    от лишних задач: добить босса могли почти одновременно из нескольких
    хендлеров, и каждая задача иначе прошла бы всю рассылку впустую.
    """
    from bot import boss as boss_mod

    key = week_key or boss_mod.week_key()
    running = _reward_tasks.get(key)
    if running is not None and not running.done():
        return running

    task = asyncio.create_task(distribute_boss_rewards(bot))
    _reward_tasks[key] = task
    # Снимаем только свой таск: к моменту завершения по этому ключу мог
    # оказаться уже следующий (новая неделя с тем же ключом невозможна, но
    # тесты и ручные вызовы обнуляют словарь).
    task.add_done_callback(
        lambda done: _reward_tasks.pop(key, None) if _reward_tasks.get(key) is done else None
    )
    return task


async def weekly_report(bot: Bot) -> None:
    """Раз в неделю: награды за босса, отчёт о прогрессе и сброс недельных счётчиков."""
    await distribute_boss_rewards(bot)
    top = await db.weekly_top(limit=3)
    top_lines = ""
    if top:
        medals = ["🥇", "🥈", "🥉"]
        # display_name, а не сырое поле: этот блок уходит КАЖДОМУ охотнику в
        # базе, поэтому непроэкранированное имя из топа — самый широкий
        # радиус поражения из всех мест, где мы подставляем чужие строки.
        rows = [
            f"{medals[i]} {display_name(row)} — {row['weekly_xp']} XP"
            for i, row in enumerate(top)
        ]
        top_lines = "\n\n<b>Топ охотников недели:</b>\n" + "\n".join(rows)
    for user in await db.all_users():
        try:
            done = user["weekly_done"]
            if done >= 15:
                verdict = texts.WEEKLY_VERDICT_GOOD
            elif done >= 6:
                verdict = texts.WEEKLY_VERDICT_MID
            else:
                verdict = texts.WEEKLY_VERDICT_BAD
            await _safe_send(
                bot,
                user["user_id"],
                texts.WEEKLY_REPORT.format(
                    done=done,
                    xp=user["weekly_xp"],
                    level=user["level"],
                    rank=config.rank_for_level(user["level"]),
                    streak=user["streak"],
                    verdict=verdict,
                )
                + top_lines,
            )
            await db.update_user(user["user_id"], weekly_xp=0, weekly_done=0)
        except Exception:  # noqa: BLE001
            log.exception("weekly_report: ошибка на пользователе %s", user["user_id"])


async def streak_danger(bot: Bot) -> None:
    """Вечером по местному времени: предупредить тех, у кого серия под угрозой.

    Джоб тикает каждые 15 минут, а рассылается только тем поясам, где сейчас
    начался час DEADLINE_HOUR. Окно `minute < 15` даёт ровно одно попадание на
    пояс в сутки, включая полу- и четвертьчасовые смещения (Индия +5:30,
    Непал +5:45): их локальные минуты сдвинуты, но всё равно проходят через 0.
    """
    for tz_name in await db.distinct_timezones():
        local = timeutil.now_in(tz_name)
        if local.hour != config.DEADLINE_HOUR or local.minute >= 15:
            continue
        today = local.strftime("%Y-%m-%d")
        # Фильтр по серии и по наличию незакрытых квестов ушёл в SQL: раньше
        # тянулась вся база, а квесты запрашивались отдельным запросом на каждого.
        for user in await db.users_in_streak_danger(
            today, config.DEADLINE_MIN_STREAK, tz_name
        ):
            try:
                pending = user["quests_total"] - user["quests_done"]
                # Заморозку предлагаем только тем, у кого зарядов нет:
                # у остальных серия и так защищена, оффер был бы лишним.
                offer = None if user["streak_freezes"] else config.UPSELL_FREEZE
                await _safe_send(
                    bot,
                    user["user_id"],
                    texts.STREAK_DANGER.format(pending=pending, streak=user["streak"]),
                    keyboards.upsell(offer),
                )
            except Exception:  # noqa: BLE001
                log.exception("streak_danger: ошибка на пользователе %s", user["user_id"])


async def winback(bot: Bot) -> None:
    """Днём: вернуть дезертиров — не появлявшихся WINBACK_AFTER_DAYS дней.

    Считается по серверному поясу осознанно: порог измеряется целыми сутками
    тишины, и сдвиг границы дня на несколько часов ничего не меняет. Пояс
    сервера здесь влияет лишь на то, в какой момент охотник получит письмо.
    """
    cutoff = (
        datetime.now(config.TZ) - timedelta(days=config.WINBACK_AFTER_DAYS)
    ).strftime("%Y-%m-%d")
    today = datetime.now(config.TZ).date()
    for user in await db.inactive_users(cutoff):
        try:
            # Флаг занимаем ДО отправки. Иначе падение джоба посередине
            # приводило к повторной рассылке всем, кому уже написали.
            if not await db.claim_winback(user["user_id"]):
                continue
            try:
                days = (today - datetime.strptime(user["last_seen"], "%Y-%m-%d").date()).days
            except ValueError:
                days = config.WINBACK_AFTER_DAYS
            await _safe_send(
                bot,
                user["user_id"],
                texts.WINBACK.format(
                    days=days,
                    bonus=config.WINBACK_BONUS_XP,
                    level=user["level"],
                    rank=config.rank_for_level(user["level"]),
                ),
            )
        except Exception:  # noqa: BLE001
            log.exception("winback: ошибка на пользователе %s", user["user_id"])


async def boss_low_hp_alert(bot: Bot) -> None:
    """Ежечасно: если босс при смерти — общий призыв добить (один раз за неделю)."""
    from bot import boss as boss_mod

    boss = await db.get_boss(boss_mod.week_key())
    if (
        boss is None
        or boss["defeated"]
        or boss["low_hp_notified"]
        or boss["hp"] > boss["max_hp"] * config.BOSS_LOW_HP_RATIO
    ):
        return
    await db.mark_boss_low_hp_notified(boss["id"])
    pct = max(1, round(boss["hp"] * 100 / boss["max_hp"]))
    text = texts.BOSS_LOW_HP.format(name=boss["name"], hp=boss["hp"], pct=pct)
    for user in await db.all_users():
        await _safe_send(bot, user["user_id"], text)


_ONBOARDING_STEPS = (1, 2, 3)


def _days_since(local_created: str, local_today: str) -> int:
    """Календарных дней между локальной датой регистрации и «сегодня».

    Битое/пустое значение или дата в будущем (сбой часов) дают отрицательное
    число — тогда шаг просто не наступил, а не отправляется досрочно.
    """
    try:
        return (date.fromisoformat(local_today) - date.fromisoformat(local_created)).days
    except ValueError:
        return -1


def _onboarding_text(step: int, user) -> str:
    if step == 1:
        return texts.ONBOARDING_DAY1_REPORT_HINT
    if step == 2:
        return texts.ONBOARDING_DAY2_BOSS_RATING
    return texts.ONBOARDING_DAY3_REFERRAL.format(
        link=share.ref_link(user["user_id"]),
        bonus=config.REF_BONUS_XP,
        threshold=config.REF_PREMIUM_THRESHOLD,
        days=config.REF_PREMIUM_DAYS,
    )


async def onboarding_chain(bot: Bot) -> None:
    """Дни 1-3 после регистрации: /report, босс+рейтинг, рефералка.

    Шаблон — db.claim_winback: право на шаг занимается атомарно ДО отправки,
    поэтому падение джоба посередине не даёт повторной рассылки. Отписка
    (onboarding_day = config.ONBOARDING_STOP) естественно выпадает из
    выборки — -1 не совпадает ни с одним ожидаемым «step - 1».

    «День N» считается в поясе охотника, а не сервера (created_at хранится в
    UTC) — иначе подсказка могла прийти ночью, как было с дедлайном серии до
    появления per-TZ дня (AUDIT 7.1).

    Один прогон продвигает каждого охотника максимум на один шаг: `handled`
    защищает от того, чтобы охотник, не появлявшийся неделю после регистрации,
    не получил все три подсказки разом в первый же прогон джоба — шаги должны
    идти по порядку, с интервалом в реальные сутки между ними.
    """
    handled: set[int] = set()
    for step in _ONBOARDING_STEPS:
        for tz_name in await db.distinct_timezones():
            local_today = timeutil.today_in(tz_name)
            for user in await db.users_pending_onboarding(step, tz_name):
                if user["user_id"] in handled:
                    continue
                created_local = timeutil.local_date_of(user["created_at"], tz_name)
                if not created_local or _days_since(created_local, local_today) < step:
                    continue
                if not await db.claim_onboarding_step(user["user_id"], step):
                    continue
                handled.add(user["user_id"])
                try:
                    await _safe_send(
                        bot,
                        user["user_id"],
                        _onboarding_text(step, user),
                        keyboards.onboarding_stop(),
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "onboarding_chain: ошибка на пользователе %s (шаг %s)",
                        user["user_id"],
                        step,
                    )


async def backup_db() -> None:
    """Корректный бэкап SQLite в WAL-режиме через VACUUM INTO + ретеншн."""
    from bot import db as db_mod

    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(config.TZ).strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"hunter_{stamp}.db"
    try:
        await db_mod.db().execute("VACUUM INTO ?", (str(dest),))
        log.info("Бэкап БД создан: %s", dest)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка создания бэкапа БД")
        return
    # Ретеншн: оставляем последние BACKUP_KEEP копий
    backups = sorted(config.BACKUP_DIR.glob("hunter_*.db"))
    for old in backups[: -config.BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TZ)
    scheduler.add_job(monitoring.job(send_reminders), "cron", minute="*", args=[bot])
    scheduler.add_job(monitoring.job(backup_db), "interval", hours=config.BACKUP_INTERVAL_HOURS)
    # Оба джоба теперь работают по локальному времени охотника и сами
    # выбирают, чей пояс дозрел, поэтому тикают чаще. Шаг 15 минут выбран под
    # четвертьчасовые смещения поясов — при шаге в час их бы сносило.
    scheduler.add_job(monitoring.job(daily_rollover), "cron", minute="5,20,35,50", args=[bot])
    scheduler.add_job(monitoring.job(streak_danger), "cron", minute="0,15,30,45", args=[bot])
    scheduler.add_job(monitoring.job(winback), "cron", hour=12, minute=30, args=[bot])
    scheduler.add_job(monitoring.job(boss_low_hp_alert), "cron", minute=15, args=[bot])
    scheduler.add_job(monitoring.job(onboarding_chain), "cron", minute=40, args=[bot])
    scheduler.add_job(
        monitoring.job(weekly_report),
        "cron",
        day_of_week=config.WEEKLY_REPORT_DAY,
        hour=config.WEEKLY_REPORT_HOUR,
        args=[bot],
    )
    return scheduler
