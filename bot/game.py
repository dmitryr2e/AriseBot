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
_day_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_xp_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_CAS_ATTEMPTS = 10
PREMIUM_UNTIL_FMT = "%Y-%m-%d %H:%M:%S"


def today_str(user=None) -> str:
    return (datetime.now(config.TZ) if user is None else timeutil.now_for(user)).strftime("%Y-%m-%d")


def _days_between(start: str, end: str) -> int:
    try:
        d1, d2 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return 0
    return max(0, (d2 - d1).days)


@dataclass
class XpResult:
    levels_gained: list[tuple[int, str, int]] = field(default_factory=list)
    rank_up: str | None = None
    xp: int = 0
    xp_needed: int = 0
    level: int = 0
    amount: int = 0
    boss_hp_left: int = -1
    boss_killed: bool = False


@dataclass
class DayEvents:
    new_day: bool = False
    quests_issued: int = 0
    missed: int = 0
    damage: int = 0
    streak_up: bool = False
    streak_reset: bool = False
    streak_frozen: bool = False
    skipped_days: int = 0
    died: bool = False
    death_level: int = 0
    dying: bool = False
    dying_survived: bool = False
    hp: int = 0
    max_hp: int = 0
    level: int = 0
    streak: int = 0
    freezes: int = 0
    milestone: tuple[int, int, int] | None = None
    milestone_result: XpResult | None = None
    gate_title: str | None = None


def is_premium(user) -> bool:
    until = user["premium_until"] or ""
    if not until:
        return bool(user["is_premium"])
    return until >= datetime.now(config.TZ).strftime(PREMIUM_UNTIL_FMT)


def premium_until_after(current: str | None, days: int) -> str:
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
    dying_until = user["dying_until"] or ""
    return bool(dying_until) and dying_until >= today_str(user) and user["hp"] <= 0


async def ensure_today(user) -> DayEvents:
    """Обработать смену дня и затем начислить бонус вехи вне day-lock."""
    user_id = user["user_id"]
    async with _day_locks[user_id]:
        fresh = await db.get_user(user_id) or user
        events = await _ensure_today_locked(fresh)

    # _ensure_today_locked уже записал дату и квесты. Бонус вехи выполняется
    # после освобождения day-lock, чтобы grant_xp мог взять его без deadlock.
    if events.milestone:
        fresh = await db.get_user(user_id)
        if fresh is not None:
            events.milestone_result = await grant_xp(fresh, events.milestone[1], count_quest=False)
    return events


async def _ensure_today_locked(user) -> DayEvents:
    events = DayEvents()
    today = today_str(user)
    last = user["last_daily_date"]
    if last == today:
        return events
    if last and last > today:
        log.info("ensure_today: у юзера %s дата ушла назад (%s -> %s), пропускаем rollover", user["user_id"], last, today)
        return events

    events.new_day = True
    hp = user["hp"]
    initial_hp = hp
    streak = user["streak"]
    best_streak = user["best_streak"]
    level = user["level"]
    xp = user["xp"]
    freezes = user["streak_freezes"]
    milestone = None

    if last:
        prev_quests = await db.quests_for_date(user["user_id"], last)
        if prev_quests:
            missed = sum(1 for q in prev_quests if not q["done"])
            if missed == 0:
                streak += 1
                best_streak = max(best_streak, streak)
                events.streak_up = True
                bonus = config.STREAK_MILESTONES.get(streak)
                if bonus:
                    bonus_xp, bonus_freezes = bonus
                    freezes += bonus_freezes
                    milestone = (streak, bonus_xp, bonus_freezes)
            elif freezes > 0:
                freezes -= 1
                events.streak_frozen = True
            else:
                events.missed = missed
                events.damage = missed * config.HP_PENALTY_PER_MISS
                hp -= events.damage
                if streak > 0:
                    events.streak_reset = True
                streak = 0

        skipped = _days_between(last, today) - 1
        if skipped > 0:
            events.skipped_days = skipped
            covered = min(freezes, skipped)
            freezes -= covered
            uncovered = skipped - covered
            if covered:
                events.streak_frozen = True
            if uncovered:
                if streak > 0:
                    events.streak_reset = True
                streak = 0
                extra_damage = min(uncovered, config.SKIPPED_DAMAGE_MAX_DAYS) * config.SKIPPED_DAY_DAMAGE
                events.damage += extra_damage
                hp -= extra_damage

    dying_until = user["dying_until"] or ""
    died_now = False
    if dying_until and dying_until < today:
        dying_until = ""
        if hp <= 0:
            events.died = True
            died_now = True
            level = max(1, level - 1)
            xp = 0
            hp = int(user["max_hp"] * config.DEATH_HP_RESTORE_RATIO)
            events.death_level = level
            await db.increment_user(user["user_id"], deaths=1)
        else:
            events.dying_survived = True

    if hp <= 0:
        events.dying = True
        hp = 0
        dying_until = dying_until or today

    events.hp = hp
    events.max_hp = user["max_hp"]
    events.level = level
    events.streak = streak
    events.freezes = freezes
    if not await db.quests_for_date(user["user_id"], today):
        daily = random.sample(QUEST_POOL, k=min(config.DAILY_QUEST_COUNT, len(QUEST_POOL)))
        rows = [(user["user_id"], title, stat, q_xp, today, 0) for title, stat, q_xp in daily]
        for cq in await db.custom_quests(user["user_id"]):
            rows.append((user["user_id"], cq["title"], cq["stat"], CUSTOM_QUEST_XP, today, 1))
        if random.random() < config.GATE_CHANCE:
            g_title, g_stat, g_xp = random.choice(GATE_POOL)
            events.gate_title = g_title
            rows.append((user["user_id"], f"⛩ {g_title}", g_stat, g_xp, today, 0))
        await db.insert_quests(rows)
        events.quests_issued = len(rows)

    # Обычный rollover меняет HP на величину штрафа, поэтому прибавляем delta
    # к актуальному HP в SQL, вместо записи устаревшего абсолютного значения.
    # Так левелап/XP, случившиеся во время await выше, не затираются.
    if not died_now:
        await db.update_rollover_state(
            user["user_id"],
            last_daily_date=today,
            hp_delta=hp - initial_hp,
            streak=streak,
            best_streak=best_streak,
            streak_freezes=freezes,
            reports_today=0,
            dying_until=dying_until,
        )
    else:
        await db.update_user(
            user["user_id"], last_daily_date=today, hp=hp, streak=streak,
            best_streak=best_streak, level=level, xp=xp,
            streak_freezes=freezes, reports_today=0, dying_until=dying_until,
        )
    events.milestone = milestone
    return events


async def grant_xp(user, amount: int, count_quest: bool = True) -> XpResult:
    from bot import boss as boss_mod
    user_id = user["user_id"]
    result = XpResult()
    # Единый порядок блокировок: сначала день, потом XP. Rollover больше не
    # может записать старый XP/level поверх только что выданного начисления.
    async with _day_locks[user_id]:
        async with _xp_locks[user_id]:
            applied = await _apply_xp(user_id, amount, 1 if count_quest else 0, result)
    if not applied:
        return result
    hp_left, killed = await boss_mod.deal_damage(user_id, result.amount)
    result.boss_hp_left = hp_left
    result.boss_killed = killed
    return result


async def _apply_xp(user_id: int, base_amount: int, inc: int, result: XpResult) -> bool:
    for _ in range(_CAS_ATTEMPTS):
        fresh = await db.get_user(user_id)
        if fresh is None:
            return False
        amount = int(base_amount * config.PREMIUM_XP_MULT) if is_premium(fresh) else base_amount
        level = fresh["level"]
        xp = fresh["xp"] + amount
        old_rank = config.rank_for_level(level)
        stat_gains: dict[str, int] = {}
        levels_gained: list[tuple[int, str, int]] = []
        hp = None
        while xp >= config.xp_to_next(level):
            xp -= config.xp_to_next(level)
            level += 1
            stat = random.choice(config.STATS)
            gain = random.choices([1, 2], weights=[80, 20])[0]
            stat_gains[stat] = stat_gains.get(stat, 0) + gain
            hp = fresh["max_hp"]
            levels_gained.append((level, config.STAT_FULL[stat], gain))
        absolute = {"level": level, "xp": xp}
        if hp is not None:
            absolute["hp"] = hp
        applied = await db.compare_and_set_user(
            user_id,
            expect={"level": fresh["level"], "xp": fresh["xp"]},
            absolute=absolute,
            increments={"weekly_xp": amount, "weekly_done": inc, "total_done": inc, **stat_gains},
        )
        if not applied:
            continue
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
