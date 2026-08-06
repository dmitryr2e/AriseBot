"""Игровая логика: опыт, уровни, HP, смерть, смена дня, стрики, врата."""
import asyncio
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bot import config, db, timeutil
from bot.quests_pool import CUSTOM_QUEST_XP, GATE_POOL, QUEST_POOL

log = logging.getLogger(__name__)

# Замок на пользователя: защита от двойной выдачи квестов при гонке
# (одновременный вызов из планировщика и хендлера).
_day_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# Замок на начисление опыта: сериализует конкурентные grant_xp одного охотника,
# чтобы compare-and-set ниже не крутился впустую при высокой конкуренции.
_xp_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# Сколько раз повторять compare-and-set, если строку изменили между чтением и
# записью (второй процесс бота, планировщик — там, где замок не помогает)
_CAS_ATTEMPTS = 10

# Формат хранения premium_until в БД
PREMIUM_UNTIL_FMT = "%Y-%m-%d %H:%M:%S"


def today_str(user=None) -> str:
    """Текущая игровая дата.

    С `user` — в его часовом поясе (граница дня охотника), без него — в поясе
    сервера. Вызовы без аргумента остались только там, где день заведомо
    общий: админ-сводка, ключ недели босса.
    """
    if user is None:
        return datetime.now(config.TZ).strftime("%Y-%m-%d")
    return timeutil.today_for(user)


def _days_between(start: str, end: str) -> int:
    """Календарных дней между двумя датами YYYY-MM-DD. 0, если даты битые."""
    try:
        d1 = date.fromisoformat(start)
        d2 = date.fromisoformat(end)
    except ValueError:
        return 0
    return max(0, (d2 - d1).days)


@dataclass
class XpResult:
    """Результат начисления опыта."""
    levels_gained: list[tuple[int, str, int]] = field(default_factory=list)  # (level, stat, gain)
    rank_up: str | None = None
    xp: int = 0
    xp_needed: int = 0
    level: int = 0
    amount: int = 0            # фактически начислено (с учётом премиума)
    boss_hp_left: int = -1     # HP босса после удара (-1 — босс уже побеждён)
    boss_killed: bool = False  # добит ли босс этим начислением


@dataclass
class DayEvents:
    """События, произошедшие при обработке смены дня."""
    new_day: bool = False
    quests_issued: int = 0
    missed: int = 0
    damage: int = 0
    streak_up: bool = False
    streak_reset: bool = False
    streak_frozen: bool = False
    # Пропущено дней полного отсутствия (бот лежал / охотник исчез)
    skipped_days: int = 0
    died: bool = False
    death_level: int = 0
    # Охотник ушёл в состояние «при смерти»: HP на нуле, уровень ещё цел
    dying: bool = False
    # Окно «при смерти» закрылось с HP > 0 — смерти не произошло
    dying_survived: bool = False
    hp: int = 0
    max_hp: int = 0
    level: int = 0
    streak: int = 0
    freezes: int = 0
    # Веха серии: (день серии, бонус XP, подаренные заморозки)
    milestone: tuple[int, int, int] | None = None
    milestone_result: XpResult | None = None
    # Врата дня: название бонус-квеста, если открылись
    gate_title: str | None = None


def is_premium(user) -> bool:
    """Активен ли премиум (по дате premium_until)."""
    until = user["premium_until"] or ""
    if not until:
        return bool(user["is_premium"])
    return until >= datetime.now(config.TZ).strftime(PREMIUM_UNTIL_FMT)


def premium_until_after(current: str | None, days: int) -> str:
    """Новая дата окончания премиума при продлении на `days` дней.

    Отсчёт идёт от текущей даты окончания, если премиум ещё активен, и от
    «сейчас» в остальных случаях (нет премиума, истёк, битая строка в БД).

    Общая для покупки за звёзды и для награды за вербовку: награда писала
    дату абсолютно, поэтому Монарх, оплативший 30 дней и на следующий день
    приведший десятого друга, терял оплаченный срок и получал 7 дней.
    """
    now = datetime.now(config.TZ)
    base = now
    if current:
        try:
            existing = datetime.strptime(current, PREMIUM_UNTIL_FMT).replace(tzinfo=config.TZ)
            if existing > now:
                base = existing
        except ValueError:
            pass
    return (base + timedelta(days=days)).strftime(PREMIUM_UNTIL_FMT)


def is_dying(user) -> bool:
    """Охотник в окне «при смерти»: HP на нуле, уровень ещё не потерян.

    Проверяется и дата окна, и HP: левелап или покупка воскрешения могли
    поднять HP раньше, чем окно закрылось на следующем rollover.
    """
    dying_until = user["dying_until"] or ""
    return bool(dying_until) and dying_until >= today_str(user) and user["hp"] <= 0


async def ensure_today(user) -> DayEvents:
    """Обработать смену дня: штрафы за вчера, стрик, вехи, выдача квестов."""
    async with _day_locks[user["user_id"]]:
        # Перечитываем строку под замком: параллельный вызов мог уже всё сделать
        user = await db.get_user(user["user_id"]) or user
        return await _ensure_today_locked(user)


async def _ensure_today_locked(user) -> DayEvents:
    events = DayEvents()
    today = today_str(user)
    last = user["last_daily_date"]

    if last == today:
        return events

    # Дата ушла назад: охотник сменил пояс на более западный (Владивосток ->
    # Москва) или на сервере поправили часы. Итоги того же дня подводить
    # второй раз нельзя — списали бы серию и HP за уже закрытый день.
    # Ждём, пока локальная дата снова догонит last_daily_date.
    if last and last > today:
        log.info(
            "ensure_today: у юзера %s дата ушла назад (%s -> %s), пропускаем rollover",
            user["user_id"],
            last,
            today,
        )
        return events

    events.new_day = True
    hp = user["hp"]
    streak = user["streak"]
    best_streak = user["best_streak"]
    level = user["level"]
    xp = user["xp"]

    # --- Итоги прошлого дня (если квесты выдавались) ---
    freezes = user["streak_freezes"]
    milestone: tuple[int, int, int] | None = None
    if last:
        prev_quests = await db.quests_for_date(user["user_id"], last)
        if prev_quests:
            missed = sum(1 for q in prev_quests if not q["done"])
            if missed == 0:
                streak += 1
                best_streak = max(best_streak, streak)
                events.streak_up = True
                # Веха серии: бонус XP и заморозки
                bonus = config.STREAK_MILESTONES.get(streak)
                if bonus:
                    bonus_xp, bonus_freezes = bonus
                    freezes += bonus_freezes
                    milestone = (streak, bonus_xp, bonus_freezes)
            elif freezes > 0:
                # Заморозка: стрик и HP сохраняются, тратится 1 заряд
                freezes -= 1
                events.streak_frozen = True
            else:
                events.missed = missed
                events.damage = missed * config.HP_PENALTY_PER_MISS
                hp -= events.damage
                if streak > 0:
                    events.streak_reset = True
                streak = 0

        # --- Честный учёт дней полного отсутствия ---
        # Между last и сегодня могли пройти дни, за которые квесты вообще
        # не выдавались (охотник не заходил или бот лежал). Раньше они
        # игнорировались и серия выживала нечестно.
        skipped = _days_between(last, today) - 1
        if skipped > 0:
            events.skipped_days = skipped
            # Каждый пропущенный день можно закрыть заморозкой
            covered = min(freezes, skipped)
            freezes -= covered
            uncovered = skipped - covered
            if covered:
                events.streak_frozen = True
            if uncovered:
                if streak > 0:
                    events.streak_reset = True
                streak = 0
                extra = min(uncovered, config.SKIPPED_DAMAGE_MAX_DAYS)
                extra_damage = extra * config.SKIPPED_DAY_DAMAGE
                events.damage += extra_damage
                hp -= extra_damage

    # --- «При смерти» и смерть ---
    # HP <= 0 больше не убивает сразу: охотник получает окно до конца дня,
    # внутри которого уровень ещё можно спасти (воскрешение за звёзды или
    # левелап, восстанавливающий HP). Смерть наступает на следующем rollover,
    # если HP так и осталось на нуле.
    dying_until = user["dying_until"] or ""
    if dying_until and dying_until < today:
        dying_until = ""
        if hp <= 0:
            events.died = True
            level = max(1, level - 1)
            xp = 0
            hp = int(user["max_hp"] * config.DEATH_HP_RESTORE_RATIO)
            events.death_level = level
            await db.increment_user(user["user_id"], deaths=1)
        else:
            events.dying_survived = True

    if hp <= 0:
        # Либо охотник только что упал, либо окно ещё не истекло (тот же день)
        events.dying = True
        hp = 0
        dying_until = dying_until or today

    events.hp = hp
    events.max_hp = user["max_hp"]
    events.level = level
    events.streak = streak
    events.freezes = freezes

    # --- Выдача квестов на сегодня (с защитой от дублей) ---
    if not await db.quests_for_date(user["user_id"], today):
        daily = random.sample(QUEST_POOL, k=min(config.DAILY_QUEST_COUNT, len(QUEST_POOL)))
        rows = [
            (user["user_id"], title, stat, q_xp, today, 0)
            for title, stat, q_xp in daily
        ]
        for cq in await db.custom_quests(user["user_id"]):
            rows.append((user["user_id"], cq["title"], cq["stat"], CUSTOM_QUEST_XP, today, 1))
        # Врата: с шансом GATE_CHANCE открывается бонус-квест повышенной сложности
        if random.random() < config.GATE_CHANCE:
            g_title, g_stat, g_xp = random.choice(GATE_POOL)
            events.gate_title = g_title
            rows.append((user["user_id"], f"⛩ {g_title}", g_stat, g_xp, today, 0))
        await db.insert_quests(rows)
        events.quests_issued = len(rows)

    await db.update_user(
        user["user_id"],
        last_daily_date=today,
        hp=hp,
        streak=streak,
        best_streak=best_streak,
        level=level,
        xp=xp,
        streak_freezes=freezes,
        reports_today=0,
        dying_until=dying_until,
    )

    # --- Бонус вехи серии (после сохранения основного состояния) ---
    if milestone:
        events.milestone = milestone
        fresh = await db.get_user(user["user_id"])
        if fresh is not None:
            events.milestone_result = await grant_xp(fresh, milestone[1], count_quest=False)

    return events


async def grant_xp(user, amount: int, count_quest: bool = True) -> XpResult:
    """Начислить опыт с обработкой левелапов, рангов, премиум-множителя и урона боссу.

    Начисление атомарно: строка перечитывается перед каждой попыткой, счётчики
    (weekly_xp, weekly_done, total_done, статы) пишутся инкрементами, а уровень
    и остаток XP — через compare-and-set. Если между чтением и записью кто-то
    успел изменить уровень или XP (второй хендлер, планировщик, бонус вехи),
    попытка повторяется, и ни одно начисление не теряется.
    """
    from bot import boss as boss_mod

    user_id = user["user_id"]
    result = XpResult()

    async with _xp_locks[user_id]:
        applied = await _apply_xp(user_id, amount, 1 if count_quest else 0, result)
    if not applied:
        return result

    # Урон боссу недели = начисленный XP
    hp_left, killed = await boss_mod.deal_damage(user_id, result.amount)
    result.boss_hp_left = hp_left
    result.boss_killed = killed

    return result


async def _apply_xp(user_id: int, base_amount: int, inc: int, result: XpResult) -> bool:
    """Записать начисление в БД. Возвращает False, если начислить не удалось."""
    for _ in range(_CAS_ATTEMPTS):
        fresh = await db.get_user(user_id)
        if fresh is None:
            return False

        amount = base_amount
        if is_premium(fresh):
            amount = int(amount * config.PREMIUM_XP_MULT)

        level = fresh["level"]
        xp = fresh["xp"] + amount
        old_rank = config.rank_for_level(level)

        stat_gains: dict[str, int] = {}
        levels_gained: list[tuple[int, str, int]] = []
        hp: int | None = None

        while xp >= config.xp_to_next(level):
            xp -= config.xp_to_next(level)
            level += 1
            stat = random.choice(config.STATS)
            gain = random.choices([1, 2], weights=[80, 20])[0]
            stat_gains[stat] = stat_gains.get(stat, 0) + gain
            hp = fresh["max_hp"]  # полное восстановление при левелапе
            levels_gained.append((level, config.STAT_FULL[stat], gain))

        absolute: dict[str, int] = {"level": level, "xp": xp}
        if hp is not None:
            absolute["hp"] = hp

        applied = await db.compare_and_set_user(
            user_id,
            expect={"level": fresh["level"], "xp": fresh["xp"]},
            absolute=absolute,
            increments={
                "weekly_xp": amount,
                "weekly_done": inc,
                "total_done": inc,
                **stat_gains,
            },
        )
        if not applied:
            continue  # строку изменили параллельно — читаем заново

        result.amount = amount
        result.levels_gained = levels_gained
        new_rank = config.rank_for_level(level)
        if new_rank != old_rank:
            result.rank_up = new_rank
        result.xp = xp
        result.xp_needed = config.xp_to_next(level)
        result.level = level
        return True

    log.error("grant_xp: не удалось начислить %s XP юзеру %s", base_amount, user_id)
    return False


def hp_bar(hp: int, max_hp: int, width: int = 10) -> str:
    filled = max(0, round(width * hp / max_hp))
    return "█" * filled + "░" * (width - filled)
