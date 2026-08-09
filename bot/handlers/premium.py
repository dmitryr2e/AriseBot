"""Хендлер: /premium — статус «Монарх» и разовые покупки через Telegram Stars (XTR)."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery

from bot import config, db, game, texts
from bot.handlers.helpers import load_user

router = Router()

_PRODUCTS = {
    "premium30": (texts.PAY_PREMIUM_TITLE, texts.PAY_PREMIUM_DESC, config.PREMIUM_PRICE_STARS),
    "revive": (texts.PAY_REVIVE_TITLE, texts.PAY_REVIVE_DESC, config.REVIVE_PRICE_STARS),
    "freeze": (texts.PAY_FREEZE_TITLE, texts.PAY_FREEZE_DESC, config.FREEZE_PRICE_STARS),
}


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"👑 Монарх — 30 дней · {config.PREMIUM_PRICE_STARS} ⭐", callback_data="buy:premium30")],
            [InlineKeyboardButton(text=f"💀 Воскрешение · {config.REVIVE_PRICE_STARS} ⭐", callback_data="buy:revive")],
            [InlineKeyboardButton(text=f"🧊 Заморозка серии · {config.FREEZE_PRICE_STARS} ⭐", callback_data="buy:freeze")],
        ]
    )


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    user = await load_user(message)
    if user is None:
        return
    if game.is_premium(user):
        until = (user["premium_until"] or "").split(" ")[0] or "∞"
        status_line = texts.PREMIUM_ACTIVE_LINE.format(until=until)
    else:
        status_line = texts.PREMIUM_INACTIVE_LINE
    await message.answer(texts.PREMIUM_MENU.format(status_line=status_line), reply_markup=_menu_kb())


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(
        texts.PAYSUPPORT.format(support=config.SUPPORT_CONTACT, terms_url=config.TERMS_URL, privacy_url=config.PRIVACY_URL),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery) -> None:
    payload = callback.data.split(":", 1)[1]
    product = _PRODUCTS.get(payload)
    if product is None:
        await callback.answer("Неизвестный протокол.")
        return
    title, desc, price = product

    if payload == "revive":
        user = await db.get_user(callback.from_user.id)
        # Воскрешение не является обычным лечением: покупка разрешена только
        # в критическом окне, иначе кнопка обещает механику, которой сейчас нет.
        if user is None or not game.is_dying(user):
            await callback.answer()
            await callback.message.answer(texts.REVIVE_NOT_NEEDED)
            return

    await callback.answer()
    await callback.message.answer_invoice(title=title, description=desc, payload=payload, currency="XTR", prices=[LabeledPrice(label=title, amount=price)])


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    ok = query.invoice_payload in _PRODUCTS
    await query.answer(ok=ok, error_message=None if ok else "Протокол устарел. Открой /premium заново.")


@router.message(F.successful_payment)
async def on_payment(message: Message) -> None:
    sp = message.successful_payment
    payload = sp.invoice_payload
    user = await db.get_user(message.from_user.id)
    if user is None:
        return
    is_new = await db.record_payment(charge_id=sp.telegram_payment_charge_id, user_id=message.from_user.id, payload=payload, amount_stars=sp.total_amount)
    if not is_new:
        return
    if payload == "premium30":
        until_str = game.premium_until_after(user["premium_until"], config.PREMIUM_DAYS)
        await db.update_user(message.from_user.id, premium_until=until_str, is_premium=1)
        await message.answer(texts.PAYMENT_SUCCESS_PREMIUM.format(until=until_str.split(" ")[0]))
    elif payload == "revive":
        await db.update_user(message.from_user.id, hp=user["max_hp"], dying_until="")
        await message.answer(texts.PAYMENT_SUCCESS_REVIVE.format(hp=user["max_hp"], max_hp=user["max_hp"]))
    elif payload == "freeze":
        count = await db.increment_and_get(message.from_user.id, "streak_freezes")
        await message.answer(texts.PAYMENT_SUCCESS_FREEZE.format(count=count))
