from bot import config, keyboards


def test_premium_upsell_uses_current_name():
    kb = keyboards.upsell(config.UPSELL_PREMIUM)
    assert kb is not None
    assert "Восходящий" in kb.inline_keyboard[0][0].text
    assert "Монарх" not in kb.inline_keyboard[0][0].text
