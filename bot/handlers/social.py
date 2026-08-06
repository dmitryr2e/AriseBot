"""Хендлеры: /rating, /ref, /achievements, /hideme."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import achievements as ach_mod
from bot import config, db, keyboards, share, texts
from bot.handlers.helpers import load_user, process_day_events
from bot.safehtml import display_name

router = Router()


@router.message(Command("rating"))
async def cmd_rating(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    await process_day_events(message, user)

    top = await db.weekly_top(limit=10)
    if not top:
        await message.answer(texts.RATING_EMPTY)
        return

    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    lines = [texts.RATING_HEADER]
    for i, row in enumerate(top):
        # display_name, а не сырое поле: имя охотника видят все остальные, и
        # без экранирования оно превращалось в разметку (ссылка-фишинг или
        # битый тег, роняющий /rating всем сразу).
        name = display_name(row)
        rank = config.rank_for_level(row["level"])
        lines.append(
            f"{medals[i]} <b>{name}</b> — {row['weekly_xp']} XP "
            f"⟨ур. {row['level']} · ранг {rank} · серия {row['streak']}⟩"
        )
    text = "\n".join(lines)

    user = await db.get_user(message.from_user.id)
    if user["weekly_xp"] > 0:
        pos, total = await db.weekly_position(message.from_user.id)
        text += texts.RATING_POSITION.format(pos=pos, total=total, xp=user["weekly_xp"])
    text += texts.RATING_HIDDEN_NOTE
    await message.answer(text)


@router.message(Command("hideme"))
async def cmd_hideme(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    hidden = 0 if user["hide_in_rating"] else 1
    await db.update_user(message.from_user.id, hide_in_rating=hidden)
    await message.answer(texts.HIDE_ON if hidden else texts.HIDE_OFF)


@router.message(Command("ref"))
async def cmd_ref(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    await message.answer(
        texts.REF_INFO.format(
            link=share.ref_link(message.from_user.id),
            count=user["ref_count"],
            bonus=config.REF_BONUS_XP,
            threshold=config.REF_PREMIUM_THRESHOLD,
            days=config.REF_PREMIUM_DAYS,
        ),
        # Без кнопки ссылку приходилось копировать вручную и пересылать самому —
        # главная потеря в реф-воронке была именно здесь.
        reply_markup=keyboards.share_kb(
            message.from_user.id, share.card_text(user)
        ),
    )


@router.message(Command("achievements"))
async def cmd_achievements(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    await process_day_events(message, user)

    unlocked = await db.user_achievements(message.from_user.id)
    lines = [
        texts.ACHIEVEMENTS_HEADER.format(
            unlocked=len(unlocked), total=len(ach_mod.ACHIEVEMENTS)
        )
    ]
    for ach in ach_mod.ACHIEVEMENTS:
        if ach.code in unlocked:
            lines.append(f"🏆 <b>{ach.title}</b> — {ach.desc}")
        else:
            lines.append(f"🔒 <i>{ach.title}</i> — {ach.desc}")
    await message.answer("\n".join(lines))
