"""Process-wide ledger of LLM spend: Jev decisions, Kimi calls, Kimi avoided.

* **Jev** — calls, input tokens and cost exactly as OpenRouter reports them.
* **Kimi** — calls and tokens exactly as Moonshot reports them, priced at the
  kimi-k2.6 list price (the account's model; cache hits at the cache rate).
* **Avoided** — for every decision Jev took over, the Kimi call it replaced,
  per site. This is an ESTIMATE: the agent's real system prompt and the
  payload it would have been sent, at ~4 characters per token, plus a typical
  output length. It is labelled as such everywhere it is shown.

The API server reports the running totals at ``GET /llm/usage`` and attaches
each job's share (a before/after difference; jobs run one at a time) to the
job result as ``llm_usage``.
"""

from __future__ import annotations

import copy
import threading
from functools import lru_cache
from typing import Any

__all__ = ["KIMI_PRICE_PER_MTOK", "diff", "record_avoided", "record_jev", "record_kimi",
           "snapshot"]

#: Moonshot kimi-k2.6 list price, USD per 1M tokens (platform.kimi.ai pricing).
KIMI_PRICE_PER_MTOK: dict[str, float] = {"input": 0.95, "cached_input": 0.16, "output": 4.00}
_CHARS_PER_TOKEN = 4.0

_lock = threading.Lock()
_ledger: dict[str, Any] = {
    "jev": {"calls": 0, "input_tokens": 0, "cost_usd": 0.0},
    "kimi": {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0},
    "avoided_kimi_estimate": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                              "by_site": {}},
}


def kimi_cost(input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    p = KIMI_PRICE_PER_MTOK
    fresh = max(0, input_tokens - cached_input_tokens)
    return (fresh * p["input"] + cached_input_tokens * p["cached_input"]
            + output_tokens * p["output"]) / 1e6


def record_jev(input_tokens: int, cost_usd: float) -> None:
    with _lock:
        j = _ledger["jev"]
        j["calls"] += 1
        j["input_tokens"] += int(input_tokens)
        j["cost_usd"] += float(cost_usd)


def record_kimi(input_tokens: int, output_tokens: int, cached_input_tokens: int = 0,
                calls: int = 1) -> None:
    with _lock:
        k = _ledger["kimi"]
        k["calls"] += calls
        k["input_tokens"] += int(input_tokens)
        k["cached_input_tokens"] += int(cached_input_tokens)
        k["output_tokens"] += int(output_tokens)
        k["cost_usd"] += kimi_cost(int(input_tokens), int(output_tokens), int(cached_input_tokens))


@lru_cache(maxsize=32)
def _prompt_tokens(agent: str) -> int:
    from heaviside.agents.llm_call import load_prompt

    return int(len(load_prompt(agent)) / _CHARS_PER_TOKEN)


def record_avoided(site: str, agent: str | None, *, payload_chars: int, output_tokens: int,
                   calls: int = 1) -> None:
    """Estimate and record the Kimi call(s) a Jev decision replaced.

    ``agent`` names the prompt Kimi would have run (its system prompt is sent
    once per call); ``payload_chars`` is the user message it would have got.
    """
    tin = int(payload_chars / _CHARS_PER_TOKEN) + (calls * _prompt_tokens(agent) if agent else 0)
    cost = kimi_cost(tin, output_tokens)
    with _lock:
        a = _ledger["avoided_kimi_estimate"]
        a["calls"] += calls
        a["input_tokens"] += tin
        a["output_tokens"] += int(output_tokens)
        a["cost_usd"] += cost
        s = a["by_site"].setdefault(site, {"calls": 0, "decisions": 0, "cost_usd": 0.0})
        s["calls"] += calls
        s["decisions"] += 1
        s["cost_usd"] += cost


def snapshot() -> dict[str, Any]:
    with _lock:
        snap = copy.deepcopy(_ledger)
    saved = snap["avoided_kimi_estimate"]["cost_usd"] - snap["jev"]["cost_usd"]
    snap["net_saving_estimate_usd"] = saved
    return snap


def diff(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """``after - before`` for every numeric leaf (a job's share of the ledger)."""
    def sub(a: Any, b: Any) -> Any:
        if isinstance(a, dict):
            return {k: sub(v, (b or {}).get(k, 0 if not isinstance(v, dict) else {}))
                    for k, v in a.items()}
        if isinstance(a, (int, float)):
            out = a - (b or 0)
            return round(out, 8) if isinstance(out, float) else out
        return a

    return sub(after, before)


#: Heaviside's website in the shared OpenMagnetics Umami (the id App.vue injects).
UMAMI_WEBSITE_ID_DEFAULT = "2e9c5afa-bf1f-41ee-949f-62fa9e0639f5"


def umami_event_data(job_kind: str, share: dict[str, Any]) -> dict[str, Any]:
    """The flat numeric properties one job reports to Umami."""
    j, k, a = share["jev"], share["kimi"], share["avoided_kimi_estimate"]
    return {
        "job_kind": job_kind,
        "jev_calls": j["calls"],
        "jev_cost_usd": round(j["cost_usd"], 6),
        "kimi_calls": k["calls"],
        "kimi_input_tokens": k["input_tokens"],
        "kimi_output_tokens": k["output_tokens"],
        "kimi_cost_usd": round(k["cost_usd"], 6),
        "kimi_calls_avoided_est": a["calls"],
        "kimi_cost_avoided_est_usd": round(a["cost_usd"], 6),
        "net_saving_est_usd": round(a["cost_usd"] - j["cost_usd"], 6),
    }


def send_to_umami(job_kind: str, share: dict[str, Any]) -> None:
    """Post one ``llm_usage`` event for a finished job to Umami.

    Only when ``HEAVISIDE_UMAMI_URL`` is set (prod: the co-hosted instance at
    ``http://127.0.0.1:3001``), so dev never reports. Analytics must not fail a
    design: a send error is logged as a warning, not raised.
    """
    import logging
    import os

    url = os.environ.get("HEAVISIDE_UMAMI_URL")
    if not url:
        return
    import httpx

    body = {"type": "event", "payload": {
        "website": os.environ.get("HEAVISIDE_UMAMI_WEBSITE_ID", UMAMI_WEBSITE_ID_DEFAULT),
        "hostname": os.environ.get("HEAVISIDE_UMAMI_HOSTNAME", "heaviside.openconverters.com"),
        "url": f"/jobs/{job_kind}", "name": "llm_usage",
        "data": umami_event_data(job_kind, share)}}
    try:
        r = httpx.post(url.rstrip("/") + "/api/send", json=body, timeout=10,
                       headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) heaviside-server"})
        if r.status_code >= 300:
            logging.getLogger(__name__).warning("umami llm_usage event rejected: HTTP %s %s",
                                                r.status_code, r.text[:200])
    except httpx.HTTPError as exc:
        logging.getLogger(__name__).warning("umami llm_usage event not sent: %s", exc)
