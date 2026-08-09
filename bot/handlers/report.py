"""Хендлер: /report — ИИ-оценка отчёта о проделанной работе (Gemini)."""
import asyncio
import logging
from collections import defaultdict

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
_report_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class ReportFlow(StatesGroup):
    waiting_text = State()


def _daily_limit(user) -> int:
    return config.PREMIUM_REPORTS_PER_DAY if game.is_premium(user) else config.FREE_REPORTS_PER_DAY


async def _answer_limit(message: Message, user, limit: int) -> None:
    if game.is_premium(user):
        await message.answer(texts.REPORT_LIMIT_PREMIUM.format(limit=limit))
        return
    await message.answer(
        texts.REPORT_LIMIT.format(limit=limit, premium_limit=config.PREMIUM_REPORTS_PER_DAY),
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
    if not user["ai_notice_seen"]:
        await db.update_user(message.from_user.id, ai_notice_seen=1)
        await message.answer(texts.GEMINI_DISCLAIMER.format(privacy_url=config.PRIVACY_URL), disable_web_page_preview=True)
    await state.set_state(ReportFlow.waiting_text)
    await message.answer(texts.REPORT_PROMPT.format(left=limit - used))


@router.message(ReportFlow.waiting_text, F.text.startswith("/"))
async def report_cancelled(message: Message, state: FSMContext) -> None:
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

    async with _report_locks[message.from_user.id]:
        user = await db.get_user(message.from_user.id)
        if user is None:
            await state.clear()
            return
        report_date = game.today_str(user)
        limit = _daily_limit(user)
        used = await db.reports_count_today(message.from_user.id, report_date)
        if used >= limit:
            await state.clear()
            await _answer_limit(message, user, limit)
            return
        injection_marker = ai.detect_suspicious_report(text)
        if injection_marker is not None:
            log.warning("suspicious report: user_id=%s marker=%s fingerprint=%s", message.from_user.id, injection_marker, ai.fingerprint_report(text))
        await state.clear()
        if await db.report_is_duplicate(message.from_user.id, report_date, text):
            await message.answer(texts.REPORT_DUPLICATE)
            return
        await message.answer(texts.REPORT_ANALYZING)
        try:
            xp, stats, verdict = await ai.evaluate_report(text)
        except ai.AiUnavailable:
            await state.set_state(ReportFlow.waiting_text)
            await message.answer(texts.REPORT_AI_DOWN)
            return
        if not await db.add_report(message.from_user.id, report_date, text, xp, verdict):
            await message.answer(texts.REPORT_DUPLICATE)
            return
        await db.increment_user(message.from_user.id, total_reports=1)
        safe_verdict = esc(verdict)
        if xp <= 0:
            await message.answer(texts.REPORT_ZERO.format(verdict=safe_verdict))
            return
        user = await db.get_user(message.from_user.id)
        result = await game.grant_xp(user, xp, count_quest=False)
        await db.increment_user(message.from_user.id, **dict.fromkeys(stats, 1))
        stats_label = ", ".join(config.STAT_LABELS[s] for s in stats)
        await message.answer(texts.REPORT_RESULT.format(verdict=safe_verdict, xp=result.amount, stats=stats_label, progress=f"{result.xp} / {result.xp_needed}"))
        await notify_xp_events(message, result)
