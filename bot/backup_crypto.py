"""Шифрование резервных копий SQLite через Fernet."""
from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet


def encrypt_file(source: Path, destination: Path, key: str) -> None:
    """Зашифровать файл целиком и удалить открытый исходник после успеха."""
    if not key:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY не задан")
    try:
        cipher = Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY должен быть Fernet-ключом") from exc
    data = source.read_bytes()
    destination.write_bytes(cipher.encrypt(data))
    source.unlink()
