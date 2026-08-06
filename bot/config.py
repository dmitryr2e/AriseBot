"""Конфигурация бота."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent


def _load_env_files() -> None:
    """Подхватываем переменные из .env-файлов проекта (без сторонних зависимостей)."""
    root = BASE_DIR.parent
    for name in (".env", ".env.local", ".env.development.local"):
        path = root / name
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_env_files()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "SystemAriseBot")

# ---- Юридические ссылки (лендинг) и контакт поддержки ----
SITE_URL = os.getenv("SITE_URL", "https://sololevelingbot.vercel.app").rstrip("/")
PRIVACY_URL = f"{SITE_URL}/privacy"
TERMS_URL = f"{SITE_URL}/terms"
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@SystemAriseSupport")

# ---- Админы (id через запятую в ADMIN_IDS) ----
ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

# ---- Gemini (ИИ-отчёты) ----
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Основная: быстрая, отличный JSON, ~500 бесплатных запросов/день.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Запасные (по порядку): Gemma — огромный дневной лимит (~14k/день),
# затем прошлое поколение flash-lite на случай недоступности обеих.
GEMINI_FALLBACK_MODELS = [
    "gemma-4-26b-a4b-it",
    "gemini-3.5-flash-lite",
]

# Часовой пояс для расчёта "дня" и расписаний
TZ_NAME = os.getenv("BOT_TZ", "Europe/Moscow")
TZ = ZoneInfo(TZ_NAME)

# БД живёт в data/ (volume в Docker, вне git). Каталог создаётся при старте.
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", str(BASE_DIR.parent / "data")))
DB_PATH = os.getenv("BOT_DB_PATH", str(DATA_DIR / "hunter.db"))

# Каталог для локальных бэкапов БД
BACKUP_DIR = Path(os.getenv("BOT_BACKUP_DIR", str(DATA_DIR / "backups")))
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "28"))  # ретеншн: 28 копий (~7 суток при 6ч)
# Fernet-ключ для шифрования дампов БД. Пустое значение = бэкапы отключены:
# класть на диск незашифрованную копию базы с токенами и платежами нельзя.
BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "")

FONT_PATH = BASE_DIR / "assets" / "RussoOne-Regular.ttf"

# ---- Игровой баланс ----
DAILY_QUEST_COUNT = 4          # случайных ежедневных квестов в день
HP_MAX = 100
HP_PENALTY_PER_MISS = 8        # урон за каждый невыполненный квест дня
DEATH_HP_RESTORE_RATIO = 0.5   # с каким HP воскресает персонаж

# Дни полного отсутствия (охотник не заходил / бот лежал): квесты за них
# не выдавались, поэтому наказываем отдельным фиксированным уроном.
SKIPPED_DAY_DAMAGE = 10        # урон за каждый пропущенный день
SKIPPED_DAMAGE_MAX_DAYS = 3    # но не больше, чем за N дней (защита от даунтайма)

FREE_CUSTOM_QUESTS = 3         # лимит своих квестов без премиума
PREMIUM_CUSTOM_QUESTS = 10

# --- ИИ-отчёты ---
FREE_REPORTS_PER_DAY = 1
PREMIUM_REPORTS_PER_DAY = 3
REPORT_MAX_XP = 120            # потолок XP за один отчёт
REPORT_MIN_LEN = 20            # минимальная длина текста отчёта

# --- Премиум ---
PREMIUM_XP_MULT = 1.5          # множитель XP для премиума
PREMIUM_PRICE_STARS = 149      # Stars за 30 дней
PREMIUM_DAYS = 30
REVIVE_PRICE_STARS = 49        # мгновенное воскрешение без потери уровня
FREEZE_PRICE_STARS = 25        # заморозка стрика на 1 пропуск

# Ключи апселл-офферов. Живут здесь, а не в keyboards, потому что их
# возвращает bot/render.py: рендер обязан остаться чистым и без aiogram.
# Клавиатуру по ключу собирает keyboards.upsell().
UPSELL_PREMIUM = "premium"
UPSELL_REVIVE = "revive"
UPSELL_FREEZE = "freeze"

# --- Рефералы ---
REF_BONUS_XP = 100             # бонус XP приглашённому и пригласившему
REF_PREMIUM_THRESHOLD = 10     # друзей для премиума на неделю
REF_PREMIUM_DAYS = 7

# --- Босс недели ---
BOSS_BASE_HP = 5000            # базовое HP босса (масштабируется от игроков)
BOSS_HP_PER_HUNTER = 400
BOSS_REWARD_XP = 150           # награда каждому участнику при победе
BOSS_TOP_REWARD_XP = 300       # бонус топ-3 дамагерам

# --- Вехи серии: день серии -> (бонус XP, подаренные заморозки) ---
STREAK_MILESTONES: dict[int, tuple[int, int]] = {
    3: (50, 0),
    7: (100, 1),
    14: (200, 0),
    30: (500, 1),
    60: (1000, 1),
    100: (2000, 2),
}

# --- Врата (случайный бонус-квест дня) ---
GATE_CHANCE = 0.25             # вероятность открытия врат при выдаче квестов

DEFAULT_REMINDER = "20:00"     # напоминание по умолчанию

# --- Умные уведомления ---
DEADLINE_HOUR = 22             # вечерний «стрик под угрозой» (у кого серия >= 3)
DEADLINE_MIN_STREAK = 3
WINBACK_AFTER_DAYS = 3         # win-back после N дней тишины
WINBACK_BONUS_XP = 50          # бонус за возвращение
BOSS_LOW_HP_RATIO = 0.15       # порог «босс при смерти» для общей рассылки

WEEKLY_REPORT_DAY = "sun"      # день недельного отчёта
WEEKLY_REPORT_HOUR = 21

# --- Онбординг-цепочка (дни 1-3 после регистрации) ---
ONBOARDING_STOP = -1           # users.onboarding_day: охотник отписался от подсказок

STATS = ("strength", "intelligence", "endurance", "agility", "charisma")

STAT_LABELS = {
    "strength": "СИЛ",
    "intelligence": "ИНТ",
    "endurance": "ВЫН",
    "agility": "ЛВК",
    "charisma": "ХАР",
}

STAT_FULL = {
    "strength": "Сила",
    "intelligence": "Интеллект",
    "endurance": "Выносливость",
    "agility": "Ловкость",
    "charisma": "Харизма",
}

# Ранги: минимальный уровень -> ранг
RANK_THRESHOLDS = [
    (60, "S"),
    (45, "A"),
    (30, "B"),
    (20, "C"),
    (10, "D"),
    (1, "E"),
]


def rank_for_level(level: int) -> str:
    for threshold, rank in RANK_THRESHOLDS:
        if level >= threshold:
            return rank
    return "E"


def xp_to_next(level: int) -> int:
    """Сколько опыта нужно для перехода с level на level+1."""
    return int(100 * (level ** 1.25))
