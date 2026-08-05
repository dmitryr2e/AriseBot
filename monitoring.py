"""Мониторинг: алерты админам в Telegram и метрики фоновых джобов.

ROADMAP Фаза 2, п. 20 (AUDIT 5.3). Осознанно без Sentry: внешний SaaS —
ещё один секрет в окружении и ещё одна зависимость ради проекта, у которого
админов буквально несколько человек и все они уже сидят в Telegram. Канал
доставки тот же, что у самого продукта, поэтому «алерты не приходят» —
ситуация, которую невозможно не заметить.

Три вещи, которые здесь есть:

1. ``TelegramAlertHandler`` — logging-хендлер уровня ERROR. Всё, что бот уже
   логирует через ``log.exception`` (глобальный обработчик апдейтов в
   ``bot/main.py``, каждый фоновый джоб, срыв CAS в ``game._apply_xp``),
   автоматически превращается в сообщение админам. Новых мест логирования
   заводить не нужно — в этом весь смысл.
2. ``job`` — обёртка для джобов APScheduler: длительность, счётчики запусков
   и падений, единый ERROR-лог при исключении (то есть тот же алерт).
3. ``note_rollover`` — сторож смены дня. Требование аудита «алерт, если
   rollover обработал 0» нельзя понимать буквально: джоб тикает каждые
   15 минут и в норме почти всегда обрабатывает ноль охотников — полночь у
   пояса наступает раз в сутки. Сломанной сменой дня является не нулевой
   прогон, а сутки без единого обработанного охотника при непустой базе.

Модуль обязан быть неубиваемым: любая ошибка внутри мониторинга гасится и
никогда не всплывает в вызывающий код. Упавший мониторинг — это потерянный
алерт, а не упавший бот.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bot import config

log = logging.getLogger(__name__)

# Уровень, начиная с которого лог уходит в Telegram.
ALERT_LEVEL = logging.ERROR
# Один и тот же источник ошибки не чаще раза в 5 минут: падающий каждую
# минуту джоб иначе устроит админам DDoS и упрётся в лимиты Telegram.
ALERT_COOLDOWN_SEC = 300.0
# Telegram режет сообщение на 4096 символах — оставляем запас на заголовок.
ALERT_MAX_CHARS = 3000
# Сутки + запас на пояса и рестарты: реальная смена дня случается у каждого
# охотника раз в 24 часа, поэтому раньше 26 часов тишины паниковать не о чем.
ROLLOVER_SILENCE_SEC = 26 * 3600.0
ROLLOVER_ALERT_COOLDOWN_SEC = 6 * 3600.0

_bot: Any | None = None
_loop: asyncio.AbstractEventLoop | None = None
_handler: logging.Handler | None = None
# Сильные ссылки на задачи отправки: asyncio держит только слабые, и без
# реестра сборщик мусора может убить алерт до того, как он уйдёт.
_tasks: set[asyncio.Task] = set()
_last_alert: dict[str, float] = {}
_rollover_last_processed: float = time.monotonic()


@dataclass
class JobStat:
    """Метрики одного фонового джоба (в памяти процесса, без внешней TSDB)."""

    runs: int = 0
    failures: int = 0
    last_started: float = 0.0
    last_duration: float = 0.0
    last_error: str = ""


JOB_STATS: dict[str, JobStat] = {}


def setup(bot: Any, *, attach_handler: bool = True) -> None:
    """Включить мониторинг. Вызывать из работающего event loop (bot/main.py).

    Ссылка на loop захватывается здесь: logging-хендлер может сработать из
    любого места, в том числе из чужого потока, и без явной ссылки на цикл
    отправить корутину было бы некуда.
    """
    global _bot, _loop, _handler
    _bot = bot
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
        log.warning("monitoring.setup вызван вне event loop — алерты будут пропускаться")
    if not config.ADMIN_IDS:
        log.warning("monitoring: ADMIN_IDS пуст, алерты в Telegram отключены")
        return
    if attach_handler and _handler is None:
        _handler = TelegramAlertHandler()
        logging.getLogger().addHandler(_handler)
        log.info("monitoring: алерты уровня %s уходят %s админам", logging.getLevelName(ALERT_LEVEL), len(config.ADMIN_IDS))


def shutdown() -> None:
    """Снять хендлер и забыть бота (тесты и корректное завершение процесса)."""
    global _bot, _loop, _handler
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler = None
    _bot = None
    _loop = None
    _tasks.clear()
    _last_alert.clear()


async def send_alert(
    text: str, key: str | None = None, cooldown: float = ALERT_COOLDOWN_SEC
) -> bool:
    """Отправить текст всем админам. True — ушло хотя бы одному.

    ``parse_mode=None`` принципиально: в алерт попадает traceback, а в нём
    сплошь и рядом угловые скобки (``<module>``, generic-типы). С HTML-
    разметкой по умолчанию Telegram ответил бы 400 — и алерт об ошибке
    потерялся бы именно там, где он нужнее всего.
    """
    if _bot is None or not config.ADMIN_IDS:
        return False
    dedup_key = key or text[:120]
    now = time.monotonic()
    last = _last_alert.get(dedup_key)
    if last is not None and now - last < cooldown:
        return False
    _last_alert[dedup_key] = now

    payload = text[:ALERT_MAX_CHARS]
    sent = False
    for admin_id in config.ADMIN_IDS:
        try:
            await _bot.send_message(
                admin_id, payload, parse_mode=None, disable_web_page_preview=True
            )
            sent = True
        except Exception as exc:  # noqa: BLE001 — алерт не имеет права падать
            # Сознательно WARNING + no_alert: ERROR ушёл бы обратно в этот же
            # хендлер и закрутил бы рекурсию «не отправился алерт об алерте».
            log.warning(
                "monitoring: алерт админу %s не доставлен: %s",
                admin_id,
                exc,
                extra={"no_alert": True},
            )
    return sent


class TelegramAlertHandler(logging.Handler):
    """ERROR-лог -> сообщение админам. Ошибки внутри себя гасит молча."""

    def __init__(self, level: int = ALERT_LEVEL) -> None:
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            # Защита от рекурсии точечная, по флагу записи, а не по имени
            # логгера: сам мониторинг обязан уметь кричать о падении джоба
            # (log.exception в job()), и глушить весь bot.monitoring целиком
            # значило бы потерять именно те алерты, ради которых всё затевалось.
            # Помечены no_alert только логи о недоставленном алерте.
            if getattr(record, "no_alert", False):
                return
            if _bot is None or _loop is None or _loop.is_closed():
                return
            text = _format_alert(record)
            key = f"{record.name}:{record.module}:{record.lineno}"
            self._schedule(send_alert(text, key=key))
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _schedule(self, coro: Awaitable[Any]) -> None:
        assert _loop is not None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is _loop:
            task = _loop.create_task(coro)  # type: ignore[arg-type]
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
        else:
            # Лог мог прийти из чужого потока (например, из executor'а):
            # create_task оттуда не потокобезопасен.
            asyncio.run_coroutine_threadsafe(coro, _loop)  # type: ignore[arg-type]


def _format_alert(record: logging.LogRecord) -> str:
    head = f"🚨 {record.levelname} · {record.name}"
    text = f"{head}\n{record.getMessage()}"
    if record.exc_info:
        tb = "".join(traceback.format_exception(*record.exc_info))
        text = f"{text}\n\n{tb[-1200:]}"
    return text[:ALERT_MAX_CHARS]


def job(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Обернуть корутину-джоб метриками и единым ERROR-логом.

    Исключение гасится намеренно: APScheduler и так не уронит процесс, но
    печатает свой traceback мимо нашего логгера — а значит, мимо алертов.
    Здесь падение сначала превращается в ``log.exception`` (то есть в алерт
    админам), и только потом джоб тихо завершается до следующего тика.
    """
    name = getattr(func, "__name__", repr(func))

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        stat = JOB_STATS.setdefault(name, JobStat())
        stat.runs += 1
        stat.last_started = time.time()
        started = time.perf_counter()
        try:
            return await func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            stat.failures += 1
            stat.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("Джоб %s упал", name)
            return None
        finally:
            stat.last_duration = time.perf_counter() - started

    wrapper.__name__ = f"monitored_{name}"
    wrapper.__qualname__ = wrapper.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


async def note_rollover(processed: int, failed: int = 0) -> None:
    """Отметить итог прогона daily_rollover и поднять тревогу при тишине.

    Нулевой прогон сам по себе нормален (см. модульный докстринг), поэтому
    считается не он, а время с последнего прогона, который реально кого-то
    перевёл на новый день. Если суток с лишним не было ни одного, а охотники
    в базе есть — смена дня сломана, и это ровно тот отказ, который иначе
    заметит только пользователь.
    """
    global _rollover_last_processed
    if processed > 0:
        _rollover_last_processed = time.monotonic()
        return
    if failed > 0:
        # Ошибки уже уехали алертом из самого джоба — здесь только не даём
        # «тишине» выглядеть здоровой из-за того, что все прогоны падали.
        return
    silence = time.monotonic() - _rollover_last_processed
    if silence < ROLLOVER_SILENCE_SEC:
        return
    try:
        from bot import db

        total = await db.count_where("users")
    except Exception:  # noqa: BLE001
        return
    if total <= 0:
        return
    await send_alert(
        "🚨 daily_rollover: за "
        f"{silence / 3600:.1f} ч ни один охотник не переведён на новый день, "
        f"а в базе их {total}. Смена дня, скорее всего, сломана.",
        key="rollover_silence",
        cooldown=ROLLOVER_ALERT_COOLDOWN_SEC,
    )


def stats_lines() -> list[str]:
    """Метрики джобов человекочитаемо — для /admin."""
    if not JOB_STATS:
        return ["— джобы ещё не запускались"]
    lines = []
    now = time.time()
    for name in sorted(JOB_STATS):
        stat = JOB_STATS[name]
        ago = f"{(now - stat.last_started) / 60:.0f} мин назад" if stat.last_started else "—"
        line = f"— {name}: {stat.runs} запусков, ошибок {stat.failures}, последний {ago}"
        if stat.last_error:
            line += f" ({stat.last_error})"
        lines.append(line)
    return lines


def reset() -> None:
    """Полный сброс состояния — только для тестов."""
    global _rollover_last_processed
    JOB_STATS.clear()
    _last_alert.clear()
    _tasks.clear()
    _rollover_last_processed = time.monotonic()
