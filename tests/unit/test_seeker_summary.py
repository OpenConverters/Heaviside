"""The datasheet-seeker's category summary mappers: raw seeker JSON (as returned
by ``kimi_seek``) → the flat ``_summarize_candidate``-keyed dict the cross-ref
gates read. These are pure mappings — no LLM, no network — so they run in the
normal suite and pin the exact keys the gates consume per category.
"""

from __future__ import annotations

import pytest

from heaviside.librarian.datasheet import seeker


def test_magnetic_summary_uses_lowest_drop_isat_and_max_dcr() -> None:
    raw = {
        "mpn": "L1",
        "inductance_H": 4.7e-6,
        "isat_A": {"drop_10pct": 6.8, "drop_20pct": 8.0, "drop_30pct": 10.6},
        "rated_current_A": 21.2,
        "dcr_ohm": {"typ": 0.003, "max": 0.0034},
    }
    s = seeker.summary_from_seeker("magnetic", raw)
    assert s["saturation_current"] == 6.8  # lowest-drop (10%) is the conservative pick
    assert s["saturation_current_drop_pct"] == 10
    assert s["rated_current"] == 21.2
    assert s["dcr"] == 0.0034  # worst-case max, not typ
    assert s["value_si"] == pytest.approx(4.7e-6)


def test_magnetic_summary_drops_implausible_inductance() -> None:
    # LLM dropped the SI prefix (wrote 4.7 for 4.7µH): value >= 0.1 H is impossible
    # for a power inductor, so it must be omitted rather than cached wrong.
    s = seeker.summary_from_seeker("magnetic", {"mpn": "L", "inductance_H": 4.7})
    assert "inductance" not in s and "value_si" not in s


def test_capacitor_summary_maps_gate_keys_and_pct() -> None:
    raw = {
        "mpn": "C", "capacitance_F": 1e-7, "voltage_V": 25, "dielectric_code": "X7R",
        "temp_max_C": 125, "esr_ohm": 0.02, "ripple_current_A": 1.5, "tolerance_frac": 0.1,
    }
    s = seeker.summary_from_seeker("capacitor", raw)
    assert s["dielectric_code"] == "X7R"
    assert s["temp_max_C"] == 125.0
    assert s["esr"] == 0.02
    assert s["ripple_current"] == 1.5
    assert s["tolerance_pct"] == pytest.approx(10.0)  # frac -> percent
    assert s["value_si"] == pytest.approx(1e-7)


def test_mosfet_summary_maps_gate_keys() -> None:
    raw = {"mpn": "Q", "vds_V": 100, "rds_on_ohm": 0.05, "id_A": 30,
           "qg_C": 12e-9, "vgs_th_max_V": 3.0, "temp_max_C": 150}
    s = seeker.summary_from_seeker("mosfet", raw)
    assert s["rds_on"] == 0.05
    assert s["qg"] == pytest.approx(12e-9)
    assert s["vgs_threshold_max"] == 3.0
    assert s["vds"] == 100.0


def test_diode_summary_maps_gate_keys() -> None:
    raw = {"mpn": "D", "vrrm_V": 600, "vf_V": 0.45, "if_A": 10,
           "qrr_C": 50e-9, "trr_s": 35e-9, "temp_max_C": 175}
    s = seeker.summary_from_seeker("diode", raw)
    assert s["vf"] == 0.45
    assert s["qrr"] == pytest.approx(50e-9)
    assert s["trr"] == pytest.approx(35e-9)


def test_resistor_summary_maps_gate_keys_and_pct() -> None:
    raw = {"mpn": "R", "resistance_ohm": 47000, "power_W": 0.25,
           "tcr_ppm": 100, "tolerance_frac": 0.01}
    s = seeker.summary_from_seeker("resistor", raw)
    assert s["power_rating"] == 0.25
    assert s["tcr"] == 100.0
    assert s["tolerance_pct"] == pytest.approx(1.0)
    assert s["value_si"] == 47000.0


def test_summary_omits_absent_fields_never_guesses() -> None:
    # Only capacitance present → only that key (plus mpn) comes out; no defaults.
    s = seeker.summary_from_seeker("capacitor", {"mpn": "C", "capacitance_F": 1e-6})
    assert set(s) <= {"mpn", "capacitance", "value_si"}
    assert "esr" not in s and "temp_max_C" not in s


def test_summary_unknown_category_returns_mpn_only() -> None:
    s = seeker.summary_from_seeker("widget", {"mpn": "W", "foo": 1})
    assert s == {"mpn": "W"}


def test_magnetic_result_ok_requires_a_current_rating() -> None:
    assert seeker._magnetic_result_ok({"rated_current_A": 5.0}) is True
    assert seeker._magnetic_result_ok({"isat_A": {"drop_20pct": 6.0}}) is True
    assert seeker._magnetic_result_ok({"inductance_H": 4.7e-6}) is False
    assert seeker._magnetic_result_ok({}) is False
