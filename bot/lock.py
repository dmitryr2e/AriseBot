"""Замок «один экземпляр бота» (ROADMAP Фаза 2, п. 21 / AUDIT 5.4).

Зачем: два процесса с одним токеном, дергающие getUpdates, дают
``TelegramConflictError: terminated by other getUpdates request`` — ровно тот
инцидент, который разбирался в сессии 12 (см. HANDOFF § -1). Классический
сценарий: ``restart: always`` в docker-compose поднял новый контейнер раньше,
чем умер старый, либо кто-то запустил бота руками рядом с compose. Хуже того,
второй процесс не просто мешает поллингу — он второй раз крутит планировщик
(rollover, начисления, рассылки).

Почему flock, а не PID-файл: flock снимается ядром при смерти процесса —
включая SIGKILL и OOM. PID-файл после такого падения остаётся на диске и
блокирует честный перезапуск, а проверка «жив ли PID» ломается на переиспользовании
номеров. Здесь несвежий lock-файл безвреден по определению.
"""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

from bot import config

try:  # pragma: no cover — на Linux/macOS всегда есть
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# Замок живёт рядом с БД: это тот же volume, что монтируется в контейнер,
# поэтому два контейнера с общим data/ увидят один и тот же файл.
DEFAULT_LOCK_PATH = Path(
    os.getenv("BOT_LOCK_PATH", str(Path(config.DB_PATH).parent / "bot.lock"))
)


class AlreadyRunning(RuntimeError):
    """Замок занят другим процессом."""

    def __init__(self, path: Path, pid: int | None) -> None:
        self.path = path
        self.pid = pid
        super().__init__(
            f"Бот уже запущен: замок {path} держит процесс "
            f"{pid if pid else 'с неизвестным PID'}"
        )


class InstanceLock:
    """Эксклюзивный неблокирующий flock на файле."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_LOCK_PATH
        self._fd: int | None = None

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    def acquire(self) -> InstanceLock:
        """Занять замок. Бросает AlreadyRunning, если его держит кто-то ещё."""
        if fcntl is None:
            log.warning(
                "Платформа без fcntl: защита от второго экземпляра отключена (%s)",
                self.path,
            )
            return self
        if self._fd is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pid = _read_pid(fd)
            os.close(fd)
            raise AlreadyRunning(self.path, pid) from None
        # PID пишем только для диагностики (в логе и в сообщении об ошибке):
        # источником истины остаётся сам flock, а не содержимое файла.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.fsync(fd)
        except OSError as exc:  # noqa: BLE001
            log.warning("Не удалось записать PID в %s: %s", self.path, exc)
        self._fd = fd
        atexit.register(self.release)
        log.info("Замок экземпляра занят: %s (pid %s)", self.path, os.getpid())
        return self

    def release(self) -> None:
        """Снять замок. Идемпотентно — вызывается и из finally, и из atexit."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover
            pass
        finally:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover
                pass
        # Файл сознательно не удаляем: unlink открыл бы гонку, в которой
        # следующий процесс уже создал и залочил файл с тем же именем, а мы
        # бы его снесли. Пустой lock-файл ничего не стоит.

    def __enter__(self) -> InstanceLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def _read_pid(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 32).decode("utf-8", "ignore").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def acquire(path: str | os.PathLike | None = None) -> InstanceLock:
    """Короткая форма: занять замок и вернуть его."""
    return InstanceLock(path).acquire()
