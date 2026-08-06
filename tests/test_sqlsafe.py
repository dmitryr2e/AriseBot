"""Allowlist идентификаторов для динамического SQL (bot/sqlsafe.py)."""
import pytest

from bot import sqlsafe


def test_known_columns_pass():
    sqlsafe.require_user_columns({"hp": 1, "streak": 2, "created_at": ""})


def test_unknown_column_rejected():
    with pytest.raises(ValueError):
        sqlsafe.require_user_columns(["hp", "nope"])


def test_injection_attempt_rejected():
    with pytest.raises(ValueError):
        sqlsafe.require_user_columns(["hp = 0; DROP TABLE users; --"])


def test_empty_columns_pass():
    sqlsafe.require_user_columns([])


def test_known_tables_pass():
    for table in ("users", "quests", "reports", "payments", "bosses"):
        sqlsafe.require_table(table)


def test_unknown_table_rejected():
    with pytest.raises(ValueError):
        sqlsafe.require_table("users; DROP TABLE payments")
