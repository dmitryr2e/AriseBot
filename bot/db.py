"""Слой работы с SQLite (aiosqlite)."""
from pathlib import Path

import aiosqlite

from bot import ai, config, sqlsafe

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '', first_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')), level INTEGER DEFAULT 1,
    xp INTEGER DEFAULT 0, hp INTEGER DEFAULT 100, max_hp INTEGER DEFAULT 100,
    strength INTEGER DEFAULT 5, intelligence INTEGER DEFAULT 5, endurance INTEGER DEFAULT 5,
    agility INTEGER DEFAULT 5, charisma INTEGER DEFAULT 5, streak INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
    reminder_time TEXT DEFAULT '20:00', last_daily_date TEXT DEFAULT '',
    weekly_xp INTEGER DEFAULT 0, weekly_done INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_reminder ON users(reminder_time);
CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL,
    stat TEXT NOT NULL, xp INTEGER NOT NULL, quest_date TEXT NOT NULL,
    is_custom INTEGER DEFAULT 0, done INTEGER DEFAULT 0, FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_quests_user_date ON quests(user_id, quest_date);
CREATE INDEX IF NOT EXISTS idx_quests_date ON quests(quest_date, user_id);
CREATE TABLE IF NOT EXISTS custom_quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, stat TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, report_date TEXT NOT NULL,
    text TEXT NOT NULL, xp_awarded INTEGER DEFAULT 0, verdict TEXT DEFAULT '', fingerprint TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_user_date ON reports(user_id, report_date);
CREATE TABLE IF NOT EXISTS bosses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, week_key TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    max_hp INTEGER NOT NULL, hp INTEGER NOT NULL, defeated INTEGER DEFAULT 0, rewarded INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS boss_damage (
    boss_id INTEGER NOT NULL, user_id INTEGER NOT NULL, damage INTEGER DEFAULT 0,
    PRIMARY KEY (boss_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_boss_damage_user ON boss_damage(user_id);
CREATE TABLE IF NOT EXISTS achievements (
    user_id INTEGER NOT NULL, code TEXT NOT NULL, unlocked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, code)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, charge_id TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
    payload TEXT NOT NULL, amount_stars INTEGER NOT NULL, refunded INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
"""

_USER_MIGRATIONS = {
    "premium_until": "TEXT DEFAULT ''", "referred_by": "INTEGER DEFAULT 0", "ref_count": "INTEGER DEFAULT 0",
    "reports_today": "INTEGER DEFAULT 0", "streak_freezes": "INTEGER DEFAULT 0", "total_done": "INTEGER DEFAULT 0",
    "total_reports": "INTEGER DEFAULT 0", "hide_in_rating": "INTEGER DEFAULT 0", "last_seen": "TEXT DEFAULT ''",
    "winback_sent": "INTEGER DEFAULT 0", "ai_notice_seen": "INTEGER DEFAULT 0", "dying_until": "TEXT DEFAULT ''",
    "tz": "TEXT DEFAULT ''", "tz_changed_at": "TEXT DEFAULT ''", "onboarding_day": "INTEGER DEFAULT 0",
}
_BOSS_MIGRATIONS = {"low_hp_notified": "INTEGER DEFAULT 0"}
_REPORTS_MIGRATIONS = {"fingerprint": "TEXT"}
_db: aiosqlite.Connection | None = None


async def init_db() -> aiosqlite.Connection:
    global _db
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript(_SCHEMA)
    for table, migrations in (("users", _USER_MIGRATIONS), ("bosses", _BOSS_MIGRATIONS), ("reports", _REPORTS_MIGRATIONS)):
        cur = await _db.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in await cur.fetchall()}
        for col, decl in migrations.items():
            if col not in existing:
                await _db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_users_tz ON users(tz)")
    await _db.execute("DROP INDEX IF EXISTS idx_reports_user_fingerprint")
    await _db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_user_date_fingerprint ON reports(user_id, report_date, fingerprint) WHERE fingerprint IS NOT NULL")
    await _db.commit()
    return _db


def db() -> aiosqlite.Connection:
    assert _db is not None, "DB not initialized"
    return _db


async def close_db() -> None:
    if _db is not None:
        await _db.close()


async def get_user(user_id: int) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return await cur.fetchone()


async def create_user(user_id: int, username: str, first_name: str) -> None:
    await db().execute("INSERT OR IGNORE INTO users (user_id, username, first_name, reminder_time) VALUES (?, ?, ?, ?)", (user_id, username or "", first_name or "", config.DEFAULT_REMINDER))
    await db().commit()


async def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    sqlsafe.require_user_columns(fields)
    cols = ", ".join(f"{k} = ?" for k in fields)
    await db().execute(f"UPDATE users SET {cols} WHERE user_id = ?", (*fields.values(), user_id))
    await db().commit()


async def increment_user(user_id: int, **deltas: int) -> None:
    deltas = {k: v for k, v in deltas.items() if v}
    if not deltas:
        return
    sqlsafe.require_user_columns(deltas)
    cols = ", ".join(f"{k} = {k} + ?" for k in deltas)
    await db().execute(f"UPDATE users SET {cols} WHERE user_id = ?", (*deltas.values(), user_id))
    await db().commit()


async def increment_and_get(user_id: int, column: str, delta: int = 1) -> int:
    sqlsafe.require_user_columns([column])
    cur = await db().execute(f"UPDATE users SET {column} = {column} + ? WHERE user_id = ? RETURNING {column}", (delta, user_id))
    row = await cur.fetchone()
    await db().commit()
    return row[column] if row else 0


async def claim_referral(new_user_id: int, referrer_id: int) -> bool:
    if referrer_id == new_user_id:
        return False
    cur = await db().execute("UPDATE users SET referred_by = ? WHERE user_id = ? AND COALESCE(referred_by, 0) = 0", (referrer_id, new_user_id))
    await db().commit()
    return cur.rowcount > 0


async def compare_and_set_user(user_id: int, expect: dict, absolute: dict | None = None, increments: dict | None = None) -> bool:
    sqlsafe.require_user_columns({*expect, *(absolute or {}), *(increments or {})})
    sets: list[str] = []
    params: list = []
    for col, value in (absolute or {}).items():
        sets.append(f"{col} = ?")
        params.append(value)
    for col, delta in (increments or {}).items():
        sets.append(f"{col} = {col} + ?")
        params.append(delta)
    if not sets:
        return True
    where = "".join(f" AND {col} = ?" for col in expect)
    cur = await db().execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?{where}", (*params, user_id, *expect.values()))
    await db().commit()
    return cur.rowcount == 1


async def all_users() -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users")
    return await cur.fetchall()


async def users_with_reminder(hhmm: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE reminder_time = ?", (hhmm,))
    return await cur.fetchall()


_PROGRESS_JOIN = """
LEFT JOIN (SELECT user_id, COUNT(*) AS total, SUM(done) AS done FROM quests WHERE quest_date = ? GROUP BY user_id) q ON q.user_id = u.user_id
"""
_PROGRESS_COLS = "COALESCE(q.total, 0) AS quests_total, COALESCE(q.done, 0) AS quests_done"


async def quests_progress_for_date(date: str) -> dict[int, tuple[int, int]]:
    cur = await db().execute("SELECT user_id, COUNT(*) AS total, SUM(done) AS done FROM quests WHERE quest_date = ? GROUP BY user_id", (date,))
    return {row["user_id"]: (row["total"], row["done"]) for row in await cur.fetchall()}


async def set_timezone(user_id: int, tz_name: str, changed_at: str) -> bool:
    cur = await db().execute("UPDATE users SET tz = ?, tz_changed_at = ? WHERE user_id = ? AND COALESCE(tz, '') != ?", (tz_name, changed_at, user_id, tz_name))
    await db().commit()
    return cur.rowcount > 0


async def distinct_timezones() -> list[str]:
    cur = await db().execute("SELECT DISTINCT COALESCE(tz, '') AS tz FROM users")
    return [row["tz"] for row in await cur.fetchall()]


async def users_needing_rollover(tz_name: str, local_date: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE COALESCE(tz, '') = ? AND last_daily_date != ?", (tz_name, local_date))
    return await cur.fetchall()


async def users_with_reminder_progress(hhmm: str, date: str, tz_name: str) -> list[aiosqlite.Row]:
    cur = await db().execute(f"SELECT u.*, {_PROGRESS_COLS} FROM users u {_PROGRESS_JOIN} WHERE u.reminder_time = ? AND COALESCE(u.tz, '') = ?", (date, hhmm, tz_name))
    return await cur.fetchall()


async def users_in_streak_danger(date: str, min_streak: int, tz_name: str) -> list[aiosqlite.Row]:
    cur = await db().execute(f"SELECT u.*, {_PROGRESS_COLS} FROM users u {_PROGRESS_JOIN} WHERE u.streak >= ? AND COALESCE(u.tz, '') = ? AND COALESCE(q.total, 0) > COALESCE(q.done, 0)", (date, min_streak, tz_name))
    return await cur.fetchall()


async def touch_last_seen(user_id: int, today: str) -> None:
    await db().execute("UPDATE users SET last_seen = ?, winback_sent = 0 WHERE user_id = ? AND last_seen != ?", (today, user_id, today))
    await db().commit()


async def inactive_users(cutoff_date: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE winback_sent = 0 AND last_seen != '' AND last_seen <= ?", (cutoff_date,))
    return await cur.fetchall()


async def claim_winback(user_id: int) -> bool:
    cur = await db().execute("UPDATE users SET winback_sent = 1 WHERE user_id = ? AND winback_sent = 0", (user_id,))
    await db().commit()
    return cur.rowcount > 0


async def claim_onboarding_step(user_id: int, step: int) -> bool:
    cur = await db().execute("UPDATE users SET onboarding_day = ? WHERE user_id = ? AND onboarding_day = ?", (step, user_id, step - 1))
    await db().commit()
    return cur.rowcount > 0


async def users_pending_onboarding(step: int, tz_name: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE onboarding_day = ? AND COALESCE(tz, '') = ?", (step - 1, tz_name))
    return await cur.fetchall()


async def delete_user_data(user_id: int) -> None:
    conn = db()
    try:
        await conn.execute("BEGIN")
        for table in ("quests", "custom_quests", "reports", "achievements", "boss_damage"):
            await conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await conn.execute("UPDATE payments SET user_id = 0 WHERE user_id = ?", (user_id,))
        await conn.execute("UPDATE users SET referred_by = 0 WHERE referred_by = ?", (user_id,))
        await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def count_where(table: str, where: str = "1=1", params: tuple = ()) -> int:
    sqlsafe.require_table(table)
    cur = await db().execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params)
    return (await cur.fetchone())["c"]


async def mark_boss_low_hp_notified(boss_id: int) -> None:
    await db().execute("UPDATE bosses SET low_hp_notified = 1 WHERE id = ?", (boss_id,))
    await db().commit()


async def quests_for_date(user_id: int, date: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM quests WHERE user_id = ? AND quest_date = ? ORDER BY is_custom, id", (user_id, date))
    return await cur.fetchall()


async def insert_quests(rows: list[tuple]) -> None:
    await db().executemany("INSERT INTO quests (user_id, title, stat, xp, quest_date, is_custom) VALUES (?, ?, ?, ?, ?, ?)", rows)
    await db().commit()


async def get_quest(quest_id: int) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM quests WHERE id = ?", (quest_id,))
    return await cur.fetchone()


async def mark_quest_done(quest_id: int) -> bool:
    cur = await db().execute("UPDATE quests SET done = 1 WHERE id = ? AND done = 0", (quest_id,))
    await db().commit()
    return cur.rowcount > 0


async def custom_quests(user_id: int) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM custom_quests WHERE user_id = ? ORDER BY id", (user_id,))
    return await cur.fetchall()


async def add_custom_quest(user_id: int, title: str, stat: str) -> None:
    await db().execute("INSERT INTO custom_quests (user_id, title, stat) VALUES (?, ?, ?)", (user_id, title, stat))
    await db().commit()


async def delete_custom_quest(user_id: int, cq_id: int) -> None:
    await db().execute("DELETE FROM custom_quests WHERE id = ? AND user_id = ?", (cq_id, user_id))
    await db().commit()


async def reports_count_today(user_id: int, date: str) -> int:
    cur = await db().execute("SELECT COUNT(*) AS c FROM reports WHERE user_id = ? AND report_date = ?", (user_id, date))
    return (await cur.fetchone())["c"]


async def report_is_duplicate(user_id: int, report_date: str, text: str) -> bool:
    fingerprint = ai.fingerprint_report(text)
    cur = await db().execute("SELECT 1 FROM reports WHERE user_id = ? AND report_date = ? AND fingerprint = ? LIMIT 1", (user_id, report_date, fingerprint))
    return await cur.fetchone() is not None


async def add_report(user_id: int, report_date: str, text: str, xp: int, verdict: str) -> bool:
    fingerprint = ai.fingerprint_report(text)
    cur = await db().execute("INSERT OR IGNORE INTO reports (user_id, report_date, text, xp_awarded, verdict, fingerprint) VALUES (?, ?, ?, ?, ?, ?)", (user_id, report_date, text, xp, verdict, fingerprint))
    await db().commit()
    return cur.rowcount > 0


async def get_boss(week_key: str) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM bosses WHERE week_key = ?", (week_key,))
    return await cur.fetchone()


async def create_boss(week_key: str, name: str, max_hp: int) -> aiosqlite.Row:
    await db().execute("INSERT OR IGNORE INTO bosses (week_key, name, max_hp, hp) VALUES (?, ?, ?, ?)", (week_key, name, max_hp, max_hp))
    await db().commit()
    return await get_boss(week_key)


async def damage_boss(boss_id: int, user_id: int, damage: int) -> int:
    await db().execute("UPDATE bosses SET hp = MAX(0, hp - ?) WHERE id = ? AND defeated = 0", (damage, boss_id))
    await db().execute("INSERT INTO boss_damage (boss_id, user_id, damage) VALUES (?, ?, ?) ON CONFLICT(boss_id, user_id) DO UPDATE SET damage = damage + ?", (boss_id, user_id, damage, damage))
    cur = await db().execute("SELECT hp FROM bosses WHERE id = ?", (boss_id,))
    row = await cur.fetchone()
    hp = row["hp"] if row else 0
    if hp <= 0:
        await db().execute("UPDATE bosses SET defeated = 1 WHERE id = ?", (boss_id,))
    await db().commit()
    return hp


async def boss_top_damagers(boss_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT bd.user_id, bd.damage, u.first_name, u.username FROM boss_damage bd JOIN users u ON u.user_id = bd.user_id WHERE bd.boss_id = ? ORDER BY bd.damage DESC LIMIT ?", (boss_id, limit))
    return await cur.fetchall()


async def boss_participants(boss_id: int) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT user_id, damage FROM boss_damage WHERE boss_id = ?", (boss_id,))
    return await cur.fetchall()


async def claim_boss_rewarded(boss_id: int) -> bool:
    cur = await db().execute("UPDATE bosses SET rewarded = 1 WHERE id = ? AND rewarded = 0", (boss_id,))
    await db().commit()
    return cur.rowcount > 0


async def weekly_top(limit: int = 10) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT user_id, first_name, username, level, weekly_xp, streak FROM users WHERE hide_in_rating = 0 AND weekly_xp > 0 ORDER BY weekly_xp DESC LIMIT ?", (limit,))
    return await cur.fetchall()


async def weekly_position(user_id: int) -> tuple[int, int]:
    """Позиция и размер публичного рейтинга без скрытых игроков."""
    cur = await db().execute("SELECT COUNT(*) AS c FROM users WHERE hide_in_rating = 0 AND weekly_xp > (SELECT weekly_xp FROM users WHERE user_id = ?)", (user_id,))
    ahead = (await cur.fetchone())["c"]
    cur = await db().execute("SELECT COUNT(*) AS c FROM users WHERE hide_in_rating = 0 AND weekly_xp > 0", ())
    total = (await cur.fetchone())["c"]
    return ahead + 1, total


async def record_payment(charge_id: str, user_id: int, payload: str, amount_stars: int) -> bool:
    cur = await db().execute("INSERT OR IGNORE INTO payments (charge_id, user_id, payload, amount_stars) VALUES (?, ?, ?, ?)", (charge_id, user_id, payload, amount_stars))
    await db().commit()
    return cur.rowcount > 0


async def get_payment(charge_id: str) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM payments WHERE charge_id = ?", (charge_id,))
    return await cur.fetchone()


async def user_payments(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    return await cur.fetchall()


async def mark_payment_refunded(charge_id: str) -> bool:
    cur = await db().execute("UPDATE payments SET refunded = 1 WHERE charge_id = ? AND refunded = 0", (charge_id,))
    await db().commit()
    return cur.rowcount > 0


async def user_achievements(user_id: int) -> set[str]:
    cur = await db().execute("SELECT code FROM achievements WHERE user_id = ?", (user_id,))
    return {row["code"] for row in await cur.fetchall()}


async def unlock_achievement(user_id: int, code: str) -> bool:
    cur = await db().execute("INSERT OR IGNORE INTO achievements (user_id, code) VALUES (?, ?)", (user_id, code))
    await db().commit()
    return cur.rowcount > 0
