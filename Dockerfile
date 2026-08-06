FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/

# Непривилегированный пользователь; data/ — volume для БД и бэкапов
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV BOT_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
