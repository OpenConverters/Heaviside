"""Regression: the RE spec-extract path must not emit the whole BOM in one
giant JSON.

The prod failure (profiling the GaN reference, ~50-component BOM): stage 1
``spec-extract`` was asked to return the SPEC *and* the COMPLETE bill of
materials in a single response. On a large board that overran the model's
``max_tokens`` ("Agent has reached an unrecoverable state due to max_tokens
limit") and both retries failed too — so the whole RE/CR run aborted.

The fix splits the responsibilities:

* spec-extract returns the spec + topology + a bounded ``key_components`` list
  (power-path only). Its token budget is fixed and modest — it no longer scales
  with PDF size, because it no longer carries the BOM.
* the COMPLETE parts census comes from the dedicated ``bom-extractor`` agent
  (``heaviside.stages.bom_extract._extract_full_bom_rows``): a BOM-only,
  json-mode, tool-less call whose compact output stays under the token cap.

All deterministic — the LLM boundary is mocked, no live LLM calls.
"""
from __future__ import annotations

from unittest.mock import patch

from heaviside.pipeline.re_pipeline import (
    _stage1_spec_extract,
    _stage2_reverse_engineer,
)
from heaviside.pipeline.re_state import REState

# The bom-extractor's max_tokens is capped here; the spec-extract output is
# bounded well below it. Both must stay under the model's single-shot context.
_BOM_TOKEN_CAP = 32768


def test_spec_extract_token_budget_is_bounded_for_a_huge_pdf() -> None:
    """spec-extract must request a small, FIXED max_tokens even for an enormous
    PDF — it no longer emits the full BOM, so its budget can't scale toward the
    model's single-shot limit (pre-fix it scaled to 32768 and overran)."""
    captured: dict[str, object] = {}

    def _fake_call(agent: str, msg: str, **kw: object) -> dict[str, object]:
        captured["agent"] = agent
        captured["max_tokens"] = kw.get("max_tokens")
        return {
            "specs": {
                "topology": "buck",
                "vin_min": 36,
                "vin_max": 60,
                "outputs": [{"voltage": 12, "current": 5, "power": 60}],
                "switching_frequency": 200000,
            },
            "performance": {"efficiency": 0.95},
            "key_components": [
                {"ref_des": "U1", "role": "controller", "mpn": "LM5"},
                {"ref_des": "L1", "role": "buckInductor", "mpn": "744"},
            ],
        }

    state = REState(reference="big-board")
    state.pdf_text = "X" * 800_000  # ~800k-char datasheet

    with patch("heaviside.pipeline.re_pipeline.call_agent_json", _fake_call):
        state = _stage1_spec_extract(state)

    assert captured["agent"] == "spec-extract"
    # Bounded and fixed — NOT scaled up to the model's single-shot cap.
    assert captured["max_tokens"] == 8192
    # The spec still parses out cleanly.
    assert state.ref_spec is not None
    assert state.ref_spec.topology == "buck"
    assert state.ref_spec.vout == 12.0
    # And spec-extract carried NO full BOM — that's stage 2's job now.
    assert not (state.extract_data or {}).get("bom")


def test_stage2_bom_comes_from_dedicated_extractor_and_is_complete() -> None:
    """A large (60-line) BOM must come back COMPLETE via the dedicated, bounded
    bom-extractor census — not a single-shot spec-extract emission. The
    bom-extractor's request must stay under the model's token cap."""
    big_rows = [
        {"ref_des": f"C{i}", "category": "capacitor", "mpn": f"CAP{i}", "value": "10uF"}
        for i in range(60)
    ]

    state = REState(reference="big-board")
    state.pdf_text = "PDF BODY " * 50_000
    # Roles for the power-path parts come from spec-extract's key_components and
    # must be overlaid onto the role-less census.
    state.extract_data = {"key_components": [{"ref_des": "C0", "role": "outputCap"}]}

    # _extract_full_bom_rows does a *local* `from heaviside.agents.llm_call import
    # call_agent_json`, so patch it at the source module — the local import
    # re-resolves the attribute at call time.
    with patch("heaviside.agents.llm_call.call_agent_json") as m:
        m.return_value = {"bom": big_rows}
        state = _stage2_reverse_engineer(state)

    # The full census came back — nothing dropped to a token cap.
    assert len(state.ref_bom) == 60
    assert state.ref_bom[0]["mpn"] == "CAP0"

    # It went to the dedicated bom-extractor agent, with a bounded budget.
    assert m.call_count == 1
    call_agent_name = m.call_args.args[0]
    assert call_agent_name == "bom-extractor"
    assert m.call_args.kwargs["max_tokens"] <= _BOM_TOKEN_CAP

    # The power-path role from key_components was overlaid by ref-des.
    assert state.ref_bom[0].get("role") == "outputCap"


def test_stage2_no_pdf_uses_reverse_engineer_agent_bounded() -> None:
    """With no PDF there is no census to extract; the small inferred BOM comes
    from the reverse-engineer agent with a bounded budget (no scaling)."""
    captured: dict[str, object] = {}

    def _fake_call(agent: str, msg: str, **kw: object) -> dict[str, object]:
        captured["agent"] = agent
        captured["max_tokens"] = kw.get("max_tokens")
        return {
            "bom": [
                {"ref_des": "Q1", "category": "mosfet", "mpn": "CSD19536KTT"},
                {"ref_des": "D1", "category": "diode", "mpn": "SS34"},
            ]
        }

    state = REState(reference="name-only-buck")  # no pdf_text

    with patch("heaviside.pipeline.re_pipeline.call_agent_json", _fake_call):
        state = _stage2_reverse_engineer(state)

    assert captured["agent"] == "reverse-engineer"
    assert captured["max_tokens"] == 8192
    assert len(state.ref_bom) == 2
    assert state.ref_bom[0]["mpn"] == "CSD19536KTT"
