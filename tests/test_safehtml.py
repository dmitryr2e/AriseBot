"""Экранирование пользовательских строк перед отправкой с parse_mode=HTML.

Смысл этих тестов — зафиксировать поведение, из-за отсутствия которого имя
охотника вида `<a href=...>` становилось кликабельной ссылкой в общем
рейтинге и в недельной рассылке по всей базе.
"""
from bot.safehtml import MAX_NAME_LEN, display_name, esc


def test_esc_neutralises_tags():
    assert esc("<b>жирный</b>") == "&lt;b&gt;жирный&lt;/b&gt;"


def test_esc_neutralises_link_injection():
    rendered = esc('<a href="https://evil.tld">Топ-1</a>')
    assert "<a" not in rendered
    assert "&lt;a href=" in rendered


def test_esc_handles_ampersand():
    assert esc("Ким & Ко") == "Ким &amp; Ко"


def test_esc_of_none_is_empty():
    assert esc(None) == ""


def test_esc_accepts_non_strings():
    assert esc(42) == "42"


def test_display_name_escapes_first_name():
    row = {"first_name": "<i>Тень</i>", "username": ""}
    assert display_name(row) == "&lt;i&gt;Тень&lt;/i&gt;"


def test_display_name_falls_back_to_username():
    assert display_name({"first_name": "", "username": "hunter"}) == "hunter"


def test_display_name_falls_back_to_default():
    assert display_name({"first_name": "", "username": ""}) == "Охотник"


def test_display_name_survives_missing_columns():
    assert display_name({}) == "Охотник"


def test_display_name_is_truncated():
    row = {"first_name": "я" * 500, "username": ""}
    assert len(display_name(row)) == MAX_NAME_LEN
