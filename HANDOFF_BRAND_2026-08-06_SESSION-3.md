# ARISE rebrand handoff: session 3, 2026-08-06

## Status
- PR #26 was merged successfully: legal pages and premium copy use `Восходящий`.
- PR #27 is intentionally not merged: an aggressive rewrite of `bot/texts.py` failed Bot CI during collection/tests.
- No failed-branch changes were merged into `main`.

## Recovery plan
- Restore `bot/texts.py` from `main` and make only exact string replacements, preserving every exported template name and placeholder used by handlers/tests.
- Replace only user-facing `Монарх` occurrences with `Восходящий`; do not rebuild the module from scratch.
- Run pytest and ruff before opening a replacement PR.

## Remaining brand work
- Validate all remaining `SoloLeveling`, `Монарх`, `Игрис`, and `SOLO LEVELING SYSTEM` occurrences.
- Regenerate the two static demo card PNGs with current `bot/card.py`.
- Decide on a real replacement for the default `sololevelingbot.vercel.app` domain.

## Important handoff rule
Keep this session note and the root `HANDOFF.md` aligned after each successful session. The failed PR #27 is a cautionary example: preserve module APIs during copy-only branding changes.
