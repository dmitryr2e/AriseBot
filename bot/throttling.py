"""Анти-флуд: ограничение частоты апдейтов на охотника.

До этой middleware у бота не было вообще никакого ограничения частоты.
``/card`` рендерит PNG 800x1240 (в hd — 1600x2480) с гауссовым размытием в
отдельном потоке, ``/report`` уходит в Gemini и тратит дневную квоту ключа,
а любая команда — это запросы в единственное соединение с SQLite. Один
пользователь с зажатой кнопкой утилизировал CPU, тредпул и квоту разом, и
упирал бота в лимиты Telegram на отправке ответов.

Реализация — token bucket на пользователя в памяти процесса: бот
однопроцессный (см. ``bot/lock.py``), поэтому Redis был бы лишней
зависимостью ради одного счётчика. Пополнение непрерывное, а не окнами:
живой человек лимита не замечает, автокликер упирается в него на второй
секунде.

Что НЕ троттлится принципиально:

* ``pre_checkout_query`` — на него Telegram ждёт ответ 10 секунд, иначе
  платёж отменяется;
* сообщение с ``successful_payment`` — пропустить его значит забрать
  Stars и не выдать товар;
* админы из ``config.ADMIN_IDS`` — ``/broadcast`` и так идёт с собственным
  троттлингом рассылки.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bot import config

log = logging.getLogger(__name__)

# Токенов в секунду и ёмкость бакета. 1/сек с запасом на 8 подряд: человек
# столько не нажимает, а скрипт съедает запас мгновенно.
REFILL_PER_SEC = 1.0
BUCKET_SIZE = 8.0
DEFAULT_COST = 1.0

# Дорогие команды. /card рендерит картинку в потоке, /report ходит в Gemini,
# /rating и /boss тянут агрегаты по базе — им цена выше обычной.
COMMAND_COST: dict[str, float] = {
    "/card": 4.0,
    "/report": 2.0,
    "/rating": 2.0,
    "/boss": 1.5,
    "/start": 2.0,
}

# Не чаще одного предупреждения в 15 секунд на охотника: иначе на флуд мы
# отвечаем собственным флудом и сами упираемся в лимиты Telegram.
NOTICE_COOLDOWN = 15.0
# Бакет забывается после IDLE_TTL тишины: за это время он всё равно успевает
# восстановиться до полного (IDLE_TTL * REFILL_PER_SEC много больше ёмкости),
# поэтому забытый бакет неотличим от нового. Словарь не должен расти по числу
# всех, кто когда-либо писал боту.
IDLE_TTL = 600.0
PRUNE_INTERVAL = 300.0

THROTTLED_TEXT = (
    "⚠️ Слишком много запросов подряд. Система остывает — повтори через несколько секунд."
)


class _Bucket:
    __slots__ = ("notified", "tokens", "updated")

    def __init__(self, tokens: float, now: float) -> None:
        self.tokens = tokens
        self.updated = now
        self.notified = 0.0


_buckets: dict[int, _Bucket] = {}
_last_prune = 0.0


def cost_for_text(text: str | None) -> float:
    """Цена апдейта по тексту сообщения: команда из COMMAND_COST или базовая."""
    if not text:
        return DEFAULT_COST
    head = text.split(maxsplit=1)[0].lower()
    if not head.startswith("/"):
        return DEFAULT_COST
    head = head.split("@", 1)[0]  # /card@SystemAriseBot в группах
    return COMMAND_COST.get(head, DEFAULT_COST)


def take(user_id: int, cost: float, now: float) -> bool:
    """Списать ``cost`` токенов. False — лимит исчерпан, апдейт надо отбросить."""
    bucket = _buckets.get(user_id)
    if bucket is None:
        bucket = _Bucket(BUCKET_SIZE, now)
        _buckets[user_id] = bucket
    else:
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(BUCKET_SIZE, bucket.tokens + elapsed * REFILL_PER_SEC)
        bucket.updated = now
    if bucket.tokens < cost:
        return False
    bucket.tokens -= cost
    return True


def prune(now: float) -> None:
    """Выбросить бакеты, по которым давно не было запросов. Не чаще PRUNE_INTERVAL."""
    global _last_prune
    if now - _last_prune < PRUNE_INTERVAL:
        return
    _last_prune = now
    stale = [uid for uid, bucket in _buckets.items() if now - bucket.updated > IDLE_TTL]
    for uid in stale:
        _buckets.pop(uid, None)


def reset() -> None:
    """Полный сброс состояния — только для тестов."""
    global _last_prune
    _buckets.clear()
    _last_prune = 0.0


def _exempt(event: Update) -> bool:
    """Апдейты, которые нельзя отбрасывать ни при каких условиях."""
    message = event.message
    if message is not None and message.successful_payment is not None:
        return True
    return event.pre_checkout_query is not None


class ThrottleMiddleware(BaseMiddleware):
    """Отбрасывает апдейты сверх лимита ДО обращения к БД и хендлерам."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update) or _exempt(event):
            return await handler(event, data)

        if event.message is not None:
            tg_user = event.message.from_user
            cost = cost_for_text(event.message.text)
        elif event.callback_query is not None:
            tg_user = event.callback_query.from_user
            cost = DEFAULT_COST
        else:
            return await handler(event, data)

        if tg_user is None or tg_user.id in config.ADMIN_IDS:
            return await handler(event, data)

        now = time.monotonic()
        prune(now)
        if take(tg_user.id, cost, now):
            return await handler(event, data)

        log.info("throttled: user_id=%s cost=%s", tg_user.id, cost)
        await self._notify(event, tg_user.id, now)
        return None

    async def _notify(self, event: Update, user_id: int, now: float) -> None:
        """Сказать охотнику, что он упёрся в лимит. Молча гасим любые сбои."""
        bucket = _buckets.get(user_id)
        if event.callback_query is not None:
            # На callback ответить обязаны в любом случае: без ответа на
            # кнопке навсегда останется крутилка.
            try:
                await event.callback_query.answer(THROTTLED_TEXT)
            except Exception:  # noqa: BLE001
                pass
            return
        if bucket is not None and now - bucket.notified < NOTICE_COOLDOWN:
            return
        if bucket is not None:
            bucket.notified = now
        try:
            await event.message.answer(THROTTLED_TEXT)
        except Exception:  # noqa: BLE001
            pass
