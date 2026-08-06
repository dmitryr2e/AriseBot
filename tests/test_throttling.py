"""Token bucket анти-флуда.

Время передаётся в `take` явным аргументом, поэтому тесты не спят и не
патчат `time.monotonic`.
"""
import pytest

from bot import throttling


@pytest.fixture(autouse=True)
def clean_buckets():
    throttling.reset()
    yield
    throttling.reset()


def test_burst_is_allowed_then_blocked():
    now = 1000.0
    allowed = sum(1 for _ in range(20) if throttling.take(1, throttling.DEFAULT_COST, now))
    assert allowed == int(throttling.BUCKET_SIZE)
    assert not throttling.take(1, throttling.DEFAULT_COST, now)


def test_tokens_refill_over_time():
    now = 1000.0
    while throttling.take(1, throttling.DEFAULT_COST, now):
        pass
    assert not throttling.take(1, throttling.DEFAULT_COST, now)
    assert throttling.take(1, throttling.DEFAULT_COST, now + 2.0)


def test_bucket_does_not_overfill():
    now = 1000.0
    throttling.take(1, throttling.DEFAULT_COST, now)
    # Час простоя не должен давать больше, чем ёмкость бакета.
    later = now + 3600.0
    allowed = sum(1 for _ in range(50) if throttling.take(1, throttling.DEFAULT_COST, later))
    assert allowed == int(throttling.BUCKET_SIZE)


def test_users_are_independent():
    now = 1000.0
    while throttling.take(1, throttling.DEFAULT_COST, now):
        pass
    assert not throttling.take(1, throttling.DEFAULT_COST, now)
    assert throttling.take(2, throttling.DEFAULT_COST, now)


def test_heavy_commands_cost_more():
    assert throttling.cost_for_text("/card") > throttling.DEFAULT_COST
    assert throttling.cost_for_text("/report") > throttling.DEFAULT_COST


def test_cost_ignores_bot_suffix_and_case():
    assert throttling.cost_for_text("/Card@SystemAriseBot") == throttling.COMMAND_COST["/card"]


def test_plain_text_costs_default():
    assert throttling.cost_for_text("сегодня отжался 50 раз") == throttling.DEFAULT_COST
    assert throttling.cost_for_text(None) == throttling.DEFAULT_COST


def test_card_spam_is_capped():
    now = 1000.0
    cost = throttling.cost_for_text("/card")
    allowed = sum(1 for _ in range(10) if throttling.take(1, cost, now))
    assert allowed == int(throttling.BUCKET_SIZE // cost)


def test_prune_forgets_idle_full_buckets():
    now = 1000.0
    throttling.take(1, throttling.DEFAULT_COST, now)
    # Бакет успел полностью восстановиться и давно не использовался.
    throttling.prune(now + throttling.IDLE_TTL + throttling.PRUNE_INTERVAL + 1.0)
    assert not throttling._buckets
