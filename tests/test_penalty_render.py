from bot import config, game, render, texts


def test_penalty_message_does_not_include_absence_damage():
    events = game.DayEvents(
        new_day=True,
        missed=2,
        damage=36,  # 16 from missed quests + 20 from two skipped days
        skipped_days=2,
        streak_reset=True,
        hp=64,
        max_hp=config.HP_MAX,
        freezes=0,
    )

    messages = render.render_day_messages(events)
    penalty = next(msg.text for msg in messages if texts.HP_LOSS.split("{", 1)[0] in msg.text)

    assert "-16 HP" in penalty
    assert "-36 HP" not in penalty
