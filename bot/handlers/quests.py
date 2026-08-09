"""Хендлеры: /quests и отметка выполнения квестов."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, game, texts
from bot.handlers.helpers import load_user, notify_xp_events, process_day_events
from bot.safehtml import esc

router = Router()


def _callback_id(data: str) -> int | None:
    """id из callback_data вида 'done:123'. None — данные подделаны или битые."""
    try:
        return int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _quests_view(quests) -> tuple[str, InlineKeyboardMarkup | None]:
    done_count = sum(1 for q in quests if q["done"])
    lines = [f"<b>{texts.SYS} // КВЕСТЫ ДНЯ</b>  ⟨{done_count}/{len(quests)}⟩", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for q in quests:
        mark = "✅" if q["done"] else "◻"
        tag = " ⟨личный⟩" if q["is_custom"] else ""
        lines.append(f"{mark} <b>{esc(q['title'])}</b>{tag}\n     [{config.STAT_LABELS[q['stat']]}] +{q['xp']} XP")
        if not q["done"]:
            buttons.append([InlineKeyboardButton(text=f"Исполнить: {q['title'][:32]}", callback_data=f"done:{q['id']}")])
    if done_count == len(quests):
        lines.append("\nВсе квесты исполнены. Система наблюдает за твоим отдыхом.")
    else:
        lines.append("\nНевыполнение будет наказано.")
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    return "\n".join(lines), kb


@router.message(Command("quests"))
async def cmd_quests(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    await process_day_events(message, user)
    quests = await db.quests_for_date(message.from_user.id, game.today_str(user))
    text, kb = _quests_view(quests)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("first:"))
async def cb_first_quest_done(callback: CallbackQuery) -> None:
    """Онбординг: выполнение первого квеста с мгновенным фидбеком."""
    quest_id = _callback_id(callback.data)
    if quest_id is None:
        await callback.answer("Квест не найден в реестре.", show_alert=True)
        return
    quest = await db.get_quest(quest_id)
    if quest is None or quest["user_id"] != callback.from_user.id:
        await callback.answer("Квест не найден в реестре.", show_alert=True)
        return
    if quest["done"]:
        await callback.answer("Уже исполнено.")
        return
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Отправь /start.", show_alert=True)
        return
    if quest["quest_date"] != game.today_str(user):
        await callback.answer("Срок исполнения истёк.", show_alert=True)
        return
    if not await db.mark_quest_done(quest_id):
        await callback.answer("Уже исполнено.")
        return
    result = await game.grant_xp(user, quest["xp"])
    progress = f"Опыт: {result.xp} / {result.xp_needed}"
    await callback.message.answer(texts.QUEST_DONE.format(title=esc(quest["title"]), xp=result.amount, stat=config.STAT_LABELS[quest["stat"]], progress=progress))
    await notify_xp_events(callback.message, result, user_id=callback.from_user.id)
    await callback.message.answer(texts.ONBOARDING_FIRST_DONE)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Проверено. Засчитано.")


@router.callback_query(F.data.startswith("done:"))
async def cb_quest_done(callback: CallbackQuery) -> None:
    quest_id = _callback_id(callback.data)
    if quest_id is None:
        await callback.answer("Квест не найден в реестре.", show_alert=True)
        return
    quest = await db.get_quest(quest_id)
    if quest is None or quest["user_id"] != callback.from_user.id:
        await callback.answer("Квест не найден в реестре.", show_alert=True)
        return
    if quest["done"]:
        await callback.answer("Уже исполнено. Система не начисляет опыт дважды.")
        return
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Отправь /start.", show_alert=True)
        return
    if quest["quest_date"] != game.today_str(user):
        await callback.answer("Срок исполнения истёк. Система не принимает просрочку.", show_alert=True)
        return
    if not await db.mark_quest_done(quest_id):
        await callback.answer("Уже исполнено. Система не начисляет опыт дважды.")
        return
    result = await game.grant_xp(user, quest["xp"])
    progress = f"Опыт: {result.xp} / {result.xp_needed}"
    await callback.message.answer(texts.QUEST_DONE.format(title=esc(quest["title"]), xp=result.amount, stat=config.STAT_LABELS[quest["stat"]], progress=progress))
    await notify_xp_events(callback.message, result, user_id=callback.from_user.id)
    quests = await db.quests_for_date(callback.from_user.id, game.today_str(user))
    if all(q["done"] for q in quests):
        user = await db.get_user(callback.from_user.id)
        # Серия закрывается только при следующем rollover, поэтому +1 здесь
        # обещал значение, которого ещё нет в профиле.
        await callback.message.answer(texts.ALL_QUESTS_DONE.format(streak=user["streak"]))
    text, kb = _quests_view(quests)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Исполнено.")
