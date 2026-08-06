# Деплой и эксплуатация

Целевая платформа: любой Linux VPS с Docker (бот) + Vercel (лендинг).

## Первый запуск (VPS)

```bash
git clone <repo> && cd <repo>
cp .env.example .env        # заполнить TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, ADMIN_IDS
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   ^ вписать в BACKUP_ENCRYPTION_KEY и сохранить копию ключа ВНЕ volume с бэкапами
docker compose up -d --build
docker compose logs -f bot  # убедиться, что "СИСТЕМА активирована"
```

`restart: always` в compose обеспечивает автоперезапуск после падений и ребута сервера
(при условии, что docker daemon включён: `systemctl enable docker`).

## Обновление

```bash
git pull
docker compose up -d --build
```

БД живёт в named volume `bot_data` и переживает пересборку контейнера.

## Бэкапы

- Бот сам делает бэкап каждые `BACKUP_INTERVAL_HOURS` (по умолчанию 6 ч) через
  `VACUUM INTO` — это корректный способ для SQLite в WAL-режиме
  (простое копирование файла даёт битую копию).
- Дамп сразу шифруется (Fernet, ключ `BACKUP_ENCRYPTION_KEY`): в volume лежит
  `/app/data/backups/hunter_YYYYMMDD_HHMMSS.db.enc`, открытых копий не остаётся.
  Хранится последних `BACKUP_KEEP` (по умолчанию 28 ≈ 7 суток).
- **Без `BACKUP_ENCRYPTION_KEY` бэкапы не делаются вообще** — в логе будет
  ошибка «BACKUP_ENCRYPTION_KEY не задан». Это осознанно: дамп содержит имена,
  тексты отчётов и историю платежей, класть его на диск открытым нельзя.
- Ключ храните отдельно от бэкапов (менеджер паролей / секреты хостинга).
  Потеряли ключ — потеряли все копии, расшифровать их нечем.
- **Обязательно** настройте выгрузку каталога бэкапов во внешнее хранилище, например cron на хосте:

```bash
# /etc/cron.daily/bot-backup-offsite
docker cp $(docker compose ps -q bot):/app/data/backups /srv/offsite/bot-backups
# затем rclone/rsync в S3/B2/другой сервер
```

## Восстановление

Сначала расшифровать копию тем же ключом:

```bash
python - <<'PY'
import os
from cryptography.fernet import Fernet

stamp = "<STAMP>"
key = os.environ["BACKUP_ENCRYPTION_KEY"]
with open(f"/srv/offsite/bot-backups/hunter_{stamp}.db.enc", "rb") as src:
    data = Fernet(key.encode()).decrypt(src.read())
with open("/srv/offsite/hunter.db", "wb") as dst:
    dst.write(data)
PY
```

Затем положить расшифрованный файл в volume:

```bash
docker compose down
docker run --rm -v <project>_bot_data:/data -v /srv/offsite:/backup alpine \
  cp /backup/hunter.db /data/hunter.db
docker compose up -d
rm /srv/offsite/hunter.db   # не оставляем открытую копию на хосте
```

После восстановления проверьте `/admin` — числа пользователей должны совпадать с ожидаемыми.

## Ротация секретов

Если токен бота или ключ Gemini мог утечь (репозиторий был публичным с ними в истории):
1. `@BotFather` → `/revoke` → обновить `TELEGRAM_BOT_TOKEN` в `.env`.
2. Google AI Studio → удалить ключ, создать новый → обновить `GEMINI_API_KEY`.
3. `docker compose up -d` для перечитывания `.env`.

При ротации `BACKUP_ENCRYPTION_KEY` старые копии останутся на старом ключе —
не выбрасывайте его, пока не истечёт срок хранения этих бэкапов.

## Важно про git-историю

`hunter.db*` удалены из рабочего дерева и добавлены в `.gitignore`, но если они
уже попадали в коммиты — вычистите историю перед публикацией репозитория:

```bash
pip install git-filter-repo
git filter-repo --invert-paths --path hunter.db --path hunter.db-shm --path hunter.db-wal
git push --force
```
