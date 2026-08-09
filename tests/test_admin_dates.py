from datetime import UTC, datetime

from bot import config, timeutil


def test_utc_created_at_converts_to_local_date():
    # 23:30 UTC is already the next day in Moscow.
    utc_stamp = "2026-08-08 23:30:00"
    expected = datetime.fromisoformat(utc_stamp).replace(tzinfo=UTC).astimezone(
        config.TZ
    ).strftime("%Y-%m-%d")

    assert timeutil.local_date_of(utc_stamp, config.TZ_NAME) == expected
