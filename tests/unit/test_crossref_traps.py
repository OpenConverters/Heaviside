"""Trap-fixture harness for the crossref v2 correctness gates (no LLM, no tokens).

Each test poses a *named trap* — a substitute that is wrong in one specific,
physically-meaningful way — and asserts the deterministic chain catches it. This
is the layer that was missing when a 330 nH inductor shipped as a "partial"
substitute for a 1.5 µH original: the comparator mechanics had unit tests, but
nothing asserted that the assembled gate REJECTS a bad part.

Parts are real Würth Elektronik MPNs resolved from the internal DB so the stage
reads authentic electrical values:

  * ``744383560R33`` — WE-MAPI, 330 nH (the exact substitute from the screenshot)
  * ``74438356015``  — WE-PD, 1.5 µH (a genuine in-kind match for the original)

The original ``IHLP1616ABER1R5M11`` (Vishay, 1.5 µH) is deliberately NOT in the
Würth DB — mirroring a real cross-reference where the competitor original is
known only by its BOM value.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from heaviside.pipeline.crossref_pipeline import (
    _best_inkind_candidate,
    _stage_param_check,
)
from heaviside.stages.component_match import find_candidates

PART_330NH = "744383560R33"  # 330 nH — the wrong-value trap
PART_1P5UH = "74438356015"  # 1.5 µH — the correct in-kind match


def _env(mpn: str, value_si: float, category: str = "magnetic"):
    """Fetch a real catalogue envelope for an exact MPN via find_candidates."""
    for c in find_candidates(
        category=category,
        target_manufacturer="Würth Elektronik",
        value_si=value_si,
        min_voltage=0,
        max_results=25,
    ):
        ref = c.env.get(category, {}).get("manufacturerInfo", {}).get("reference")
        if ref == mpn:
            return c.env
    raise AssertionError(f"fixture MPN {mpn} not found in internal DB")


def _state(rows: list[dict], source_bom: list[dict] | None = None):
    return SimpleNamespace(
        crossref_result=rows,
        stress_by_ref={},
        source_bom=source_bom if source_bom is not None else rows,
        target_manufacturer="Würth Elektronik",
    )


# ── The 330 nH regression, at the full stage level ───────────────────────────
class TestPrimaryValueGateStage:
    def test_330nH_substitute_for_1p5uH_is_rejected(self):
        # The exact screenshot: original 1.5 µH, substitute 330 nH (0.22×).
        row = {
            "ref_des": "L1",
            "component_type": "magnetic",
            "original_pn": "IHLP1616ABER1R5M11",
            "original_value": "1.5µH",
            "substitute_pn": PART_330NH,
            "substitute_value": "330nH",
            "status": "partial",
            "notes": "deterministic in-kind rescue",
        }
        _stage_param_check(_state([row]))
        assert row["status"] == "no_substitute", "a 4.5× value miss must not survive"
        assert row["substitute_pn"] is None
        assert "PRIMARY_VALUE" in row.get("guardrail_fires", [])
        assert "out of range" in row["notes"].lower()

    def test_looser_tolerance_resistor_demoted(self):
        # FAE-judge finding: Würth 560112110013 is ±5%, the Vishay original is
        # ±1%. The tolerance gate must demote 'recommended' → 'partial' and state
        # it honestly (the LLM prose had called it "0.05% tighter" — a misread of
        # the 0.05 fraction, which is 5%). Both parts are real and in the DB.
        row = {
            "ref_des": "R1",
            "component_type": "resistor",
            "original_pn": "CRCW040247K0FKED",
            "original_value": "47k",
            "substitute_pn": "560112110013",
            "substitute_value": "47k",
            "status": "recommended",
            "notes": "",
        }
        _stage_param_check(_state([row]))
        assert row["status"] == "partial"
        assert "PARAM:tolerance_pct" in row.get("guardrail_fires", [])
        tol = next(
            (p for p in row.get("_param_results", []) if p["name"] == "tolerance_pct"), None
        )
        assert tol is not None and tol["verdict"] == "fail"
        assert "5" in tol["substitute"] and "1" in tol["original"]

    def test_x5r_for_x7r_caught_via_max_temp(self):
        # FAE-judge finding: Würth 885012105016 is X5R (max +85°C); the Murata
        # original GRM155R71C104KA88 is X7R (+125°C). The coarse "ceramic-class-2"
        # bucket matched, but the max-operating-temperature gate must catch the
        # +125→+85 downgrade and demote recommended → partial.
        row = {
            "ref_des": "C1",
            "component_type": "capacitor",
            "original_pn": "GRM155R71C104KA88",
            "original_value": "0.1uF",
            "substitute_pn": "885012105016",
            "substitute_value": "0.1uF",
            "status": "recommended",
            "notes": "",
        }
        _stage_param_check(_state([row]))
        assert row["status"] == "partial"
        assert "PARAM:temp_max_C" in row.get("guardrail_fires", [])
        tmax = next(
            (p for p in row.get("_param_results", []) if p["name"] == "temp_max_C"), None
        )
        assert tmax is not None and tmax["verdict"] == "fail"

    def test_unverified_original_capped_to_partial(self):
        # P1: original not in the Würth DB (Vishay IHLP) → the match rests on
        # value/voltage/package only, so a 'recommended' must be capped to
        # 'partial' with an unverified-original caveat (never a HIGH exact match).
        row = {
            "ref_des": "L1", "component_type": "magnetic",
            # A part genuinely absent from the internal DB (the real FAE case was
            # Coilcraft XGL6060, never in the DB), so its ratings are unknown.
            "original_pn": "XGL6060-NOTINDB-1R5", "original_value": "1.5µH",
            "substitute_pn": PART_1P5UH, "substitute_value": "1.5µH",
            "status": "recommended", "notes": "",
        }
        _stage_param_check(_state([row]))
        assert row["status"] == "partial"
        assert "ORIGINAL_UNVERIFIED" in row.get("guardrail_fires", [])
        assert "unverified" in row["notes"].lower()

    def test_severe_isat_shortfall_rejected(self):
        # P3: original 74437377015 (Isat 25.5A) vs substitute 7440320015
        # (Isat 2.1A) — far below 70% → the garbage substitute must never ship.
        # The current-match rescue may REPLACE it with a real same-value part that
        # meets the 25.5A rating (a better outcome than no_substitute); either way
        # the 2.1A part is gone and the row is never 'recommended'/'exact'.
        row = {
            "ref_des": "L1", "component_type": "magnetic",
            "original_pn": "74437377015", "original_value": "1.5µH",
            "substitute_pn": "7440320015", "substitute_value": "1.5µH",
            "status": "recommended", "notes": "",
        }
        _stage_param_check(_state([row]))
        assert row["status"] not in ("recommended", "exact")
        assert row.get("substitute_pn") != "7440320015"  # garbage 2.1A part gone
        fires = row.get("guardrail_fires", [])
        if row["status"] == "no_substitute":
            assert any(f.startswith("CURRENT:") for f in fires)
        else:  # rescued to a real current-adequate part
            assert row["status"] == "partial" and "RESCUE:current_match" in fires

    def test_bom_rated_current_rejects_undersized_out_of_db_original(self):
        # P3 back-stop: the original isn't in the DB, but the BOM states 18 A —
        # a 2.1 A substitute must be rejected on the BOM current, not shipped as
        # a "partial" (the Coilcraft-power-inductor findings).
        row = {
            "ref_des": "L1", "component_type": "magnetic",
            "original_pn": "XGL4030-NOTINDB", "original_value": "1.5µH",
            "substitute_pn": "7440320015", "substitute_value": "1.5µH",
            "status": "recommended", "notes": "",
        }
        bom = [{"ref_des": "L1", "component_type": "magnetic",
                "value": "1.5µH", "rated_current": 18.0}]
        _stage_param_check(_state([row], source_bom=bom))
        assert row["status"] == "no_substitute"
        assert any(f.startswith("CURRENT:") for f in row.get("guardrail_fires", []))

    def test_matching_1p5uH_substitute_is_kept(self):
        # A genuine 1.5 µH match must NOT be rejected by the value gate.
        row = {
            "ref_des": "L1",
            "component_type": "magnetic",
            "original_pn": "IHLP1616ABER1R5M11",
            "original_value": "1.5µH",
            "substitute_pn": PART_1P5UH,
            "substitute_value": "1.5µH",
            "status": "partial",
            "notes": "",
        }
        _stage_param_check(_state([row]))
        assert row["status"] != "no_substitute"
        assert "PRIMARY_VALUE" not in row.get("guardrail_fires", [])


# ── The rescue gate: it must never PROPOSE the wrong-value part ───────────────
# For magnetics the rescue now also requires a current requirement (P7): a
# value-only magnetic rescue is unsafe, so these comps carry rated_current.
class TestRescueGateTraps:
    def test_wrong_value_candidate_alone_is_refused(self):
        # Only a 330 nH candidate available for a 1.5 µH original → no rescue.
        comp = {"value": "1.5µH", "component_type": "magnetic", "rated_current": 3.0}
        patch = _best_inkind_candidate(comp, "magnetic", [_env(PART_330NH, 330e-9)])
        assert patch is None

    def test_good_candidate_is_rescued(self):
        comp = {"value": "1.5µH", "component_type": "magnetic", "rated_current": 3.0}
        patch = _best_inkind_candidate(comp, "magnetic", [_env(PART_1P5UH, 1.5e-6)])
        assert patch is not None
        assert patch["substitute_pn"] == PART_1P5UH
        assert patch["status"] in ("recommended", "partial")

    def test_wrong_value_skipped_in_mixed_list(self):
        # A 330 nH candidate ranked ahead of a 1.5 µH one must be SKIPPED, and
        # the 1.5 µH one chosen — the gate can't be fooled by list position.
        comp = {"value": "1.5µH", "component_type": "magnetic", "rated_current": 3.0}
        cands = [_env(PART_330NH, 330e-9), _env(PART_1P5UH, 1.5e-6)]
        patch = _best_inkind_candidate(comp, "magnetic", cands)
        assert patch is not None
        assert patch["substitute_pn"] == PART_1P5UH

    def test_unparseable_original_value_refuses_rescue(self):
        # No parseable value on a value-matched part → cannot verify the
        # defining spec → refuse (no-fallbacks), never guess from list order.
        comp = {"component_type": "magnetic"}  # no value at all
        patch = _best_inkind_candidate(comp, "magnetic", [_env(PART_1P5UH, 1.5e-6)])
        assert patch is None

    def test_reads_value_not_legacy_value_si_key(self):
        # Regression: the rescue used to read comp['value_si']/['original_value'],
        # keys the normalized BOM row never carries — the value check silently
        # no-oped. It must now read comp['value'].
        comp = {"value": "1.5µH", "component_type": "magnetic", "rated_current": 3.0}
        good = _best_inkind_candidate(comp, "magnetic", [_env(PART_1P5UH, 1.5e-6)])
        bad = _best_inkind_candidate(comp, "magnetic", [_env(PART_330NH, 330e-9)])
        assert good is not None and bad is None

    def test_p7_magnetic_rescue_refused_without_current(self):
        # P7: a magnetic rescue with NO current requirement is unsafe (the
        # 210mA-RF-inductor-for-a-21A-power-part finding) — refuse, don't
        # value-match blindly.
        comp = {"value": "1.5µH", "component_type": "magnetic"}  # no current
        patch = _best_inkind_candidate(comp, "magnetic", [_env(PART_1P5UH, 1.5e-6)])
        assert patch is None

    def test_p7_undersized_magnetic_rescue_skipped(self):
        # P7: candidate 74438356015 (Isat 4.8A) can't carry a 10A requirement →
        # skipped, not rescued.
        comp = {"value": "1.5µH", "component_type": "magnetic", "rated_current": 10.0}
        patch = _best_inkind_candidate(comp, "magnetic", [_env(PART_1P5UH, 1.5e-6)])
        assert patch is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
