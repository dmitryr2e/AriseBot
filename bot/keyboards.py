"""Клавиатуры: меню Системы, выбор пояса, апселл-кнопки, кнопка «Поделиться»."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from bot import config, share, timeutil

BTN_QUESTS = "⚔ Квесты"
BTN_REPORT = "📝 Отчёт"
BTN_PROFILE = "👤 Статус"
BTN_BOSS = "🐉 Босс"
BTN_RATING = "🏆 Рейтинг"
BTN_MORE = "⚙ Ещё"
BUTTON_COMMANDS = {BTN_QUESTS: "quests", BTN_REPORT: "report", BTN_PROFILE: "profile", BTN_BOSS: "boss", BTN_RATING: "rating", BTN_MORE: "help"}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_QUESTS), KeyboardButton(text=BTN_REPORT)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_BOSS)],
            [KeyboardButton(text=BTN_RATING), KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Система ждёт твоих действий…",
    )


UPSELL_PREMIUM = "premium"
UPSELL_REVIVE = "revive"
UPSELL_FREEZE = "freeze"

_UPSELL_BUTTONS: dict[str, tuple[str, str, int]] = {
    UPSELL_PREMIUM: ("👑 Статус «Восходящий»", "premium30", config.PREMIUM_PRICE_STARS),
    UPSELL_REVIVE: ("💀 Воскрешение", "revive", config.REVIVE_PRICE_STARS),
    UPSELL_FREEZE: ("🧊 Заморозка серии", "freeze", config.FREEZE_PRICE_STARS),
}


def upsell(*keys: str | None) -> InlineKeyboardMarkup | None:
    rows = []
    for key in keys:
        button = _UPSELL_BUTTONS.get(key or "")
        if button is None:
            continue
        label, payload, price = button
        rows.append([InlineKeyboardButton(text=f"{label} · {price} ⭐", callback_data=f"buy:{payload}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


SHARE_LABEL = "📣 Поделиться"


def share_button(user_id: int, text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=SHARE_LABEL, url=share.share_url(user_id, text))


def share_kb(user_id: int, text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[share_button(user_id, text)]])


def message_markup(*, upsell_key: str | None = None, share_for: tuple[int, str] | None = None) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    offer = upsell(upsell_key)
    if offer is not None:
        rows.extend(offer.inline_keyboard)
    if share_for is not None:
        rows.append([share_button(*share_for)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def onboarding_stop() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔕 Не напоминать", callback_data="onb:stop")]])


def timezone_menu() -> InlineKeyboardMarkup:
    zones = timeutil.COMMON_ZONES
    rows = [[InlineKeyboardButton(text=label, callback_data=f"tz:{tz}") for label, tz in zones[i : i + 2]] for i in range(0, len(zones), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)
