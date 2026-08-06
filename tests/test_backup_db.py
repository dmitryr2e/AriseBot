"""Джоб бэкапа кладёт на диск только зашифрованные копии (scheduler.backup_db)."""
from cryptography.fernet import Fernet

from bot import config, scheduler


async def test_backup_writes_only_encrypted_artifact(conn, user, tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    backups = tmp_path / "backups"
    monkeypatch.setattr(config, "BACKUP_DIR", backups)
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_KEY", key)

    await scheduler.backup_db()

    files = sorted(backups.iterdir())
    assert len(files) == 1, f"ожидался ровно один артефакт, получили {files}"
    assert files[0].name.endswith(".db.enc")
    # Открытый дамп не должен пережить джоб
    assert not list(backups.glob("hunter_*.db"))
    plain = Fernet(key.encode()).decrypt(files[0].read_bytes())
    assert plain.startswith(b"SQLite format 3")


async def test_backup_skipped_without_key(conn, user, tmp_path, monkeypatch):
    """Нет ключа — нет бэкапа: открытую копию базы на диск не кладём."""
    backups = tmp_path / "backups"
    monkeypatch.setattr(config, "BACKUP_DIR", backups)
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_KEY", "")

    await scheduler.backup_db()

    assert not backups.exists()


async def test_backup_retention_keeps_last_n(conn, user, tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(5):
        (backups / f"hunter_2026010{i}_000000.db.enc").write_bytes(b"old")
    monkeypatch.setattr(config, "BACKUP_DIR", backups)
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_KEY", key)
    monkeypatch.setattr(config, "BACKUP_KEEP", 3)

    await scheduler.backup_db()

    assert len(list(backups.glob("hunter_*.db.enc"))) == 3
