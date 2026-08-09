"""Точка входа. Запуск: python -m bot.main"""
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, TelegramObject, Update

from bot import config, db, game, lock, monitoring, texts, throttling
from bot.handlers import setup_routers
from bot.scheduler import setup_scheduler


class ActivityMiddleware(BaseMiddleware):
    """Отмечает активность и начисляет win-back бонус вернувшимся."""

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]], Awaitable[Any]
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None

        if isinstance(event, Update):
            if event.message:
                tg_user = event.message.from_user
            elif event.callback_query:
                tg_user = event.callback_query.from_user

        if tg_user is not None:
            user = await db.get_user(tg_user.id)

            if user is not None:
                # winback_sent означает, что сообщение о возвращении уже было
                # подготовлено для этого эпизода отсутствия. Бонус можно
                # забрать только если пользователь действительно отсутствовал
                # не меньше WINBACK_AFTER_DAYS: обычная активность больше не
                # превращается в ежедневную раздачу XP.
                cutoff = (
                    datetime.now(config.TZ).date()
                    - timedelta(days=config.WINBACK_AFTER_DAYS)
                ).strftime("%Y-%m-%d")
                cur = await db.db().execute(
                    "UPDATE users SET winback_sent = 0 "
                    "WHERE user_id = ? AND winback_sent = 1 "
                    "AND last_seen != '' AND last_seen <= ?",
                    (tg_user.id, cutoff),
                )
                await db.db().commit()
                if cur.rowcount > 0:
                    await game.grant_xp(
                        user,
                        config.WINBACK_BONUS_XP,
                        count_quest=False,
                    )

                    try:
                        await event.bot.send_message(
                            tg_user.id,
                            texts.WINBACK_BONUS_GRANTED.format(
                                bonus=config.WINBACK_BONUS_XP
                            ),
                        )
                    except Exception:
                        pass

                await db.touch_last_seen(
                    tg_user.id,
                    game.today_str(user),
                )

        return await handler(event, data)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("system")


COMMANDS = [
    BotCommand(command="quests", description="Квесты дня"),
    BotCommand(command="report", description="ИИ-отчёт о работе (+XP)"),
    BotCommand(command="profile", description="Статус охотника"),
    BotCommand(command="card", description="Карточка охотника"),
    BotCommand(command="boss", description="Босс недели"),
    BotCommand(command="rating", description="Рейтинг охотников"),
    BotCommand(command="achievements", description="Достижения"),
    BotCommand(command="ref", description="Позвать друзей (+XP)"),
    BotCommand(command="addquest", description="Добавить свой квест"),
    BotCommand(command="myquests", description="Мои квесты"),
    BotCommand(command="remind", description="Время напоминания"),
    BotCommand(command="timezone", description="Часовой пояс"),
    BotCommand(command="premium", description="Статус «Монарх»"),
    BotCommand(command="paysupport", description="Поддержка по оплатам"),
    BotCommand(command="privacy", description="Какие данные хранит Система"),
    BotCommand(command="delete_me", description="Удалить все свои данные"),
    BotCommand(command="help", description="Протоколы Системы"),
]


async def main() -> None:
    if not config.BOT_TOKEN:
        log.error(
            "Переменная окружения TELEGRAM_BOT_TOKEN "
            "не задана. Завершение."
        )
        sys.exit(1)

    instance_lock = None
    scheduler = None
    bot = None

    try:
        # Занимаем lock до инициализации БД и запуска polling.
        # Любая ошибка после этого попадёт в finally.
        instance_lock = lock.acquire()

        await db.init_db()
        log.info("База данных инициализирована: %s", config.DB_PATH)

        bot = Bot(
            token=config.BOT_TOKEN,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML
            ),
        )

        monitoring.setup(bot)

        dp = Dispatcher()
        # Троттлинг — первым: отброшенный апдейт не должен доходить ни до
        # ActivityMiddleware (а это запрос в БД на каждое нажатие), ни до
        # хендлеров.
        dp.update.outer_middleware(throttling.ThrottleMiddleware())
        dp.update.outer_middleware(ActivityMiddleware())
        dp.include_router(setup_routers())

        @dp.error()
        async def on_error(event: Any) -> bool:
            """Глобальный обработчик: логируем и мягко отвечаем."""
            log.exception(
                "Ошибка обработки апдейта: %s",
                event.exception,
            )

            upd = event.update

            try:
                if upd.callback_query:
                    await upd.callback_query.answer(
                        "Система дала сбой. Попробуй ещё раз.",
                        show_alert=True,
                    )
                elif upd.message:
                    await upd.message.answer(
                        "⚠️ Система дала сбой. "
                        "Попробуй ещё раз чуть позже."
                    )
            except Exception:
                pass

            return True

        scheduler = setup_scheduler(bot)
        scheduler.start()
        log.info(
            "Планировщик запущен (TZ=%s)",
            config.TZ_NAME,
        )

        await bot.set_my_commands(COMMANDS)
        log.info(
            "СИСТЕМА активирована. "
            "Начинаю наблюдение за охотниками..."
        )

        await dp.start_polling(bot)

    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)

        monitoring.shutdown()

        if bot is not None:
            await bot.session.close()

        if instance_lock is not None:
            instance_lock.release()

        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
