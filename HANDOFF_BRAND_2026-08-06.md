# ARISE rebrand handoff: 2026-08-06

## Сессия
- Подтверждён переход бренда SoloLevelingBot -> ARISE.
- Подтверждено новое имя premium-уровня: **Восходящий**.
- #24 merged: landing, metadata, AI prompt, boss name Игрис -> Мортекс, module label.
- #25 merged: generated card renderer uses ARISE SYSTEM, Игрок, Восходящий.
- #26 merged: premium copy and legal pages, `Монарх` -> `Восходящий`, old IP/fan-project wording removed.

## Сессия 4 (эта ветка): восстановление после провала #27
- **PR #27 закрыт без слияния и в main не попал.** Причина: модуль `bot/texts.py` был
  переписан с нуля (680 -> 122 строки) вместо точечной замены строк. Bot CI (lint + tests)
  упал; Landing и CodeQL были зелёными.
- Что именно ломалось в #27 и чего нельзя повторять:
  - пропали константы `HIDE_ON` и `HIDE_OFF` — их читает `/hideme`
    (`bot/handlers/social.py`), это `AttributeError` в рантайме;
  - `DELETE_ME_CONFIRM` потерял плейсхолдеры `{level}`, `{rank}`, `{streak}`,
    которые передаёт хендлер;
  - юридически значимые тексты (`PAYSUPPORT`, `PREMIUM_INFO`, онбординг) были
    сокращены до неузнаваемости.
- Эта ветка правит `bot/texts.py` **только заменой строк**: все имена констант,
  плейсхолдеры, порядок и комментарии сохранены.
  - докстринг больше не ссылается на Solo Leveling;
  - `SYS` -> `⟦ ARISE ⟧`, синхронно с шапкой карточки в `bot/card.py`;
  - `Монарх` -> `Восходящий` во всех пользовательских строках;
  - `охотник` -> `игрок`, чтобы текст бота совпадал с генератором карточек из #25;
  - восстановлены символы, побитые прошлыми правками (`СБОЙ СВЯЗИ`, `HIDE_OFF`,
    `BOSS_STATUS`, `PAY_PREMIUM_DESC`, `PAY_FREEZE_DESC`, `DELETE_ME_CONFIRM`,
    `TZ_PROMPT`, `RATING_EMPTY`, `PREMIUM_MENU`, `ONBOARDING_FIRST_QUEST`).
- Слово «Система» осознанно оставлено как внутриигровой голос: это общий троп
  жанра, а бренд несёт заголовок `⟦ ARISE ⟧`.

## Что ещё осталось
- Перегенерировать `public/hunter-card-sample.png` и `public/hunter-card-monarch.png`
  текущим `bot/card.py` с вымышленными именами; переименовать второй файл только
  вместе со всеми ссылками на него.
- Решить судьбу термина «Врата» (`GATE_OPENED`, `bot/quests_pool.py`, `bot/game.py`,
  `bot/render.py`, `config.GATE_CHANCE`). Сейчас не тронут намеренно: менять его надо
  сквозным заходом по всем слоям, а не в одном тексте.
- Заменить дефолтный домен `sololevelingbot.vercel.app` в `bot/config.py` и `lib/site.ts`.
- Проверить `app/terms/page.tsx`, `app/privacy/page.tsx`, `app/layout.tsx`,
  `components/landing/*` на остатки `SoloLevelingBot` после мержа #26.
- Обновить корневой `HANDOFF.md` кратким резюме, когда ребрендинг закроется.

## Правило хендоффа
Этот файл — durable-заметка для мультиагентных сессий. Держать его в актуальном
состоянии после каждой сессии. Провал #27 — назидательный пример: при копирайт-правках
нельзя ломать публичный API модуля.

## Verification
- #24/#25/#26 CI был зелёный: bot lint+tests, landing types+build, Docker, CodeQL.
