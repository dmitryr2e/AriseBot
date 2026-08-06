# ARISE rebrand handoff: 2026-08-06

## Сессия
- Подтверждён переход бренда SoloLevelingBot -> ARISE.
- Подтверждено новое имя premium-уровня: **Восходящий**.
- #24 merged: landing, metadata, AI prompt, boss name Игрис -> Мортекс, module label.
- #25 merged: generated card renderer uses ARISE SYSTEM, Игрок, Восходящий.
- This branch updates premium copy and legal pages: `Монарх` -> `Восходящий`, old IP/fan-project wording removed.

## Что ещё проверить
- Перегенерировать `public/hunter-card-sample.png` и `public/hunter-card-monarch.png` with current `bot/card.py`; rename the second asset only if all references are updated.
- Update remaining bot user-facing strings in `bot/texts.py` and any internal config labels that still say `Монарх`.
- Decide whether to replace the default `sololevelingbot.vercel.app` domain. Keep env overrides synchronized in `bot/config.py` and `lib/site.ts` until the real domain exists.
- Update root `HANDOFF.md` with this session summary when convenient; this file is the durable session note for multi-AI handoff.

## Verification
- #24/#25 CI was green: bot lint+tests, landing types+build, Docker, CodeQL.
