"""Слой работы с SQLite (aiosqlite)."""
from pathlib import Path

import aiosqlite

from bot import ai, config, sqlsafe

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT DEFAULT '',
    first_name      TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    level           INTEGER DEFAULT 1,
    xp              INTEGER DEFAULT 0,
    hp              INTEGER DEFAULT 100,
    max_hp          INTEGER DEFAULT 100,
    strength        INTEGER DEFAULT 5,
    intelligence    INTEGER DEFAULT 5,
    endurance       INTEGER DEFAULT 5,
    agility         INTEGER DEFAULT 5,
    charisma        INTEGER DEFAULT 5,
    streak          INTEGER DEFAULT 0,
    best_streak     INTEGER DEFAULT 0,
    is_premium      INTEGER DEFAULT 0,
    reminder_time    TEXT DEFAULT '20:00',
    last_daily_date TEXT DEFAULT '',
    weekly_xp       INTEGER DEFAULT 0,
    weekly_done     INTEGER DEFAULT 0,
    deaths          INTEGER DEFAULT 0
);
-- По reminder_time бьёт users_with_reminder_progress из планировщика — каждую минуту.
CREATE INDEX IF NOT EXISTS idx_users_reminder ON users(reminder_time);

CREATE TABLE IF NOT EXISTS quests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    stat        TEXT NOT NULL,
    xp          INTEGER NOT NULL,
    quest_date  TEXT NOT NULL,
    is_custom   INTEGER DEFAULT 0,
    done        INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_quests_user_date ON quests(user_id, quest_date);
-- Агрегат по дню (quests_progress_for_date) фильтрует только по дате,
-- а idx_quests_user_date с ведущим user_id для этого не годится.
CREATE INDEX IF NOT EXISTS idx_quests_date ON quests(quest_date, user_id);

CREATE TABLE IF NOT EXISTS custom_quests (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    title    TEXT NOT NULL,
    stat     TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    report_date TEXT NOT NULL,
    text        TEXT NOT NULL,
    xp_awarded  INTEGER DEFAULT 0,
    verdict     TEXT DEFAULT '',
    -- SHA-256 нормализованного текста (bot.ai.fingerprint_report), AUDIT 2.4.
    -- NULL для строк, записанных до этой миграции — уникальный индекс ниже
    -- трактует несколько NULL как различные значения (не конфликтуют между
    -- собой), так что старые данные не ломают дедуп новых отчётов.
    fingerprint TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_user_date ON reports(user_id, report_date);

CREATE TABLE IF NOT EXISTS bosses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    week_key    TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    max_hp      INTEGER NOT NULL,
    hp          INTEGER NOT NULL,
    defeated    INTEGER DEFAULT 0,
    rewarded    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS boss_damage (
    boss_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    damage   INTEGER DEFAULT 0,
    PRIMARY KEY (boss_id, user_id)
);
-- PK начинается с boss_id, поэтому выборки/удаления по одному охотнику
-- (delete_user_data, JOIN в топе урона) без этого индекса шли сканом.
CREATE INDEX IF NOT EXISTS idx_boss_damage_user ON boss_damage(user_id);

CREATE TABLE IF NOT EXISTS achievements (
    user_id     INTEGER NOT NULL,
    code        TEXT NOT NULL,
    unlocked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    charge_id    TEXT UNIQUE NOT NULL,
    user_id      INTEGER NOT NULL,
    payload      TEXT NOT NULL,
    amount_stars INTEGER NOT NULL,
    refunded     INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
"""

# Новые колонки users для мягкой миграции существующих БД
_USER_MIGRATIONS = {
    "premium_until": "TEXT DEFAULT ''",
    "referred_by": "INTEGER DEFAULT 0",
    "ref_count": "INTEGER DEFAULT 0",
    "reports_today": "INTEGER DEFAULT 0",
    "streak_freezes": "INTEGER DEFAULT 0",
    "total_done": "INTEGER DEFAULT 0",
    "total_reports": "INTEGER DEFAULT 0",
    "hide_in_rating": "INTEGER DEFAULT 0",
    "last_seen": "TEXT DEFAULT ''",        # дата последней активности (YYYY-MM-DD)
    "winback_sent": "INTEGER DEFAULT 0",  # отправлено ли win-back сообщение
    "ai_notice_seen": "INTEGER DEFAULT 0",  # показан ли дисклеймер об обработке в Gemini
    # Дата (YYYY-MM-DD), до конца которой охотник «при смерти»: HP на нуле,
    # но уровень ещё не потерян. Пустая строка — обычное состояние.
    "dying_until": "TEXT DEFAULT ''",
    # Часовой пояс IANA (Europe/Moscow). Пустая строка = пояс не выбран, день
    # считается по config.TZ — так старые строки не требуют бэкфилла.
    "tz": "TEXT DEFAULT ''",
    # Когда пояс меняли последний раз (UTC, ISO). Смена пояса вперёд мгновенно
    # «переводит» охотника в следующий день, поэтому её приходится ограничивать
    # по частоте — иначе прыжками через пояса накручивалась бы серия.
    "tz_changed_at": "TEXT DEFAULT ''",
    # Номер последнего отправленного шага онбординг-цепочки (0 — ни одного).
    # -1 (config.ONBOARDING_STOP) — охотник отписался, шаги больше не шлём.
    "onboarding_day": "INTEGER DEFAULT 0",
}

_BOSS_MIGRATIONS = {
    "low_hp_notified": "INTEGER DEFAULT 0",  # разослано ли «босс при смерти»
}

# Мягкая миграция для reports: на старой БД колонки ещё нет, ALTER TABLE
# без DEFAULT даёт NULL для существующих строк (см. коммент к _SCHEMA).
_REPORTS_MIGRATIONS = {
    "fingerprint": "TEXT",
}

_db: aiosqlite.Connection | None = None


async def init_db() -> aiosqlite.Connection:
    global _db
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    # WAL + busy_timeout: устойчивость к конкурентным записям
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript(_SCHEMA)
    # Мягкая миграция: добавляем недостающие колонки
    for table, migrations in (
        ("users", _USER_MIGRATIONS),
        ("bosses", _BOSS_MIGRATIONS),
        ("reports", _REPORTS_MIGRATIONS),
    ):
        cur = await _db.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in await cur.fetchall()}
        for col, decl in migrations.items():
            if col not in existing:
                await _db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Индексы по колонкам из мягкой миграции создаём только здесь: в _SCHEMA
    # они бы падали, потому что на старой БД колонки ещё не существует.
    # По tz группируются все фоновые джобы (rollover, напоминания, дедлайн).
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_users_tz ON users(tz)")

    # Дедуп повторных отчётов за конкретный игровой день
    await _db.execute("DROP INDEX IF EXISTS idx_reports_user_fingerprint")
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_user_date_fingerprint "
        "ON reports(user_id, report_date, fingerprint) "
        "WHERE fingerprint IS NOT NULL"
    )
    await _db.commit()
    return _db


def db() -> aiosqlite.Connection:
    assert _db is not None, "DB not initialized"
    return _db


async def close_db() -> None:
    if _db is not None:
        await _db.close()


# ---------- users ----------

async def get_user(user_id: int) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return await cur.fetchone()


async def create_user(user_id: int, username: str, first_name: str) -> None:
    await db().execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, reminder_time) "
        "VALUES (?, ?, ?, ?)",
        (user_id, username or "", first_name or "", config.DEFAULT_REMINDER),
    )
    await db().commit()


async def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    # Имена колонок уходят в запрос строкой — их нельзя забиндить параметром,
    # поэтому единственная защита от инъекции здесь — allowlist (bot/sqlsafe.py).
    sqlsafe.require_user_columns(fields)
    cols = ", ".join(f"{k} = ?" for k in fields)
    await db().execute(
        f"UPDATE users SET {cols} WHERE user_id = ?",
        (*fields.values(), user_id),
    )
    await db().commit()


async def increment_user(user_id: int, **deltas: int) -> None:
    """Инкрементально изменить счётчики: SET col = col + ?.

    В отличие от update_user не затирает значение, посчитанное в Python из
    возможно устаревшей строки, поэтому безопасно при конкурентных вызовах.
    """
    deltas = {k: v for k, v in deltas.items() if v}
    if not deltas:
        return
    sqlsafe.require_user_columns(deltas)
    cols = ", ".join(f"{k} = {k} + ?" for k in deltas)
    await db().execute(
        f"UPDATE users SET {cols} WHERE user_id = ?",
        (*deltas.values(), user_id),
    )
    await db().commit()


async def increment_and_get(user_id: int, column: str, delta: int = 1) -> int:
    """Инкремент одной колонки с возвратом НОВОГО значения — одним запросом.

    Отдельные increment_user + get_user здесь не годятся: при двух
    одновременных вербовках обе перечитывающие стороны могли увидеть уже
    дважды увеличенный ref_count, и точное сравнение с порогом
    (`== REF_PREMIUM_THRESHOLD`) не срабатывало ни у одной. RETURNING отдаёт
    каждому вызову собственное значение, поэтому порог пересекается ровно раз.

    Возвращает 0, если строки нет.
    """
    sqlsafe.require_user_columns([column])
    cur = await db().execute(
        f"UPDATE users SET {column} = {column} + ? WHERE user_id = ? RETURNING {column}",
        (delta, user_id),
    )
    row = await cur.fetchone()
    await db().commit()
    return row[column] if row else 0


async def claim_referral(new_user_id: int, referrer_id: int) -> bool:
    """Атомарно закрепить вербовщика за новичком.

    True — связь записана именно этим вызовом, бонусы нужно начислить.
    False — вербовщик у новичка уже есть либо это попытка привести самого
    себя.

    Шаблон тот же, что у claim_winback: право занимается ДО начисления.
    /start обрабатывается только для новых пользователей, но два быстрых
    нажатия по одной ссылке успевали пройти проверку «пользователя нет»
    одновременно (INSERT OR IGNORE молча гасил второй INSERT), и бонус
    выдавался дважды обеим сторонам, а ref_count рос на 2 с одного новичка.
    """
    if referrer_id == new_user_id:
        return False
    cur = await db().execute(
        "UPDATE users SET referred_by = ? "
        "WHERE user_id = ? AND COALESCE(referred_by, 0) = 0",
        (referrer_id, new_user_id),
    )
    await db().commit()
    return cur.rowcount > 0


async def compare_and_set_user(
    user_id: int,
    expect: dict,
    absolute: dict | None = None,
    increments: dict | None = None,
) -> bool:
    """Одним атомарным UPDATE записать абсолютные поля и инкременты.

    Запись применяется только если поля из expect всё ещё равны прочитанным
    ранее значениям (оптимистичная блокировка). Возвращает False, если строку
    успели изменить — вызывающий должен перечитать её и повторить попытку.

    Нужно там, где новое значение нельзя выразить инкрементом (уровень и
    остаток XP считаются циклом вычитания порогов), а соединение с SQLite
    одно на процесс, поэтому длинную транзакцию открыть нельзя.
    """
    # В запрос строкой уходят и SET-, и WHERE-идентификаторы, поэтому
    # проверяем все три словаря разом.
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
    cur = await db().execute(
        f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?{where}",
        (*params, user_id, *expect.values()),
    )
    await db().commit()
    return cur.rowcount == 1


async def all_users() -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users")
    return await cur.fetchall()


async def users_with_reminder(hhmm: str) -> list[aiosqlite.Row]:
    cur = await db().execute("SELECT * FROM users WHERE reminder_time = ?", (hhmm,))
    return await cur.fetchall()


# Подзапрос-агрегат «квесты за дату» + LEFT JOIN: у пользователя без квестов
# строки в агрегате нет, поэтому COALESCE приводит его к (0, 0).
_PROGRESS_JOIN = """
LEFT JOIN (
    SELECT user_id, COUNT(*) AS total, SUM(done) AS done
    FROM quests WHERE quest_date = ? GROUP BY user_id
) q ON q.user_id = u.user_id
"""
_PROGRESS_COLS = (
    "COALESCE(q.total, 0) AS quests_total, COALESCE(q.done, 0) AS quests_done"
)


async def quests_progress_for_date(date: str) -> dict[int, tuple[int, int]]:
    """{user_id: (всего, сделано)} по квестам за дату — одним запросом.

    Пользователей без квестов на эту дату в словаре НЕТ (трактовать как (0, 0)).
    Нужна, чтобы фоновые джобы не дёргали quests_for_date в цикле по базе.
    """
    cur = await db().execute(
        "SELECT user_id, COUNT(*) AS total, SUM(done) AS done "
        "FROM quests WHERE quest_date = ? GROUP BY user_id",
        (date,),
    )
    return {row["user_id"]: (row["total"], row["done"]) for row in await cur.fetchall()}


async def set_timezone(user_id: int, tz_name: str, changed_at: str) -> bool:
    """Сменить пояс, если он действительно другой.

    Возвращает False, когда пояс совпадает с текущим: тогда не нужно ни
    занимать суточный лимит смены, ни писать в базу. Проверка сделана в SQL,
    чтобы два одновременных нажатия кнопки не прошли оба.
    """
    cur = await db().execute(
        "UPDATE users SET tz = ?, tz_changed_at = ? "
        "WHERE user_id = ? AND COALESCE(tz, '') != ?",
        (tz_name, changed_at, user_id, tz_name),
    )
    await db().commit()
    return cur.rowcount > 0


async def distinct_timezones() -> list[str]:
    """Все значения users.tz, встречающиеся в базе (включая '' — пояс по умолчанию).

    Фоновые джобы обходят пояса, а не пользователей: локальные дату и время
    можно посчитать один раз на пояс, а фильтрацию отдать SQL. Пустая строка
    остаётся в списке как отдельная группа — это охотники без выбранного пояса.
    """
    cur = await db().execute("SELECT DISTINCT COALESCE(tz, '') AS tz FROM users")
    return [row["tz"] for row in await cur.fetchall()]


async def users_needing_rollover(tz_name: str, local_date: str) -> list[aiosqlite.Row]:
    """Охотники этого пояса, у которых локальный день уже сменился.

    Условие `last_daily_date != local_date` самодостаточно: если день закрыл
    хендлер, строка сюда уже не попадёт, а если бот лежал сутки — попадёт и
    догонит пропуск. Поэтому джоб можно запускать сколь угодно часто.
    """
    cur = await db().execute(
        "SELECT * FROM users WHERE COALESCE(tz, '') = ? AND last_daily_date != ?",
        (tz_name, local_date),
    )
    return await cur.fetchall()


async def users_with_reminder_progress(
    hhmm: str, date: str, tz_name: str
) -> list[aiosqlite.Row]:
    """Пользователи пояса tz_name с напоминанием на hhmm и прогрессом за date.

    hhmm и date — уже локальные для этого пояса (см. distinct_timezones).
    К обычным колонкам users добавляются quests_total и quests_done.
    """
    cur = await db().execute(
        f"SELECT u.*, {_PROGRESS_COLS} FROM users u {_PROGRESS_JOIN} "
        "WHERE u.reminder_time = ? AND COALESCE(u.tz, '') = ?",
        (date, hhmm, tz_name),
    )
    return await cur.fetchall()


async def users_in_streak_danger(
    date: str, min_streak: int, tz_name: str
) -> list[aiosqlite.Row]:
    """Охотники пояса tz_name с серией >= min_streak и незакрытыми квестами за date.

    Фильтры по серии и по наличию незакрытых квестов сделаны в SQL, чтобы
    вечерний джоб не тянул всю базу ради нескольких строк.
    """
    cur = await db().execute(
        f"SELECT u.*, {_PROGRESS_COLS} FROM users u {_PROGRESS_JOIN} "
        "WHERE u.streak >= ? AND COALESCE(u.tz, '') = ? "
        "AND COALESCE(q.total, 0) > COALESCE(q.done, 0)",
        (date, min_streak, tz_name),
    )
    return await cur.fetchall()


async def touch_last_seen(user_id: int, today: str) -> None:
    """Отметить активность пользователя (и сбросить win-back флаг)."""
    await db().execute(
        "UPDATE users SET last_seen = ?, winback_sent = 0 "
        "WHERE user_id = ? AND last_seen != ?",
        (today, user_id, today),
    )
    await db().commit()


async def inactive_users(cutoff_date: str) -> list[aiosqlite.Row]:
    """Пользователи, не появлявшиеся с cutoff_date и без win-back сообщения."""
    cur = await db().execute(
        "SELECT * FROM users WHERE winback_sent = 0 AND last_seen != '' AND last_seen <= ?",
        (cutoff_date,),
    )
    return await cur.fetchall()


async def claim_winback(user_id: int) -> bool:
    """Атомарно занять право на отправку win-back.

    True — флаг выставлен именно этим вызовом, сообщение нужно отправить.
    False — кто-то уже занял (параллельный джоб или повторный запуск).

    Флаг ставится ДО отправки: лучше не отправить сообщение при сбое сети,
    чем разослать его повторно всей базе после падения джоба.
    """
    cur = await db().execute(
        "UPDATE users SET winback_sent = 1 WHERE user_id = ? AND winback_sent = 0",
        (user_id,),
    )
    await db().commit()
    return cur.rowcount > 0


async def claim_onboarding_step(user_id: int, step: int) -> bool:
    """Атомарно занять шаг онбординг-цепочки (1..3).

    True — шаг наш, сообщение нужно отправить. False — шаг уже заняли
    (повторный запуск джоба) или охотник успел отписаться (onboarding_day
    тогда не равен step - 1). Условие сразу и продвигает цепочку по порядку,
    и делает вызов идемпотентным — как claim_winback, флаг ставится ДО
    отправки, чтобы падение джоба не привело к повторной рассылке.
    """
    cur = await db().execute(
        "UPDATE users SET onboarding_day = ? WHERE user_id = ? AND onboarding_day = ?",
        (step, user_id, step - 1),
    )
    await db().commit()
    return cur.rowcount > 0


async def users_pending_onboarding(step: int, tz_name: str) -> list[aiosqlite.Row]:
    """Охотники пояса tz_name, у которых пройден шаг step - 1, но не step.

    Проверка «прошло ли достаточно дней с регистрации» здесь не делается —
    для неё нужно распарсить created_at и посчитать локальную дату, это
    остаётся на стороне вызывающего (bot/scheduler.py).
    """
    cur = await db().execute(
        "SELECT * FROM users WHERE onboarding_day = ? AND COALESCE(tz, '') = ?",
        (step - 1, tz_name),
    )
    return await cur.fetchall()


async def delete_user_data(user_id: int) -> None:
    """Каскадно удалить все данные пользователя (право на забвение).

    Платежи не удаляются, а обезличиваются: user_id обнуляется, чтобы
    сохранить финансовую отчётность без привязки к субъекту.
    """
    conn = db()
    try:
        await conn.execute("BEGIN")
        await conn.execute("DELETE FROM quests WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM custom_quests WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM reports WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM achievements WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM boss_damage WHERE user_id = ?", (user_id,))
        # Обезличиваем платежи вместо удаления
        await conn.execute("UPDATE payments SET user_id = 0 WHERE user_id = ?", (user_id,))
        # Разрываем реферальные связи, чтобы не осталось следа об удалённом
        await conn.execute(
            "UPDATE users SET referred_by = 0 WHERE referred_by = ?", (user_id,)
        )
        await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def count_where(table: str, where: str = "1=1", params: tuple = ()) -> int:
    # Имя таблицы подставляется в запрос строкой — сверяем с allowlist.
    sqlsafe.require_table(table)
    cur = await db().execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params)
    return (await cur.fetchone())["c"]


async def mark_boss_low_hp_notified(boss_id: int) -> None:
    await db().execute("UPDATE bosses SET low_hp_notified = 1 WHERE id = ?", (boss_id,))
    await db().commit()


# ---------- quests ----------

async def quests_for_date(user_id: int, date: str) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT * FROM quests WHERE user_id = ? AND quest_date = ? ORDER BY is_custom, id",
        (user_id, date),
    )
    return await cur.fetchall()


async def insert_quests(rows: list[tuple]) -> None:
    """rows: (user_id, title, stat, xp, quest_date, is_custom)"""
    await db().executemany(
        "INSERT INTO quests (user_id, title, stat, xp, quest_date, is_custom) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    await db().commit()


async def get_quest(quest_id: int) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM quests WHERE id = ?", (quest_id,))
    return await cur.fetchone()


async def mark_quest_done(quest_id: int) -> bool:
    """Атомарно пометить квест выполненным. False — уже был выполнен (двойной тап)."""
    cur = await db().execute(
        "UPDATE quests SET done = 1 WHERE id = ? AND done = 0", (quest_id,)
    )
    await db().commit()
    return cur.rowcount > 0


# ---------- custom quests ----------

async def custom_quests(user_id: int) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT * FROM custom_quests WHERE user_id = ? ORDER BY id", (user_id,)
    )
    return await cur.fetchall()


async def add_custom_quest(user_id: int, title: str, stat: str) -> None:
    await db().execute(
        "INSERT INTO custom_quests (user_id, title, stat) VALUES (?, ?, ?)",
        (user_id, title, stat),
    )
    await db().commit()


async def delete_custom_quest(user_id: int, cq_id: int) -> None:
    await db().execute(
        "DELETE FROM custom_quests WHERE id = ? AND user_id = ?", (cq_id, user_id)
    )
    await db().commit()


# ---------- reports (ИИ-отчёты) ----------

async def reports_count_today(user_id: int, date: str) -> int:
    cur = await db().execute(
        "SELECT COUNT(*) AS c FROM reports WHERE user_id = ? AND report_date = ?",
        (user_id, date),
    )
    row = await cur.fetchone()
    return row["c"]


async def report_is_duplicate(
    user_id: int,
    report_date: str,
    text: str,
) -> bool:
    """Проверить копию отчёта только за конкретный игровой день."""
    fingerprint = ai.fingerprint_report(text)

    cur = await db().execute(
        "SELECT 1 FROM reports "
        "WHERE user_id = ? AND report_date = ? AND fingerprint = ? "
        "LIMIT 1",
        (user_id, report_date, fingerprint),
    )
    return await cur.fetchone() is not None


async def add_report(
    user_id: int,
    report_date: str,
    text: str,
    xp: int,
    verdict: str,
) -> bool:
    """Записать отчёт. False означает дубль за тот же день."""
    fingerprint = ai.fingerprint_report(text)

    cur = await db().execute(
        "INSERT OR IGNORE INTO reports "
        "(user_id, report_date, text, xp_awarded, verdict, fingerprint) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id,
            report_date,
            text,
            xp,
            verdict,
            fingerprint,
        ),
    )
    await db().commit()
    return cur.rowcount > 0


# ---------- boss (босс недели) ----------

async def get_boss(week_key: str) -> aiosqlite.Row | None:
    cur = await db().execute("SELECT * FROM bosses WHERE week_key = ?", (week_key,))
    return await cur.fetchone()


async def create_boss(week_key: str, name: str, max_hp: int) -> aiosqlite.Row:
    await db().execute(
        "INSERT OR IGNORE INTO bosses (week_key, name, max_hp, hp) VALUES (?, ?, ?, ?)",
        (week_key, name, max_hp, max_hp),
    )
    await db().commit()
    return await get_boss(week_key)


async def damage_boss(boss_id: int, user_id: int, damage: int) -> int:
    """Нанести урон боссу; вернуть оставшееся HP."""
    await db().execute(
        "UPDATE bosses SET hp = MAX(0, hp - ?) WHERE id = ? AND defeated = 0",
        (damage, boss_id),
    )
    await db().execute(
        "INSERT INTO boss_damage (boss_id, user_id, damage) VALUES (?, ?, ?) "
        "ON CONFLICT(boss_id, user_id) DO UPDATE SET damage = damage + ?",
        (boss_id, user_id, damage, damage),
    )
    cur = await db().execute("SELECT hp FROM bosses WHERE id = ?", (boss_id,))
    row = await cur.fetchone()
    hp = row["hp"] if row else 0
    if hp <= 0:
        await db().execute("UPDATE bosses SET defeated = 1 WHERE id = ?", (boss_id,))
    await db().commit()
    return hp


async def boss_top_damagers(boss_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT bd.user_id, bd.damage, u.first_name, u.username "
        "FROM boss_damage bd JOIN users u ON u.user_id = bd.user_id "
        "WHERE bd.boss_id = ? ORDER BY bd.damage DESC LIMIT ?",
        (boss_id, limit),
    )
    return await cur.fetchall()


async def boss_participants(boss_id: int) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT user_id, damage FROM boss_damage WHERE boss_id = ?", (boss_id,)
    )
    return await cur.fetchall()


async def claim_boss_rewarded(boss_id: int) -> bool:
    """Атомарно занять право на выдачу наград за босса.

    True — флаг выставлен именно этим вызовом, награды нужно раздать.
    False — кто-то уже занял (мгновенная выдача после добивания против
    воскресного джоба, либо два параллельных таска на одного босса).

    Флаг ставится ДО начисления: двойной XP всей пачке участников хуже,
    чем недоданный XP при сбое посередине.
    """
    cur = await db().execute(
        "UPDATE bosses SET rewarded = 1 WHERE id = ? AND rewarded = 0", (boss_id,)
    )
    await db().commit()
    return cur.rowcount > 0


# ---------- rating ----------

async def weekly_top(limit: int = 10) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT user_id, first_name, username, level, weekly_xp, streak "
        "FROM users WHERE hide_in_rating = 0 AND weekly_xp > 0 "
        "ORDER BY weekly_xp DESC LIMIT ?",
        (limit,),
    )
    return await cur.fetchall()


async def weekly_position(user_id: int) -> tuple[int, int]:
    """(позиция, всего участников) в недельном рейтинге."""
    cur = await db().execute(
        "SELECT COUNT(*) AS c FROM users WHERE weekly_xp > "
        "(SELECT weekly_xp FROM users WHERE user_id = ?)",
        (user_id,),
    )
    ahead = (await cur.fetchone())["c"]
    cur = await db().execute("SELECT COUNT(*) AS c FROM users WHERE weekly_xp > 0")
    total = (await cur.fetchone())["c"]
    return ahead + 1, total


# ---------- payments (Telegram Stars) ----------

async def record_payment(
    charge_id: str, user_id: int, payload: str, amount_stars: int
) -> bool:
    """Идемпотентная запись платежа.

    True — платёж записан впервые (товар нужно выдать).
    False — этот charge_id уже был обработан (ретрай апдейта) — товар НЕ выдавать.
    """
    cur = await db().execute(
        "INSERT OR IGNORE INTO payments (charge_id, user_id, payload, amount_stars) "
        "VALUES (?, ?, ?, ?)",
        (charge_id, user_id, payload, amount_stars),
    )
    await db().commit()
    return cur.rowcount > 0


async def get_payment(charge_id: str) -> aiosqlite.Row | None:
    cur = await db().execute(
        "SELECT * FROM payments WHERE charge_id = ?", (charge_id,)
    )
    return await cur.fetchone()


async def user_payments(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )
    return await cur.fetchall()


async def mark_payment_refunded(charge_id: str) -> bool:
    """True — пометили как возвращённый (ранее возвращён не был)."""
    cur = await db().execute(
        "UPDATE payments SET refunded = 1 WHERE charge_id = ? AND refunded = 0",
        (charge_id,),
    )
    await db().commit()
    return cur.rowcount > 0


# ---------- achievements ----------

async def user_achievements(user_id: int) -> set[str]:
    cur = await db().execute(
        "SELECT code FROM achievements WHERE user_id = ?", (user_id,)
    )
    return {row["code"] for row in await cur.fetchall()}


async def unlock_achievement(user_id: int, code: str) -> bool:
    """True, если ачивка была разблокирована только что."""
    cur = await db().execute(
        "INSERT OR IGNORE INTO achievements (user_id, code) VALUES (?, ?)",
        (user_id, code),
    )
    await db().commit()
    return cur.rowcount > 0
