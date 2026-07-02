"""Footprint-fit ranking: a substitute must fit the original's board space.

Regression for the LPS5030 → 744771122 bug, where a 12×12 mm Würth inductor was
cross-referenced for a 4.9×4.9 mm Coilcraft part because dimensions were not a
matching criterion. The rule (per product spec) applies to every category:
the substitute must occupy no more board space than the original, smaller is
better, and oversize is heavily penalised but still selectable as a last resort.
"""

from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import (
    _OVERSIZE_BASE,
    _candidate_summaries_for_llm,
    _eia_dims_from_case,
    _envelope_reference,
    _extract_dimensions,
    _footprint_penalty,
    _rank_candidates,
)


def _mag(ref: str, ind: float, dims: tuple[float, float, float] | None) -> dict:
    ds: dict = {
        "part": {"caseCode": "x"},
        "electrical": [
            {"subtype": "inductor", "inductance": {"nominal": ind}, "ratedCurrents": [1.0]}
        ],
    }
    if dims:
        ds["mechanical"] = {
            "length": {"nominal": dims[0]},
            "width": {"nominal": dims[1]},
            "height": {"nominal": dims[2]},
        }
    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": ref,
                "datasheetInfo": ds,
            }
        }
    }


def _cap(ref: str, cap: float, case: str) -> dict:
    return {
        "capacitor": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": ref,
                "datasheetInfo": {
                    "part": {"case": case},
                    "electrical": {"capacitance": {"nominal": cap}, "ratedVoltage": 50.0},
                },
            }
        }
    }


# --- dimension extraction --------------------------------------------------


def test_eia_case_code_maps_to_standard_footprint() -> None:
    assert _eia_dims_from_case("0402") == (0.0010, 0.0005)
    assert _eia_dims_from_case("C0805") == (0.0020, 0.00125)
    # A Würth magnetic case code is not an EIA chip size.
    assert _eia_dims_from_case("1260") is None
    assert _eia_dims_from_case(None) is None


def test_magnetic_dimensions_read_from_mechanical_block() -> None:
    env = _mag("744771122", 22e-6, (0.012, 0.012, 0.0058))
    assert _extract_dimensions(env, "magnetic") == (0.012, 0.012, 0.0058)


def test_capacitor_dimensions_fall_back_to_eia_case_code() -> None:
    env = _cap("WCAP-0402", 1e-9, "0402")
    assert _extract_dimensions(env, "capacitor") == (0.0010, 0.0005, None)


def test_dimensions_none_when_nothing_known() -> None:
    env = _mag("NO-DIMS", 22e-6, None)
    assert _extract_dimensions(env, "magnetic") is None


# --- footprint penalty -----------------------------------------------------


def test_oversize_part_heavily_penalised() -> None:
    src = (0.00493, 0.00493, 0.0030)  # LPS5030
    big = _footprint_penalty(src, (0.012, 0.012, 0.0058))  # 744771122
    assert big > _OVERSIZE_BASE


def test_smaller_is_better_among_fitting_parts() -> None:
    src = (0.00493, 0.00493, 0.0030)
    same = _footprint_penalty(src, (0.00493, 0.00493, 0.0030))
    smaller = _footprint_penalty(src, (0.004, 0.004, 0.0025))
    assert 0.0 < smaller < same < _OVERSIZE_BASE


def test_unknown_candidate_dims_penalised_between_fit_and_oversize() -> None:
    src = (0.00493, 0.00493, 0.0030)
    pen = _footprint_penalty(src, None)
    assert _footprint_penalty(src, (0.004, 0.004, 0.0025)) < pen < _OVERSIZE_BASE


def test_no_source_dims_means_no_penalty() -> None:
    # Cannot enforce fit without the original's size — surfaced as a diagnostic
    # elsewhere, but it must not silently penalise candidates here.
    assert _footprint_penalty(None, (0.012, 0.012, 0.0058)) == 0.0


def test_rotated_part_that_still_fits_is_not_penalised_as_oversize() -> None:
    src = (0.006, 0.003, 0.002)
    # Same footprint rotated 90°: long/short still fit.
    assert _footprint_penalty(src, (0.003, 0.006, 0.002)) < _OVERSIZE_BASE


# --- end-to-end ranking ----------------------------------------------------


def test_ranking_demotes_oversize_inductor_below_fitting_ones() -> None:
    big = _mag("744771122", 22e-6, (0.012, 0.012, 0.0058))
    fit_perfect = _mag("SMALL-22u", 22e-6, (0.004, 0.004, 0.0025))
    fit_close = _mag("SMALL-20u", 20e-6, (0.0045, 0.0045, 0.0028))
    comp = {
        "value": "22uH",
        "component_type": "magnetic",
        "_source_dims_m": (0.00493, 0.00493, 0.0030),
    }
    ranked = _rank_candidates(comp, "magnetic", [big, fit_perfect, fit_close], max_results=10)
    order = [_envelope_reference(c, "magnetic") for c in ranked]
    assert order[0] == "SMALL-22u"  # best fit: exact value, smallest fitting body
    # Oversize substitutes are dropped as a last resort when fitting parts exist
    # (crossref: "larger-package substitutes are a last resort", 7c00cac), so the
    # oversize 744771122 must NOT appear here; cf.
    # test_oversize_still_selectable_when_nothing_fits below.
    assert "744771122" not in order
    assert set(order) == {"SMALL-22u", "SMALL-20u"}


def test_oversize_still_selectable_when_nothing_fits() -> None:
    big = _mag("744771122", 22e-6, (0.012, 0.012, 0.0058))
    comp = {
        "value": "22uH",
        "component_type": "magnetic",
        "_source_dims_m": (0.00493, 0.00493, 0.0030),
    }
    ranked = _rank_candidates(comp, "magnetic", [big], max_results=10)
    assert [_envelope_reference(c, "magnetic") for c in ranked] == ["744771122"]


def test_summary_tags_fits_original_verdict() -> None:
    src = (0.00493, 0.00493, 0.0030)
    cands = [
        _mag("744771122", 22e-6, (0.012, 0.012, 0.0058)),
        _mag("SMALL-22u", 22e-6, (0.004, 0.004, 0.0025)),
        _mag("NO-DIMS", 22e-6, None),
    ]
    summaries = _candidate_summaries_for_llm(cands, "magnetic", src, limit=10)
    by_mpn = {s["mpn"]: s for s in summaries}
    assert by_mpn["744771122"]["fits_original"] is False
    assert by_mpn["SMALL-22u"]["fits_original"] is True
    assert by_mpn["NO-DIMS"]["fits_original"] == "unknown"
    assert by_mpn["744771122"]["dimensions_mm"] == {"length": 12.0, "width": 12.0, "height": 5.8}


def _res(ref, ohms, case):
    return {
        "resistor": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": ref,
                "datasheetInfo": {
                    "part": {"case": case},
                    "electrical": {"resistance": {"nominal": ohms}},
                },
            }
        }
    }


def test_exact_value_resistor_outranks_near_value_same_package():
    """Regression (CAY16470J4LF 47Ω → 39Ω bug): the value is the defining spec,
    so an EXACT-value part must rank above a near-value part even when the
    near-value one shares the original's package and the exact one is in a
    smaller (but fitting) footprint."""
    exact_0603 = _res("EXACT-47-0603", 47.0, "0603")  # exact value, smaller pkg
    near_1206 = _res("NEAR-39-1206", 39.0, "1206")  # 17% off, same pkg as source
    comp = {"value": "47", "component_type": "resistor", "package": "1206"}
    ranked = _rank_candidates(comp, "resistor", [near_1206, exact_0603], max_results=10)
    assert _envelope_reference(ranked[0], "resistor") == "EXACT-47-0603"


def test_resistor_value_dominates_footprint_smaller_is_better():
    """Among EXACT-value parts, smaller fitting footprint wins; a wrong-value
    tiny part still loses to the exact-value parts."""
    exact_0402 = _res("EXACT-47-0402", 47.0, "0402")
    exact_0603 = _res("EXACT-47-0603", 47.0, "0603")
    wrong_0402 = _res("WRONG-33-0402", 33.0, "0402")
    # 1206 source footprint (set by stage 1 from the package) enables the
    # smaller-is-better tie-break between the two exact-value parts.
    comp = {
        "value": "47",
        "component_type": "resistor",
        "package": "1206",
        "_source_dims_m": (0.0032, 0.0016, None),
    }
    ranked = _rank_candidates(
        comp, "resistor", [wrong_0402, exact_0603, exact_0402], max_results=10
    )
    order = [_envelope_reference(c, "resistor") for c in ranked]
    assert order[0] == "EXACT-47-0402"  # exact value, smallest fitting
    assert order[-1] == "WRONG-33-0402"  # wrong value ranks last
