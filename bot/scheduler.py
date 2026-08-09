"""Фоновые задачи: напоминания, смена дня, недельный отчёт, win-back, угрозы серии."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import config, db, game, keyboards, monitoring, render, share, texts, timeutil
from bot.backup_crypto import encrypt_file
from bot.safehtml import display_name

log = logging.getLogger(__name__)
_SEND_INTERVAL = 0.05
_send_lock = asyncio.Lock()
ONBOARDING_MAX_AGE_DAYS = 7


async def _safe_send(bot: Bot, user_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
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
            except Exception as exc:
                log.warning("Не удалось отправить сообщение %s: %s", user_id, exc)
                break
        await asyncio.sleep(_SEND_INTERVAL)


async def send_reminders(bot: Bot) -> None:
    for tz_name in await db.distinct_timezones():
        local = timeutil.now_in(tz_name)
        hhmm = local.strftime("%H:%M")
        date = local.strftime("%Y-%m-%d")
        for user in await db.users_with_reminder_progress(hhmm, date, tz_name):
            total = user["quests_total"]
            pending = total - user["quests_done"]
            if total and pending == 0:
                text = texts.REMINDER_ALL_DONE.format(streak=user["streak"])
            else:
                text = texts.REMINDER.format(pending=pending or "—", streak=user["streak"])
            await _safe_send(bot, user["user_id"], text)


async def daily_rollover(bot: Bot) -> None:
    processed = failed = 0
    for tz_name in await db.distinct_timezones():
        local_date = timeutil.today_in(tz_name)
        for user in await db.users_needing_rollover(tz_name, local_date):
            try:
                await _rollover_user(bot, user)
                processed += 1
            except Exception:
                failed += 1
                log.exception("daily_rollover: ошибка на пользователе %s", user["user_id"])
    if processed or failed:
        log.info("daily_rollover: обработано %s, ошибок %s", processed, failed)
    await monitoring.note_rollover(processed, failed)


async def _rollover_user(bot: Bot, user) -> None:
    events = await game.ensure_today(user)
    for msg in render.render_day_messages(events):
        await _safe_send(bot, user["user_id"], msg.text, keyboards.message_markup(upsell_key=msg.upsell, share_for=(user["user_id"], msg.share) if msg.share else None))


async def distribute_boss_rewards(bot: Bot) -> None:
    from bot import boss as boss_mod
    boss = await db.get_boss(boss_mod.week_key())
    if boss is None or not boss["defeated"] or boss["rewarded"]:
        return
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
            await _safe_send(bot, row["user_id"], texts.BOSS_REWARD.format(name=boss["name"], reward=reward, damage=row["damage"], bonus=texts.BOSS_REWARD_TOP_BONUS if is_top else ""))
            rewarded += 1
        except Exception:
            log.exception("distribute_boss_rewards: ошибка на пользователе %s", row["user_id"])
    log.info("Награды за босса %s выданы: %s из %s участников", boss["name"], rewarded, len(participants))


_reward_tasks: dict[str, asyncio.Task] = {}


def spawn_boss_rewards(bot: Bot, week_key: str | None = None) -> asyncio.Task:
    from bot import boss as boss_mod
    key = week_key or boss_mod.week_key()
    running = _reward_tasks.get(key)
    if running is not None and not running.done():
        return running
    task = asyncio.create_task(distribute_boss_rewards(bot))
    _reward_tasks[key] = task
    task.add_done_callback(lambda done: _reward_tasks.pop(key, None) if _reward_tasks.get(key) is done else None)
    return task


async def weekly_report(bot: Bot) -> None:
    await distribute_boss_rewards(bot)
    top = await db.weekly_top(limit=3)
    top_lines = ""
    if top:
        medals = ["🥇", "🥈", "🥉"]
        rows = [f"{medals[i]} {display_name(row)} — {row['weekly_xp']} XP" for i, row in enumerate(top)]
        top_lines = "\n\n<b>Топ охотников недели:</b>\n" + "\n".join(rows)
    for user in await db.all_users():
        try:
            done = user["weekly_done"]
            verdict = texts.WEEKLY_VERDICT_GOOD if done >= 15 else texts.WEEKLY_VERDICT_MID if done >= 6 else texts.WEEKLY_VERDICT_BAD
            await _safe_send(bot, user["user_id"], texts.WEEKLY_REPORT.format(done=done, xp=user["weekly_xp"], level=user["level"], rank=config.rank_for_level(user["level"]), streak=user["streak"], verdict=verdict) + top_lines)
            await db.update_user(user["user_id"], weekly_xp=0, weekly_done=0)
        except Exception:
            log.exception("weekly_report: ошибка на пользователе %s", user["user_id"])


async def streak_danger(bot: Bot) -> None:
    for tz_name in await db.distinct_timezones():
        local = timeutil.now_in(tz_name)
        if local.hour != config.DEADLINE_HOUR or local.minute >= 15:
            continue
        today = local.strftime("%Y-%m-%d")
        for user in await db.users_in_streak_danger(today, config.DEADLINE_MIN_STREAK, tz_name):
            try:
                pending = user["quests_total"] - user["quests_done"]
                offer = None if user["streak_freezes"] else config.UPSELL_FREEZE
                await _safe_send(bot, user["user_id"], texts.STREAK_DANGER.format(pending=pending, streak=user["streak"]), keyboards.upsell(offer))
            except Exception:
                log.exception("streak_danger: ошибка на пользователе %s", user["user_id"])


async def winback(bot: Bot) -> None:
    cutoff = (datetime.now(config.TZ) - timedelta(days=config.WINBACK_AFTER_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now(config.TZ).date()
    for user in await db.inactive_users(cutoff):
        try:
            if not await db.claim_winback(user["user_id"]):
                continue
            try:
                days = (today - datetime.strptime(user["last_seen"], "%Y-%m-%d").date()).days
            except ValueError:
                days = config.WINBACK_AFTER_DAYS
            await _safe_send(bot, user["user_id"], texts.WINBACK.format(days=days, bonus=config.WINBACK_BONUS_XP, level=user["level"], rank=config.rank_for_level(user["level"])) )
        except Exception:
            log.exception("winback: ошибка на пользователе %s", user["user_id"])


async def boss_low_hp_alert(bot: Bot) -> None:
    from bot import boss as boss_mod
    boss = await db.get_boss(boss_mod.week_key())
    if boss is None or boss["defeated"] or boss["low_hp_notified"] or boss["hp"] > boss["max_hp"] * config.BOSS_LOW_HP_RATIO:
        return
    await db.mark_boss_low_hp_notified(boss["id"])
    pct = max(1, round(boss["hp"] * 100 / boss["max_hp"]))
    text = texts.BOSS_LOW_HP.format(name=boss["name"], hp=boss["hp"], pct=pct)
    for user in await db.all_users():
        await _safe_send(bot, user["user_id"], text)


_ONBOARDING_STEPS = (1, 2, 3)


def _days_since(local_created: str, local_today: str) -> int:
    try:
        return (date.fromisoformat(local_today) - date.fromisoformat(local_created)).days
    except ValueError:
        return -1


def _onboarding_text(step: int, user) -> str:
    if step == 1:
        return texts.ONBOARDING_DAY1_REPORT_HINT
    if step == 2:
        return texts.ONBOARDING_DAY2_BOSS_RATING
    return texts.ONBOARDING_DAY3_REFERRAL.format(link=share.ref_link(user["user_id"]), bonus=config.REF_BONUS_XP, threshold=config.REF_PREMIUM_THRESHOLD, days=config.REF_PREMIUM_DAYS)


async def onboarding_chain(bot: Bot) -> None:
    handled: set[int] = set()
    for step in _ONBOARDING_STEPS:
        for tz_name in await db.distinct_timezones():
            local_today = timeutil.today_in(tz_name)
            for user in await db.users_pending_onboarding(step, tz_name):
                if user["user_id"] in handled:
                    continue
                created_local = timeutil.local_date_of(user["created_at"], tz_name)
                age = _days_since(created_local, local_today)
                # onboarding_day по умолчанию 0 появился через миграцию, поэтому
                # старые пользователи выглядят как «шагов ещё не было». Окно в 7
                # дней сохраняет догоняние для свежих регистраций, но исключает
                # аккаунты, которым онбординг уже явно устарел.
                if age < step or age > ONBOARDING_MAX_AGE_DAYS:
                    continue
                if not await db.claim_onboarding_step(user["user_id"], step):
                    continue
                handled.add(user["user_id"])
                try:
                    await _safe_send(bot, user["user_id"], _onboarding_text(step, user), keyboards.onboarding_stop())
                except Exception:
                    log.exception("onboarding_chain: ошибка на пользователе %s (шаг %s)", user["user_id"], step)


async def backup_db() -> None:
    from bot import db as db_mod
    if not config.BACKUP_ENCRYPTION_KEY:
        log.error("BACKUP_ENCRYPTION_KEY не задан — бэкап пропущен")
        return
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(config.TZ).strftime("%Y%m%d_%H%M%S")
    plain = config.BACKUP_DIR / f"hunter_{stamp}.db"
    dest = config.BACKUP_DIR / f"hunter_{stamp}.db.enc"
    try:
        await db_mod.db().execute("VACUUM INTO ?", (str(plain),))
    except Exception:
        log.exception("Ошибка создания бэкапа БД")
        plain.unlink(missing_ok=True)
        return
    try:
        encrypt_file(plain, dest, config.BACKUP_ENCRYPTION_KEY)
    except Exception:
        log.exception("Ошибка шифрования бэкапа БД")
        plain.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        return
    log.info("Бэкап БД создан: %s", dest)
    backups = sorted(config.BACKUP_DIR.glob("hunter_*.db.enc"))
    for old in backups[:-config.BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TZ)
    scheduler.add_job(monitoring.job(send_reminders), "cron", minute="*", args=[bot])
    scheduler.add_job(monitoring.job(backup_db), "interval", hours=config.BACKUP_INTERVAL_HOURS)
    scheduler.add_job(monitoring.job(daily_rollover), "cron", minute="5,20,35,50", args=[bot])
    scheduler.add_job(monitoring.job(streak_danger), "cron", minute="0,15,30,45", args=[bot])
    scheduler.add_job(monitoring.job(winback), "cron", hour=12, minute=30, args=[bot])
    scheduler.add_job(monitoring.job(boss_low_hp_alert), "cron", minute=15, args=[bot])
    scheduler.add_job(monitoring.job(onboarding_chain), "cron", minute=40, args=[bot])
    scheduler.add_job(monitoring.job(weekly_report), "cron", day_of_week=config.WEEKLY_REPORT_DAY, hour=config.WEEKLY_REPORT_HOUR, args=[bot])
    return scheduler
