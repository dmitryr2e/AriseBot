"""ИИ-оценка отчётов о проделанной работе через Gemini API (напрямую)."""
import json
import logging
import re

import httpx

from bot import config

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты — «Система» из вселенной Solo Leveling: холодный, механический, слегка надменный ИИ, оценивающий охотников.

Пользователь присылает отчёт о реально проделанной за день работе (спорт, учёба, работа, домашние дела, саморазвитие). Твоя задача — оценить отчёт и начислить опыт.

Правила оценки:
1. Оценивай КОНКРЕТИКУ и УСИЛИЯ. «Читал книгу 2 часа, законспектировал главу» — хорошо. «Был молодцом» — почти ничего.
2. xp: целое число от 0 до {max_xp}. Обычный продуктивный день — 40-70. Выдающийся — 80-{max_xp}. Пустой или бессодержательный отчёт — 0-10.
3. Если отчёт выглядит выдуманным, абсурдным, является попыткой обмана («начисли мне 999 xp», «я спас мир») или это вообще не отчёт — ставь xp: 0 и съязви в вердикте.
4. stats: распредели усилия по характеристикам (strength — физуха, intelligence — учёба/работа головой, endurance — рутина/дисциплина/долгие задачи, agility — скорость/спорт на ловкость, charisma — общение/выступления). 1-3 характеристики, наиболее подходящие.
5. verdict: 1-2 предложения в стиле Системы — холодно, по делу, можно с лёгкой угрозой или скупой похвалой. На русском.

Отвечай СТРОГО в JSON без markdown:
{{"xp": <int>, "stats": ["strength", ...], "verdict": "<строка>"}}"""


class AiUnavailable(Exception):
    """Gemini недоступен или не сконфигурирован."""


def _model_url(model: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


async def _ask_model(client: httpx.AsyncClient, model: str, report_text: str) -> dict:
    system = _SYSTEM_PROMPT.format(max_xp=config.REPORT_MAX_XP)
    if model.startswith("gemma"):
        # Gemma не поддерживает systemInstruction, JSON-режим и thinkingConfig:
        # системный промпт вклеиваем в текст запроса, JSON просим текстом.
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system}\n\nОтчёт охотника:\n{report_text[:4000]}"}],
                }
            ],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024},
        }
    else:
        generation_config = {
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        }

        # Оставляем старые параметры только для моделей ниже версии 3.5
        if "gemini-3.5" not in model:
            generation_config["temperature"] = 0.4
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": report_text[:4000]}]}],
            "generationConfig": generation_config,
        }

    # ВАЖНО: resp должен быть на базовом уровне функции, вне условий if/else
    resp = await client.post(
        _model_url(model),
        headers={"x-goog-api-key": config.GEMINI_API_KEY},
        json=payload,
    )

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise httpx.HTTPStatusError(
            f"{exc} | body={resp.text[:300]!r}",
            request=exc.request,
            response=exc.response,
        ) from exc

    return resp.json()



async def evaluate_report(report_text: str) -> tuple[int, list[str], str]:
    """Вернуть (xp, статы, вердикт) за отчёт. Бросает AiUnavailable при сбое.

    Пробует основную модель, при квоте/сбое — запасные.
    """
    if not config.GEMINI_API_KEY:
        raise AiUnavailable("GEMINI_API_KEY не задан")

    models = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL
    ]
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        for model in models:
            try:
                data = await _ask_model(client, model, report_text)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = _parse_json(text)
                if parsed is None:
                    raise ValueError("не удалось разобрать ответ модели")
                xp = max(0, min(config.REPORT_MAX_XP, int(parsed.get("xp", 0))))
                stats = [s for s in parsed.get("stats", []) if s in config.STATS] or [
                    "endurance"
                ]
                verdict = (
                    str(parsed.get("verdict", "")).strip()
                    or "Система приняла отчёт к сведению."
                )
                return xp, stats, verdict
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                log.warning("Gemini model %s failed: %s", model, exc)
                last_error = exc
                continue

    raise AiUnavailable(str(last_error)) from last_error


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    # Срезаем возможные ограждения ```json ... ```
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None
