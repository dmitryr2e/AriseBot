"""Экранирование пользовательских строк для parse_mode=HTML.

Бот отправляет все сообщения с ``parse_mode=HTML`` (см. ``bot/main.py``),
поэтому любая строка, пришедшая снаружи — имя, username, заголовок личного
квеста, вердикт модели — попадала в разметку как есть. Имя вида
``<a href=\"...\">Топ-1</a>`` превращалось в кликабельную ссылку в общем
рейтинге и в недельной рассылке по всей базе, а незакрытый тег ронял отправку
целиком: Telegram отвечает 400 Bad Request на битый HTML, то есть один
охотник со сломанным именем ломал ``/rating`` всем.

Правило простое: всё, что пришло снаружи, проходит через ``esc()`` прямо в
месте подстановки. Экранировать внутри ``texts.py`` нельзя — там шаблоны с
нашей собственной разметкой, и она обязана остаться живой.
"""
import html

# Telegram и так режет first_name/username, но заголовки квестов и вердикты
# модели приходят произвольной длины: обрезаем, чтобы одна строка рейтинга не
# съедала лимит сообщения в 4096 символов.
MAX_NAME_LEN = 64

DEFAULT_NAME = "Охотник"


def esc(value: object) -> str:
    """HTML-безопасное представление значения. None и пустое -> пустая строка.

    ``quote=False`` осознанно: пользовательские данные никогда не попадают в
    значения атрибутов (единственный атрибут в текстах — наш собственный href
    на политику), а экранированные кавычки в имени выглядели бы мусором.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def display_name(row, fallback: str = DEFAULT_NAME) -> str:
    """Экранированное имя охотника: first_name -> username -> fallback.

    Принимает и ``aiosqlite.Row``, и обычный dict: строки из разных запросов
    содержат разный набор колонок, а отсутствующая трактуется как пустая.
    """
    raw = ""
    for key in ("first_name", "username"):
        try:
            raw = row[key] or ""
        except (IndexError, KeyError, TypeError):
            raw = ""
        if raw:
            break
    return esc((raw or fallback)[:MAX_NAME_LEN])


def user_name(tg_user, fallback: str = DEFAULT_NAME) -> str:
    """То же самое для ``aiogram.types.User`` (у него атрибуты, а не ключи)."""
    raw = getattr(tg_user, "first_name", "") or getattr(tg_user, "username", "") or fallback
    return esc(raw[:MAX_NAME_LEN])
