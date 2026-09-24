"""Jev — TypeSafe's structured decision model, for Heaviside's closed choices.

Jev (``typesafe/jev-1.13`` on OpenRouter) never generates text. It reads a
``state`` (text or JSON) and answers typed questions:

* ``choice`` — pick one key of ``criteria`` (a map key -> description);
  returns the key, per-key probabilities and a concentration ``confidence``.
* ``noul``   — P(statement is true), 0..1; ``criteria`` optionally describes
  both sides as ``{"true": ..., "false": ...}``.
* ``score``  — an ordered scale; the returned score is the probability-weighted
  mean level index (fractional).

Every closed decision in the pipeline (pick one of N candidates, which status,
which header, is this topology viable, is this row worth challenging, does
this parameter disqualify the substitute) goes through here. Anything that
needs prose, reasoning traces or tool calls stays on Kimi.

The vendor's guidance, which the callers follow: all meaning goes in
``instructions`` and ``criteria`` (question ids never reach the model); the data
being judged goes in ``state``, with field names in backticks; independent
questions share one call; thresholds are tuned on labelled data, because
``confidence`` measures how peaked the distribution is, not whether it is right.

No silent fallbacks: a missing key, a non-200 after retries, or an answer
missing from the response raises :class:`JevError`.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "JEV_MODEL_ID",
    "JevChoice",
    "JevError",
    "choice_question",
    "decide",
    "decide_choice",
    "get_jev_usage",
    "jev_configured",
    "noul_question",
    "noul_values",
]

JEV_MODEL_ID: str = "typesafe/jev-1.13"
JEV_DECISIONS_URL: str = "https://openrouter.ai/api/alpha/decisions"

#: Jev's context is 32k tokens for the state plus the longest question.
#: ~4 chars/token, with headroom for the question text.
_MAX_STATE_CHARS: int = 100_000
#: Independent questions share a call; chunk very long question sets so a
#: single request stays well inside the context and payload limits.
_MAX_QUESTIONS_PER_CALL: int = 64

_usage = {"calls": 0, "input_tokens": 0, "cost": 0.0}


class JevError(RuntimeError):
    """A Jev decision could not be obtained (no key, HTTP error, bad answer)."""


@dataclass(frozen=True)
class JevChoice:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None


def jev_configured() -> bool:
    """True when an OpenRouter key is present to reach Jev."""
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def get_jev_usage() -> dict[str, Any]:
    return dict(_usage)


def choice_question(instructions: str, options: dict[str, str]) -> dict[str, Any]:
    if not options:
        raise JevError("choice question needs at least one option")
    if len(options) > 255:
        raise JevError(f"choice question has {len(options)} options; Jev accepts at most 255")
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def noul_question(instructions: str, *, true: str | None = None,
                  false: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if (true is None) != (false is None):
        raise JevError("noul criteria must describe both sides or neither")
    if true is not None:
        q["criteria"] = {"true": true, "false": false}
    return q


def _post(body: dict[str, Any], *, max_retries: int = 4) -> dict[str, Any]:
    import httpx

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise JevError("OPENROUTER_API_KEY is not set; Jev decisions are unavailable")
    url = os.environ.get("HEAVISIDE_JEV_URL", JEV_DECISIONS_URL)
    last = ""
    for attempt in range(max_retries + 1):
        try:
            r = httpx.post(url, json=body, timeout=60,
                           headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            last = f"transport error: {exc}"
        else:
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (408, 429) and r.status_code < 500:
                break  # a request error will not fix itself on retry
        if attempt < max_retries:
            time.sleep(min(2 ** attempt + random.random(), 20))
    raise JevError(f"Jev decision failed ({last})")


def decide(state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Ask Jev every question about ``state``; return ``{question_id: answer}``.

    Raises :class:`JevError` if any question comes back unanswered.
    """
    if not questions:
        return {}
    state_chars = len(state) if isinstance(state, str) else len(json.dumps(state, default=str))
    if state_chars > _MAX_STATE_CHARS:
        raise JevError(f"Jev state is {state_chars} chars; the 32k-token context cannot hold it — "
                       "split the input before calling")
    ids = list(questions)
    answers: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _MAX_QUESTIONS_PER_CALL):
        chunk = {k: questions[k] for k in ids[i:i + _MAX_QUESTIONS_PER_CALL]}
        data = _post({"model": os.environ.get("HEAVISIDE_JEV_MODEL", JEV_MODEL_ID),
                      "state": state, "questions": chunk})
        got = data.get("answers") or {}
        missing = [k for k in chunk if k not in got]
        if missing:
            raise JevError(f"Jev returned no answer for {missing}")
        answers.update(got)
        usage = data.get("usage") or {}
        from heaviside.llm.usage import record_jev

        record_jev(int(usage.get("input_tokens") or 0), float(usage.get("cost") or 0.0))
        _usage["calls"] += 1
        _usage["input_tokens"] += int(usage.get("input_tokens") or 0)
        _usage["cost"] += float(usage.get("cost") or 0.0)
    return answers


def decide_choice(state: Any, instructions: str, options: dict[str, str]) -> JevChoice:
    """One ``choice`` question; the returned key is always one of ``options``."""
    a = decide(state, {"q": choice_question(instructions, options)})["q"]
    picked = a.get("choice")
    if picked not in options:
        raise JevError(f"Jev chose {picked!r}, not one of {sorted(options)}")
    return JevChoice(picked, {k: float(v) for k, v in (a.get("probabilities") or {}).items()},
                     a.get("confidence"))


def noul_values(answers: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, float]:
    """Pull the P(true) of each ``noul`` answer, failing loudly on a bad shape."""
    out: dict[str, float] = {}
    for k in ids:
        v = answers[k].get("noul")
        if not isinstance(v, (int, float)):
            raise JevError(f"Jev noul answer {k!r} has no probability: {answers[k]!r}")
        out[k] = float(v)
    return out
