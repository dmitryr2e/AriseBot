"""Босс недели: общий рейд-босс, урон которому наносится полученным XP."""
import asyncio
from datetime import datetime

from bot import config, db

BOSS_NAMES = [
    "Игрис, Рыцарь Крови",
    "Барука, Король Ледяных Клыков",
    "Каргалган, Вождь Гоблинов",
    "Метус, Страж Врат",
    "Танатос, Пожиратель Теней",
    "Вулкан, Демон Пламени",
    "Керберос, Хранитель Бездны",
    "Архилич Некрон",
]

# damage_boss обновлял HP и таблицу урона разными SQL-операциями. Два
# одновременных grant_xp могли оба увидеть живого босса и записать урон уже
# после его смерти, искажая топ-3 награждаемых.
_damage_lock: asyncio.Lock | None = None
_damage_loop: asyncio.AbstractEventLoop | None = None


def _get_damage_lock() -> asyncio.Lock:
    """Вернуть lock текущего event loop. Это важно для изолированных тестов."""
    global _damage_lock, _damage_loop
    loop = asyncio.get_running_loop()
    if _damage_lock is None or _damage_loop is not loop:
        _damage_lock = asyncio.Lock()
        _damage_loop = loop
    return _damage_lock


def week_key(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(config.TZ)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


async def get_or_create_boss():
    """Босс текущей недели; HP масштабируется от числа охотников."""
    key = week_key()
    boss = await db.get_boss(key)
    if boss is not None:
        return boss
    hunters = len(await db.all_users())
    max_hp = config.BOSS_BASE_HP + hunters * config.BOSS_HP_PER_HUNTER
    # Имя детерминировано от недели, чтобы не зависеть от гонок INSERT OR IGNORE
    name = BOSS_NAMES[sum(map(ord, key)) % len(BOSS_NAMES)]
    return await db.create_boss(key, name, max_hp)


async def deal_damage(user_id: int, xp_amount: int) -> tuple[int, bool]:
    """Урон боссу = полученный XP. Возвращает (остаток HP, добит ли этим ударом)."""
    if xp_amount <= 0:
        return 0, False
    async with _get_damage_lock():
        boss = await get_or_create_boss()
        if boss["defeated"]:
            return 0, False
        hp_left = await db.damage_boss(boss["id"], user_id, xp_amount)
        return hp_left, hp_left <= 0


def bar(hp: int, max_hp: int, width: int = 14) -> str:
    filled = max(0, round(width * hp / max_hp))
    return "█" * filled + "░" * (width - filled)
