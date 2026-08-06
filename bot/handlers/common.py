"""Хендлеры: /start (с рефералкой), /help, /profile."""
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, game, keyboards, texts, timeutil
from bot.handlers.helpers import load_user, process_day_events
from bot.safehtml import display_name, esc, user_name

router = Router()


async def _ask_timezone(message: Message, user) -> None:
    """Предложить новичку выбрать пояс. Обрабатывает cb_set_timezone в settings."""
    await message.answer(
        texts.TZ_ONBOARDING.format(current=timeutil.label_of(user)),
        reply_markup=keyboards.timezone_menu(),
    )


def _parse_ref(args: str | None) -> int:
    """Извлечь id пригласившего из deep-link payload вида 'ref12345'."""
    if args and args.startswith("ref"):
        try:
            return int(args[3:])
        except ValueError:
            pass
    return 0


async def _process_referral(message: Message, new_user_id: int, referrer_id: int) -> None:
    """Начислить бонусы новичку и вербовщику, выдать премиум за порог.

    Все переходы состояния атомарны и происходят ДО отправки сообщений:
    доставка может упасть (вербовщик заблокировал бота), но награда за это
    пропасть не должна.
    """
    referrer = await db.get_user(referrer_id)
    if referrer is None or referrer_id == new_user_id:
        return

    # Связь занимаем атомарно: /start обрабатывает вербовку только для нового
    # пользователя, но два быстрых нажатия по одной ссылке успевали пройти
    # проверку «пользователя ещё нет» одновременно и начислить бонус дважды.
    if not await db.claim_referral(new_user_id, referrer_id):
        return

    # Бонус новичку
    new_user = await db.get_user(new_user_id)
    if new_user is None:
        return
    await game.grant_xp(new_user, config.REF_BONUS_XP, count_quest=False)
    # Имена в этих двух сообщениях — чужие: имя вербовщика видит новичок, имя
    # новичка видит вербовщик. Оба идут в HTML, поэтому экранируем.
    ref_name = display_name(referrer)
    await message.answer(
        texts.REF_WELCOME_BONUS.format(name=ref_name, bonus=config.REF_BONUS_XP)
    )

    # Бонус вербовщику. Инкремент возвращает новое значение одним запросом:
    # отдельные UPDATE + SELECT при двух одновременных вербовках оба видели
    # уже дважды увеличенный ref_count, и точный порог не ловил никто.
    ref_count = await db.increment_and_get(referrer_id, "ref_count")
    await game.grant_xp(referrer, config.REF_BONUS_XP, count_quest=False)

    # Премиум за порог — до отправки: раньше и выдача, и уведомление лежали в
    # одном try, поэтому вербовщик, заблокировавший бота, терял награду.
    # Продлеваем от текущей даты окончания, иначе оплаченный Монарх
    # укорачивался бы до недели.
    premium_granted = ref_count == config.REF_PREMIUM_THRESHOLD
    if premium_granted:
        await db.update_user(
            referrer_id,
            premium_until=game.premium_until_after(
                referrer["premium_until"], config.REF_PREMIUM_DAYS
            ),
        )

    new_name = user_name(message.from_user)
    try:
        await message.bot.send_message(
            referrer_id,
            texts.REF_NEW_HUNTER.format(
                name=new_name, bonus=config.REF_BONUS_XP, count=ref_count
            ),
        )
        if premium_granted:
            await message.bot.send_message(
                referrer_id,
                texts.REF_PREMIUM_GRANTED.format(
                    threshold=config.REF_PREMIUM_THRESHOLD,
                    days=config.REF_PREMIUM_DAYS,
                ),
            )
    except Exception:
        pass  # вербовщик мог заблокировать бота


async def _send_first_quest(message: Message, user) -> None:
    """Онбординг: сразу дать новичку самый лёгкий квест дня с кнопкой."""
    quests = await db.quests_for_date(message.from_user.id, game.today_str(user))
    pending = [q for q in quests if not q["done"]]
    if not pending:
        return
    first = min(pending, key=lambda q: q["xp"])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔ Исполнено — проверяй", callback_data=f"first:{first['id']}"
                )
            ]
        ]
    )
    await message.answer(
        texts.ONBOARDING_FIRST_QUEST.format(title=esc(first["title"]), xp=first["xp"]),
        reply_markup=kb,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None:
        await db.create_user(
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.first_name or "",
        )
        await message.answer(texts.WELCOME_NEW, reply_markup=keyboards.main_menu())
        referrer_id = _parse_ref(command.args)
        if referrer_id:
            await _process_referral(message, message.from_user.id, referrer_id)
        user = await db.get_user(message.from_user.id)
        await process_day_events(message, user)
        await _send_first_quest(message, user)
        # Пояс спрашиваем сразу: пока он не выбран, день закрывается по
        # серверному времени, и охотник из другого пояса теряет серию не в свою
        # полночь. Ответ не обязателен — есть дефолт, поэтому FSM не поднимаем.
        await _ask_timezone(message, user)
    else:
        await message.answer(texts.WELCOME_BACK, reply_markup=keyboards.main_menu())
        await process_day_events(message, user)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        texts.HELP.format(
            privacy_url=config.PRIVACY_URL, terms_url=config.TERMS_URL
        ),
        reply_markup=keyboards.main_menu(),
        disable_web_page_preview=True,
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    if await process_day_events(message, user):
        user = await db.get_user(message.from_user.id)

    rank = config.rank_for_level(user["level"])
    xp_needed = config.xp_to_next(user["level"])
    # Своё имя ломает разметку только себе, но 400 от Telegram делает /profile
    # недоступным навсегда — экранируем так же, как чужие.
    name = display_name(user)
    premium = " ⟨МОНАРХ⟩" if game.is_premium(user) else ""
    freezes = f"  |  Заморозки: {user['streak_freezes']}" if user["streak_freezes"] else ""

    lines = [
        f"<b>{texts.SYS} // СТАТУС ОХОТНИКА</b>",
        "",
        f"<b>{name}</b>{premium}",
        f"Ранг: <b>{rank}</b>  |  Уровень: <b>{user['level']}</b>",
        f"Опыт: <b>{user['xp']} / {xp_needed}</b>",
        f"HP: [{game.hp_bar(user['hp'], user['max_hp'])}] <b>{user['hp']}/{user['max_hp']}</b>"
        + (texts.PROFILE_DYING_MARK if game.is_dying(user) else ""),
        "",
        f"⚔ Сила: <b>{user['strength']}</b>",
        f"🧠 Интеллект: <b>{user['intelligence']}</b>",
        f"🛡 Выносливость: <b>{user['endurance']}</b>",
        f"⚡ Ловкость: <b>{user['agility']}</b>",
        f"👁 Харизма: <b>{user['charisma']}</b>",
        "",
        f"Серия дней: <b>{user['streak']}</b> 🔥 (рекорд: {user['best_streak']}){freezes}",
        f"Смертей: {user['deaths']}",
        "",
        "Карточка: /card  |  Отчёт: /report  |  Босс: /boss",
    ]
    # Апселл в /profile: воскрешение — только в окне «при смерти», где покупка
    # реально спасает уровень; иначе Монарх, и только тем, у кого его нет.
    if game.is_dying(user):
        offer = config.UPSELL_REVIVE
    elif game.is_premium(user):
        offer = None
    else:
        offer = config.UPSELL_PREMIUM
    await message.answer("\n".join(lines), reply_markup=keyboards.upsell(offer))
