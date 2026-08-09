"""Админ-панель: /admin — статистика, /broadcast — рассылка.

Доступ только для id из переменной окружения ADMIN_IDS (через запятую).
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import config, db, game, texts, timeutil

log = logging.getLogger(__name__)
router = Router()
_refund_lock = asyncio.Lock()


def _is_admin(message: Message) -> bool:
    return message.from_user.id in config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message):
        return

    today = game.today_str()
    week_ago = (datetime.now(config.TZ) - timedelta(days=7)).date()
    users = await db.all_users()
    total = len(users)
    # SQLite datetime('now') хранится в UTC, поэтому date(created_at) в SQL
    # ошибается для регистраций около полуночи по часовому поясу бота.
    created_dates = {
        user["user_id"]: timeutil.local_date_of(user["created_at"], config.TZ_NAME)
        for user in users
    }
    new_today = sum(1 for value in created_dates.values() if value == today)
    new_week = sum(
        1
        for value in created_dates.values()
        if value and datetime.strptime(value, "%Y-%m-%d").date() >= week_ago
    )

    dau = await db.count_where("users", "last_seen = ?", (today,))
    wau = await db.count_where("users", "last_seen >= ?", (week_ago.strftime("%Y-%m-%d"),))
    premium = await db.count_where(
        "users", "premium_until >= ?", (datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S"),)
    )
    quests_today = await db.count_where("quests", "quest_date = ? AND done = 1", (today,))
    reports_today = await db.count_where("reports", "report_date = ?", (today,))
    reports_total = await db.count_where("reports")
    refs_total = await db.count_where("users", "referred_by != 0")
    dead = await db.count_where("users", "deaths > 0")

    from bot import boss as boss_mod
    b = await db.get_boss(boss_mod.week_key())
    boss_line = (
        f"{b['name']}: {b['hp']}/{b['max_hp']}" + (" ⟨повержен⟩" if b and b["defeated"] else "")
        if b
        else "ещё не создан"
    )
    lines = [
        f"<b>{texts.SYS} // ПАНЕЛЬ НАБЛЮДАТЕЛЯ</b>", "",
        f"<b>Охотники:</b> {total}",
        f"— новых сегодня: {new_today}  |  за 7 дн: {new_week}",
        f"— DAU: {dau}  |  WAU: {wau}", f"— с премиумом: {premium}",
        f"— пришли по рефералке: {refs_total}", f"— умирали хоть раз: {dead}",
        "", "<b>Активность сегодня:</b>",
        f"— квестов выполнено: {quests_today}",
        f"— ИИ-отчётов: {reports_today} (всего: {reports_total})", "",
        f"<b>Босс недели:</b> {boss_line}", "",
        "Рассылка: <code>/broadcast текст</code>",
        "Платежи: <code>/payments user_id</code> · Возврат: <code>/refund charge_id</code>",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("payments"))
async def cmd_payments(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer(f"<b>{texts.SYS}</b>\n\nФормат: <code>/payments user_id</code>")
        return
    user_id = int(command.args.strip())
    rows = await db.user_payments(user_id)
    if not rows:
        await message.answer(f"<b>{texts.SYS}</b>\n\nПлатежей у {user_id} не найдено.")
        return
    lines = [f"<b>{texts.SYS} // ПЛАТЕЖИ {user_id}</b>", ""]
    for p in rows:
        status = " ⟨возврат⟩" if p["refunded"] else ""
        lines.append(f"— {p['created_at']} · {p['payload']} · {p['amount_stars']} ⭐{status}\n  <code>{p['charge_id']}</code>")
    await message.answer("\n".join(lines))


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    if not command.args:
        await message.answer(f"<b>{texts.SYS}</b>\n\nФормат: <code>/refund charge_id</code>")
        return
    async with _refund_lock:
        charge_id = command.args.strip()
        payment = await db.get_payment(charge_id)
        if payment is None:
            await message.answer(f"<b>{texts.SYS}</b>\n\nПлатёж не найден: <code>{charge_id}</code>")
            return
        if payment["refunded"]:
            await message.answer(f"<b>{texts.SYS}</b>\n\nЭтот платёж уже возвращён.")
            return
        try:
            await message.bot.refund_star_payment(user_id=payment["user_id"], telegram_payment_charge_id=charge_id)
        except Exception as e:
            log.exception("Ошибка возврата платежа %s", charge_id)
            await message.answer(f"<b>{texts.SYS}</b>\n\nОшибка возврата: <code>{e}</code>")
            return
        await db.mark_payment_refunded(charge_id)
        user = await db.get_user(payment["user_id"])
        rollback_note = ""
        if user is not None:
            if payment["payload"] == "premium30":
                await db.update_user(payment["user_id"], premium_until="", is_premium=0)
                rollback_note = " Премиум снят."
            elif payment["payload"] == "freeze" and user["streak_freezes"] > 0:
                await db.update_user(payment["user_id"], streak_freezes=user["streak_freezes"] - 1)
                rollback_note = " Заморозка списана."
        try:
            await message.bot.send_message(payment["user_id"], f"<b>{texts.SYS}</b>\n\nТвой платёж ({payment['amount_stars']} ⭐) возвращён.")
        except Exception:
            pass
        await message.answer(f"<b>{texts.SYS}</b>\n\nВозврат выполнен: {payment['amount_stars']} ⭐ пользователю {payment['user_id']}.{rollback_note}")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    if not command.args:
        await message.answer(f"<b>{texts.SYS}</b>\n\nФормат: <code>/broadcast текст сообщения</code>\nТекст уйдёт всем охотникам от имени Системы.")
        return
    text = f"<b>{texts.SYS} // ОБЪЯВЛЕНИЕ</b>\n\n{command.args}"
    users = await db.all_users()
    sent = failed = 0
    for user in users:
        try:
            await message.bot.send_message(user["user_id"], text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"<b>{texts.SYS}</b>\n\nРассылка завершена. Доставлено: {sent}, недоступно: {failed}.")
