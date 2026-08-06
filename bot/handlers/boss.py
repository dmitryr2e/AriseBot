"""Хендлер: /boss — статус босса недели."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import boss as boss_mod
from bot import config, db, texts
from bot.handlers.helpers import load_user, process_day_events
from bot.safehtml import display_name

router = Router()


@router.message(Command("boss"))
async def cmd_boss(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    await process_day_events(message, user)

    boss = await boss_mod.get_or_create_boss()
    damagers = await db.boss_top_damagers(boss["id"], limit=5)
    my_damage = 0
    for row in await db.boss_participants(boss["id"]):
        if row["user_id"] == message.from_user.id:
            my_damage = row["damage"]
            break

    if boss["defeated"]:
        text = texts.BOSS_DEFEATED_STATUS.format(name=boss["name"], my_damage=my_damage)
    else:
        text = texts.BOSS_STATUS.format(
            name=boss["name"],
            bar=boss_mod.bar(boss["hp"], boss["max_hp"]),
            hp=boss["hp"],
            max_hp=boss["max_hp"],
            my_damage=my_damage,
            reward=config.BOSS_REWARD_XP,
            top_reward=config.BOSS_TOP_REWARD_XP,
        )

    if damagers:
        lines = [texts.BOSS_TOP_HEADER]
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        for i, row in enumerate(damagers):
            # Чужие имена в общем топе — такой же источник разметки, как в
            # /rating: экранируем на подстановке.
            lines.append(f"{medals[i]} {display_name(row)} — {row['damage']}")
        text += "\n" + "\n".join(lines)

    await message.answer(text)
