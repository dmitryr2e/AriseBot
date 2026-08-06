"""Хендлеры личных квестов: /addquest, /myquests."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, game, keyboards, texts
from bot.handlers.helpers import load_user
from bot.quests_pool import CUSTOM_QUEST_XP
from bot.safehtml import esc

router = Router()


class AddQuestFlow(StatesGroup):
    waiting_stat = State()


def _limit_for(user) -> int:
    return config.PREMIUM_CUSTOM_QUESTS if game.is_premium(user) else config.FREE_CUSTOM_QUESTS


def _stat_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=config.STAT_FULL[s], callback_data=f"cqstat:{s}")]
        for s in config.STATS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("addquest"))
async def cmd_addquest(message: Message, command: CommandObject, state: FSMContext) -> None:
    user = await load_user(message)
    if user is None:
        return
    title = (command.args or "").strip()
    if not title or len(title) > 80:
        await message.answer(texts.ADDQUEST_USAGE)
        return

    limit = _limit_for(user)
    existing = await db.custom_quests(user["user_id"])
    if len(existing) >= limit:
        # Монарх уже на расширенном лимите: ему показываем другой текст и
        # не предлагаем купить то, что у него есть.
        if game.is_premium(user):
            await message.answer(texts.ADDQUEST_LIMIT_PREMIUM.format(limit=limit))
        else:
            await message.answer(
                texts.ADDQUEST_LIMIT.format(limit=limit),
                reply_markup=keyboards.upsell(config.UPSELL_PREMIUM),
            )
        return

    await state.set_state(AddQuestFlow.waiting_stat)
    # В FSM кладём сырой заголовок (в БД он тоже хранится как есть),
    # экранируем только на выводе в HTML.
    await state.update_data(title=title)
    await message.answer(
        texts.ADDQUEST_PICK_STAT.format(title=esc(title)), reply_markup=_stat_keyboard()
    )


@router.callback_query(AddQuestFlow.waiting_stat, F.data.startswith("cqstat:"))
async def cb_pick_stat(callback: CallbackQuery, state: FSMContext) -> None:
    stat = callback.data.split(":", 1)[1]
    title = (await state.get_data()).get("title")
    if title is None or stat not in config.STATS:
        await state.clear()
        await callback.answer(texts.ADDQUEST_EXPIRED, show_alert=True)
        return

    # Лимит перепроверяется: пока выбирали стат, квесты могли добавиться
    # с другого устройства, а премиум — истечь.
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await state.clear()
        await callback.answer(texts.ADDQUEST_EXPIRED, show_alert=True)
        return
    limit = _limit_for(user)
    if len(await db.custom_quests(callback.from_user.id)) >= limit:
        await state.clear()
        await callback.answer(texts.ADDQUEST_LIMIT_SHORT.format(limit=limit), show_alert=True)
        return

    await state.clear()
    await db.add_custom_quest(callback.from_user.id, title, stat)
    # Сразу добавляем квест в сегодняшний день, если день уже выдан
    today = game.today_str(user)
    if user["last_daily_date"] == today:
        await db.insert_quests(
            [(callback.from_user.id, title, stat, CUSTOM_QUEST_XP, today, 1)]
        )

    await callback.message.edit_text(
        texts.ADDQUEST_DONE.format(title=esc(title), stat=config.STAT_LABELS[stat])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cqstat:"))
async def cb_pick_stat_stale(callback: CallbackQuery) -> None:
    """Кнопка выбора стата нажата вне протокола: бот перезапущен или выбор уже сделан.

    Без этого хендлера callback остался бы без ответа и у пользователя навсегда
    крутился бы индикатор загрузки на кнопке.
    """
    await callback.answer(texts.ADDQUEST_EXPIRED, show_alert=True)


@router.message(Command("myquests"))
async def cmd_myquests(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    customs = await db.custom_quests(user["user_id"])
    if not customs:
        await message.answer(texts.NO_CUSTOM)
        return

    limit = config.PREMIUM_CUSTOM_QUESTS if game.is_premium(user) else config.FREE_CUSTOM_QUESTS
    lines = [f"<b>{texts.SYS} // ЛИЧНЫЕ КВЕСТЫ</b>  ⟨{len(customs)}/{limit}⟩", ""]
    buttons = []
    for cq in customs:
        lines.append(f"◈ <b>{esc(cq['title'])}</b> [{config.STAT_LABELS[cq['stat']]}]")
        buttons.append(
            [InlineKeyboardButton(text=f"Удалить: {cq['title'][:32]}", callback_data=f"cqdel:{cq['id']}")]
        )
    await message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("cqdel:"))
async def cb_delete_custom(callback: CallbackQuery) -> None:
    try:
        cq_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        # callback_data подделывается тривиально: битое значение — не повод
        # ронять хендлер в глобальный обработчик ошибок.
        await callback.answer("Квест не найден в реестре.", show_alert=True)
        return
    await db.delete_custom_quest(callback.from_user.id, cq_id)
    await callback.message.answer(texts.CUSTOM_DELETED)
    await callback.answer("Удалено.")
