from typing import NamedTuple

from bot import config, game, share, texts


class Msg(NamedTuple):
    """Сообщение, ключ апселл-оффера к нему и share-текст (любое — опционально)."""

    text: str
    upsell: str | None = None
    share: str | None = None


def render_day_events(events: game.DayEvents) -> list[str]:
    """Только тексты сообщений о смене дня — без апселл-офферов."""
    return [msg.text for msg in render_day_messages(events)]


def render_day_messages(events: game.DayEvents) -> list[Msg]:
    """Сообщения о смене дня: штрафы, отсутствие, заморозка, смерть, квесты, вехи, врата."""
    if not events.new_day:
        return []

    messages: list[Msg] = []
    # HP на момент штрафа: при смерти показываем 0, а не восстановленное значение
    hp_after_penalty = 0 if events.died else max(0, events.hp)

    # Урон за невыполненные квесты и урон за дни полного отсутствия имеют
    # разные причины. Не показываем в первом сообщении сумму, в которую уже
    # добавлен absence damage: иначе пользователь видит завышенный штраф.
    missed_damage = events.missed * config.HP_PENALTY_PER_MISS

    # Заморозку предлагаем только тому, у кого её нет: у охотника с зарядами
    # сгорела серия по другой причине, и кнопка «купи заморозку» выглядела бы
    # издевательством.
    freeze_offer = None if events.freezes else config.UPSELL_FREEZE

    if events.missed:
        messages.append(
            Msg(
                texts.HP_LOSS.format(
                    missed=events.missed,
                    damage=missed_damage,
                    hp=hp_after_penalty,
                    max_hp=events.max_hp,
                ),
                freeze_offer,
            )
        )

    if events.skipped_days:
        consequence = (
            texts.ABSENCE_STREAK_LOST.format(damage=events.damage)
            if events.streak_reset
            else texts.ABSENCE_COVERED
        )
        messages.append(
            Msg(
                texts.ABSENCE.format(days=events.skipped_days, consequence=consequence),
                freeze_offer if events.streak_reset else None,
            )
        )

    if events.streak_frozen:
        messages.append(Msg(texts.STREAK_FROZEN))

    if events.dying_survived:
        messages.append(Msg(texts.DYING_SURVIVED.format(level=events.level)))

    if events.died:
        messages.append(
            Msg(
                texts.DEATH.format(
                    level=events.death_level, hp=events.hp, max_hp=events.max_hp
                ),
                config.UPSELL_PREMIUM,
            )
        )

    # Оффер воскрешения: единственное окно, когда покупка реально спасает уровень
    if events.dying:
        messages.append(
            Msg(
                texts.DYING.format(
                    level=events.level, price=config.REVIVE_PRICE_STARS
                ),
                config.UPSELL_REVIVE,
            )
        )

    messages.append(Msg(texts.NEW_DAY.format(count=events.quests_issued)))

    if events.milestone:
        streak, bonus_xp, bonus_freezes = events.milestone
        freeze_line = (
            texts.STREAK_MILESTONE_FREEZE.format(count=bonus_freezes)
            if bonus_freezes
            else ""
        )
        messages.append(
            Msg(
                texts.STREAK_MILESTONE.format(
                    streak=streak, bonus=bonus_xp, freeze_line=freeze_line
                ),
                share=share.milestone_text(streak),
            )
        )
        messages.extend(render_xp_messages(events.milestone_result))

    if events.gate_title:
        messages.append(Msg(texts.GATE_OPENED.format(title=events.gate_title)))

    return messages


def render_xp_result(result: game.XpResult | None) -> list[str]:
    """Только тексты сообщений о левелапах и рангах — без share-кнопок."""
    return [msg.text for msg in render_xp_messages(result)]


def render_xp_messages(result: game.XpResult | None) -> list[Msg]:
    """Сообщения о левелапах и повышении ранга."""
    if result is None:
        return []
    messages = [
        Msg(texts.LEVEL_UP.format(level=level, stat=stat, gain=gain))
        for level, stat, gain in result.levels_gained
    ]
    if result.rank_up:
        messages.append(
            Msg(
                texts.RANK_UP.format(rank=result.rank_up),
                share=share.rank_text(result.rank_up),
            )
        )
    return messages
