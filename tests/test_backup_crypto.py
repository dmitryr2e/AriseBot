"""Шифрование дампов БД (bot/backup_crypto.py)."""
import pytest
from cryptography.fernet import Fernet

from bot.backup_crypto import encrypt_file

PAYLOAD = b"SQLite format 3\x00super-secret-token"


def test_encrypt_file_removes_plaintext(tmp_path):
    key = Fernet.generate_key().decode()
    src = tmp_path / "hunter.db"
    src.write_bytes(PAYLOAD)
    dst = tmp_path / "hunter.db.enc"

    encrypt_file(src, dst, key)

    assert not src.exists(), "открытый дамп обязан удаляться после шифрования"
    assert b"super-secret-token" not in dst.read_bytes()
    assert Fernet(key.encode()).decrypt(dst.read_bytes()) == PAYLOAD


def test_encrypt_file_requires_key(tmp_path):
    src = tmp_path / "hunter.db"
    src.write_bytes(PAYLOAD)

    with pytest.raises(RuntimeError):
        encrypt_file(src, tmp_path / "out.enc", "")

    assert src.exists(), "без ключа исходник трогать нельзя"


def test_encrypt_file_rejects_broken_key(tmp_path):
    src = tmp_path / "hunter.db"
    src.write_bytes(PAYLOAD)

    with pytest.raises(RuntimeError):
        encrypt_file(src, tmp_path / "out.enc", "not-a-fernet-key")

    assert src.exists()
