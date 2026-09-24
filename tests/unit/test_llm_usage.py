"""The LLM spend ledger: measured Jev/Kimi usage, estimated Kimi avoided."""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.llm import usage


def test_kimi_cost_uses_the_cache_rate_for_cached_tokens() -> None:
    p = usage.KIMI_PRICE_PER_MTOK
    assert usage.kimi_cost(1_000_000, 0) == pytest.approx(p["input"])
    assert usage.kimi_cost(1_000_000, 0, 1_000_000) == pytest.approx(p["cached_input"])
    assert usage.kimi_cost(0, 1_000_000) == pytest.approx(p["output"])


def test_job_share_is_the_difference() -> None:
    before = usage.snapshot()
    usage.record_jev(1000, 0.00005)
    usage.record_kimi(2000, 100)
    usage.record_avoided("review_gate", None, payload_chars=4000, output_tokens=200)
    share = usage.diff(usage.snapshot(), before)
    assert share["jev"] == {"calls": 1, "input_tokens": 1000, "cost_usd": pytest.approx(0.00005)}
    assert share["kimi"]["calls"] == 1 and share["kimi"]["input_tokens"] == 2000
    assert share["avoided_kimi_estimate"]["by_site"]["review_gate"]["decisions"] == 1
    assert share["avoided_kimi_estimate"]["input_tokens"] == 1000  # 4000 chars / 4


def test_avoided_call_counts_the_agent_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    usage._prompt_tokens.cache_clear()
    before = usage.snapshot()
    usage.record_avoided("topology_selector", "topology-selector", payload_chars=0,
                         output_tokens=0, calls=1)
    share = usage.diff(usage.snapshot(), before)
    from heaviside.agents.llm_call import load_prompt

    assert share["avoided_kimi_estimate"]["input_tokens"] == int(len(load_prompt("topology-selector")) / 4)


def test_umami_is_silent_without_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEAVISIDE_UMAMI_URL", raising=False)
    import httpx

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("must not post without HEAVISIDE_UMAMI_URL")

    monkeypatch.setattr(httpx, "post", boom)
    usage.send_to_umami("crossref", usage.snapshot())


def test_umami_event_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEAVISIDE_UMAMI_URL", "http://umami.test")
    import httpx

    sent: dict[str, Any] = {}

    class R:
        status_code = 200
        text = ""

    def post(url: str, json: dict[str, Any], **k: Any) -> R:
        sent.update(url=url, body=json)
        return R()

    monkeypatch.setattr(httpx, "post", post)
    before = usage.snapshot()
    usage.record_jev(10, 0.001)
    usage.send_to_umami("crossref", usage.diff(usage.snapshot(), before))
    assert sent["url"] == "http://umami.test/api/send"
    p = sent["body"]["payload"]
    assert p["name"] == "llm_usage" and p["website"] == usage.UMAMI_WEBSITE_ID_DEFAULT
    assert p["data"]["jev_calls"] == 1 and p["data"]["jev_cost_usd"] == 0.001
    assert p["data"]["net_saving_est_usd"] == -0.001


def test_each_job_carries_its_own_usage_share(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from heaviside.api.jobs import JobRegistry

    monkeypatch.delenv("HEAVISIDE_UMAMI_URL", raising=False)
    reg = JobRegistry(persist_dir=tmp_path)

    def job() -> dict[str, Any]:
        usage.record_jev(100, 0.002)
        return {"ok": True}

    jid = reg.submit("crossref", job)
    t0 = time.monotonic()
    while reg.get(jid).status not in ("done", "error") and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    j = reg.get(jid)
    assert j.status == "done" and j.result == {"ok": True}
    assert j.llm_usage["jev"]["calls"] == 1
    assert j.llm_usage["jev"]["cost_usd"] == pytest.approx(0.002)
