"""Хендлер: /report — ИИ-оценка отчёта о проделанной работе (Gemini)."""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot import ai, config, db, game, keyboards, texts
from bot.handlers.helpers import load_user, notify_xp_events, process_day_events
from bot.safehtml import esc

router = Router()
log = logging.getLogger(__name__)


class ReportFlow(StatesGroup):
    waiting_text = State()


def _daily_limit(user) -> int:
    return (
        config.PREMIUM_REPORTS_PER_DAY
        if game.is_premium(user)
        else config.FREE_REPORTS_PER_DAY
    )


async def _answer_limit(message: Message, user, limit: int) -> None:
    """Сообщение об исчерпанном лимите отчётов + апселл, если он уместен.

    Монарху не предлагаем купить то, что у него уже есть, и не показываем
    текст про расширение лимита — у него это и есть расширенный лимит.
    """
    if game.is_premium(user):
        await message.answer(texts.REPORT_LIMIT_PREMIUM.format(limit=limit))
        return
    await message.answer(
        texts.REPORT_LIMIT.format(
            limit=limit, premium_limit=config.PREMIUM_REPORTS_PER_DAY
        ),
        reply_markup=keyboards.upsell(config.UPSELL_PREMIUM),
    )


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    user = await load_user(message)
    if user is None:
        return
    if await process_day_events(message, user):
        user = await db.get_user(message.from_user.id)

    limit = _daily_limit(user)
    used = await db.reports_count_today(message.from_user.id, game.today_str(user))
    if used >= limit:
        await _answer_limit(message, user, limit)
        return

    # Дисклеймер об обработке текста в Google Gemini — однократно, перед первым отчётом
    if not user["ai_notice_seen"]:
        await db.update_user(message.from_user.id, ai_notice_seen=1)
        await message.answer(
            texts.GEMINI_DISCLAIMER.format(privacy_url=config.PRIVACY_URL),
            disable_web_page_preview=True,
        )

    await state.set_state(ReportFlow.waiting_text)
    await message.answer(texts.REPORT_PROMPT.format(left=limit - used))


@router.message(ReportFlow.waiting_text, F.text.startswith("/"))
async def report_cancelled(message: Message, state: FSMContext) -> None:
    """Любая команда прерывает протокол отчёта, команда обрабатывается дальше."""
    await state.clear()
    await message.answer(texts.REPORT_CANCELLED)


@router.message(ReportFlow.waiting_text, F.text)
async def report_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < config.REPORT_MIN_LEN:
        await message.answer(texts.REPORT_TOO_SHORT.format(min_len=config.REPORT_MIN_LEN))
        return

    user = await db.get_user(message.from_user.id)
    if user is None:
        await state.clear()
        return

    report_date = game.today_str(user)

    # Повторная проверка лимита (защита от гонок)
    limit = _daily_limit(user)
    used = await db.reports_count_today(message.from_user.id, report_date)
    if used >= limit:
        await state.clear()
        await _answer_limit(message, user, limit)
        return

    # Эвристика на prompt-injection (AUDIT 2.4) — только сигнал в лог, не
    # блокировка: сам текст отчёта в лог не попадает, только user_id, имя
    # сработавшего маркера и необратимый fingerprint для сопоставления.
    injection_marker = ai.detect_suspicious_report(text)
    if injection_marker is not None:
        log.warning(
            "suspicious report: user_id=%s marker=%s fingerprint=%s",
            message.from_user.id,
            injection_marker,
            ai.fingerprint_report(text),
        )

    # Сбрасываем состояние ДО медленного вызова ИИ:
    # иначе два быстрых сообщения обходят дневной лимит отчётов
    await state.clear()

    # Дедуп (AUDIT 2.4): та же (без учёта регистра/пробелов) копия отчёта не
    # должна повторно уходить в Gemini и тем более приносить награду.
    # Дешёвый pre-check здесь + гарантия на уровне БД в add_report ниже —
    # на случай гонки двух одинаковых отчётов подряд.
    if await db.report_is_duplicate(
        message.from_user.id,
        report_date,
        text,
    ):
        await message.answer(texts.REPORT_DUPLICATE)
        return

    await message.answer(texts.REPORT_ANALYZING)
    try:
        xp, stats, verdict = await ai.evaluate_report(text)
    except ai.AiUnavailable:
        await state.set_state(ReportFlow.waiting_text)  # даём повторить попытку
        await message.answer(texts.REPORT_AI_DOWN)
        return

    is_new_report = await db.add_report(
        message.from_user.id,
        report_date,
        text,
        xp,
        verdict,
    )
    if not is_new_report:
        # Гонка: тот же fingerprint успел записаться, пока ждали ответ ИИ.
        # Награду не начисляем — как и в обычном дедуп-случае выше.
        await message.answer(texts.REPORT_DUPLICATE)
        return

    await db.increment_user(message.from_user.id, total_reports=1)

    # Вердикт пишет модель по тексту пользователя, то есть это тоже
    # недоверенная строка: в HTML она идёт только экранированной.
    safe_verdict = esc(verdict)

    if xp <= 0:
        await message.answer(texts.REPORT_ZERO.format(verdict=safe_verdict))
        return

    # Прямое распределение XP по указанным статам: +1 к каждому выбранному стату.
    # Инкрементами, а не абсолютным значением из прочитанной строки: пока ИИ
    # анализировал отчёт, статы мог поднять левелап или второй хендлер.
    user = await db.get_user(message.from_user.id)
    result = await game.grant_xp(user, xp, count_quest=False)
    await db.increment_user(message.from_user.id, **dict.fromkeys(stats, 1))

    stats_label = ", ".join(config.STAT_LABELS[s] for s in stats)
    await message.answer(
        texts.REPORT_RESULT.format(
            verdict=safe_verdict,
            xp=result.amount,
            stats=stats_label,
            progress=f"{result.xp} / {result.xp_needed}",
        )
    )
    await notify_xp_events(message, result)
