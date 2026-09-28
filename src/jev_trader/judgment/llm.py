"""Frontier-LLM baseline: the same six questions, answered by an OpenAI chat model.

Used only by the Phase 3 backtest to compare against Jev on identical states.
The LLM returns a probability per option; code turns that into a JudgmentSet the
same way Jev's typed answers are read (Score = probability-weighted level).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any

from jev_trader.domain import JudgmentSet
from jev_trader.judgment.battery import build_questions

URL = "https://api.openai.com/v1/chat/completions"
SYSTEM = (
    "You are a market-microstructure judge for a market maker quoting a crypto perp. "
    "Given the market state JSON, answer every question with a probability for each "
    "option (probabilities for one question sum to 1). Reply in json only."
)


def _options(question: Any) -> list[str]:
    if isinstance(question.criteria, dict):
        return list(question.criteria)
    return [str(i) for i in range(len(question.criteria))]  # Score levels, low to high


def _describe(questions: dict[str, Any]) -> str:
    lines = []
    for key, q in questions.items():
        lines.append(f"{key}: {q.instructions}")
        criteria = q.criteria
        items = criteria.items() if isinstance(criteria, dict) else enumerate(criteria)
        lines += [f"  {name}: {meaning or name}" for name, meaning in items]
    return "\n".join(lines)


def _schema(questions: dict[str, Any]) -> dict[str, Any]:
    def probs(options: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {o: {"type": "number"} for o in options},
            "required": options,
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {k: probs(_options(q)) for k, q in questions.items()},
        "required": list(questions),
        "additionalProperties": False,
    }


def _normalize(raw: dict[str, float]) -> dict[str, float]:
    clipped = {k: max(0.0, float(v)) for k, v in raw.items()}
    total = sum(clipped.values()) or 1.0
    return {k: v / total for k, v in clipped.items()}


def parse_answer(answer: dict[str, dict[str, float]], **meta: Any) -> JudgmentSet:
    p = {k: _normalize(v) for k, v in answer.items()}

    def choice(key: str) -> tuple[str, float, dict[str, float]]:
        best = max(p[key], key=p[key].__getitem__)
        return best, p[key][best], p[key]

    def score(key: str) -> tuple[float, float]:
        return sum(int(level) * q for level, q in p[key].items()), max(p[key].values())

    regime, regime_conf, regime_probs = choice("regime")
    direction, direction_conf, direction_probs = choice("direction")
    environment, environment_conf = score("quote_environment")
    pressure, pressure_conf = score("inventory_pressure")
    return JudgmentSet(
        regime=regime,
        regime_confidence=regime_conf,
        regime_probabilities=regime_probs,
        direction=direction,
        direction_confidence=direction_conf,
        direction_probabilities=direction_probs,
        toxic_flow=p["toxic_flow"]["true"],
        liquidity_stressed=p["liquidity_stressed"]["true"],
        quote_environment=environment,
        quote_environment_confidence=environment_conf,
        inventory_pressure=pressure,
        inventory_pressure_confidence=pressure_conf,
        source="llm",
        **meta,
    )


class LLMJudge:
    """OpenAI chat-completions judge. Stdlib HTTP in a thread; no new dependency."""

    def __init__(self, api_key: str, model: str = "gpt-5.4-mini") -> None:
        self._api_key = api_key
        self._model = model
        questions = build_questions()
        self._prompt = _describe(questions)
        self._schema = _schema(questions)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    async def judge(self, state: Any) -> JudgmentSet:
        body = {
            "model": self._model,
            "reasoning_effort": "none",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": f"State:\n{json.dumps(state.to_state_dict())}\n\n"
                    f"Questions:\n{self._prompt}",
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "judgments", "strict": True, "schema": self._schema},
            },
        }
        started = time.perf_counter()
        response = await asyncio.to_thread(self._post, body)
        latency_ms = (time.perf_counter() - started) * 1000.0
        usage = response.get("usage", {})
        return parse_answer(
            json.loads(response["choices"][0]["message"]["content"]),
            model=response.get("model", self._model),
            latency_ms=latency_ms,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
