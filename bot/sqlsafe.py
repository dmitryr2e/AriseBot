"""Allowlist для идентификаторов в динамических SQL-запросах.

Значения SQLite биндит сам, а вот имена колонок и таблиц подставляются в
запрос обычной строкой (update_user, increment_user, compare_and_set_user,
count_where). Единственная защита от инъекции здесь — сверка со списком
известных идентификаторов.
"""

USER_COLUMNS = frozenset({
    "premium_until", "referred_by", "ref_count", "reports_today",
    "streak_freezes", "total_done", "total_reports", "hide_in_rating",
    "last_seen", "winback_sent", "ai_notice_seen", "dying_until",
    "tz", "tz_changed_at", "onboarding_day", "reminder_time",
    "last_daily_date", "hp", "streak", "best_streak", "level",
    "xp", "weekly_xp", "weekly_done", "deaths", "max_hp",
    "strength", "intelligence", "endurance", "agility", "charisma",
    "is_premium", "username", "first_name", "created_at",
})
TABLES = frozenset({
    "users", "quests", "custom_quests", "reports",
    "achievements", "bosses", "boss_damage", "payments",
})


def require_user_columns(columns) -> None:
    unknown = set(columns) - USER_COLUMNS
    if unknown:
        raise ValueError(f"Недопустимые поля users: {sorted(unknown)!r}")


def require_table(table: str) -> None:
    if table not in TABLES:
        raise ValueError(f"Недопустимая таблица: {table!r}")
