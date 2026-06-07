"""Tests for the diode + capacitor selectors.

Mirrors the MOSFET tests in shape; per-class field names + edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.catalogue import (
    Capacitor,
    CapacitorConstraints,
    CapacitorTiebreaker,
    Diode,
    DiodeConstraints,
    DiodeTiebreaker,
    SelectionError,
    select_capacitor,
    select_diode,
)

# ---------------------------------------------------------------------------
# Diode fixtures
# ---------------------------------------------------------------------------


def _diode_row(
    *,
    mpn: str,
    mfr: str = "ACME",
    vrrm: float,
    if_avg: float,
    vf: float = 0.7,
    qrr: float = 1e-9,
    trr: float = 50e-9,
    tech: str = "FastRecovery",
    case: str = "DO-214AB",
    status: str = "production",
) -> dict:
    return {
        "semiconductor": {
            "diode": {
                "manufacturerInfo": {
                    "name": mfr,
                    "reference": mpn,
                    "status": status,
                    "datasheetUrl": "https://example.invalid",
                    "datasheetInfo": {
                        "electrical": {
                            "reverseVoltage": vrrm,
                            "forwardCurrent": if_avg,
                            "forwardVoltage": vf,
                            "reverseRecoveryCharge": qrr,
                            "reverseRecoveryTime": trr,
                        },
                        "part": {
                            "partNumber": mpn,
                            "subType": tech,
                            "case": case,
                        },
                    },
                },
            },
        },
    }


def _write(dir_: Path, fname: str, rows: list[dict]) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / fname
    with p.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def test_diode_from_envelope_projects_basic_row() -> None:
    d = Diode.from_envelope(_diode_row(mpn="D1", vrrm=600, if_avg=3, vf=1.7))
    assert d is not None
    assert d.vrrm_rated == 600.0
    assert d.if_avg_rated == 3.0
    assert d.vf_typ == 1.7


def test_diode_select_picks_lowest_vf(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "diodes.ndjson",
        [
            _diode_row(mpn="HI_VF", vrrm=200, if_avg=5, vf=1.2),
            _diode_row(mpn="LOW_VF", vrrm=200, if_avg=5, vf=0.4),
        ],
    )
    c = DiodeConstraints(vrrm_min=100, if_avg_min=3)
    sel = select_diode(c, tiebreaker=DiodeTiebreaker.LOWEST_VF, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "LOW_VF"
    assert sel.alternatives_considered == 2


def test_diode_select_raises_with_rejection_histogram(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "diodes.ndjson",
        [
            _diode_row(mpn="SMALL_V", vrrm=50, if_avg=10),
            _diode_row(mpn="SMALL_I", vrrm=600, if_avg=0.5),
        ],
    )
    c = DiodeConstraints(vrrm_min=100, if_avg_min=3)
    with pytest.raises(SelectionError) as exc:
        select_diode(c, tiebreaker=DiodeTiebreaker.LOWEST_VF, tas_data_dir=tmp_path)
    assert exc.value.rejection_counts["vrrm_low"] == 1
    assert exc.value.rejection_counts["if_avg_low"] == 1


def test_diode_qrr_filter_when_set(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "diodes.ndjson",
        [
            _diode_row(mpn="HIGH_QRR", vrrm=200, if_avg=5, qrr=200e-9),
            _diode_row(mpn="LOW_QRR", vrrm=200, if_avg=5, qrr=5e-9),
        ],
    )
    c = DiodeConstraints(vrrm_min=100, if_avg_min=3, qrr_max=10e-9)
    sel = select_diode(c, tiebreaker=DiodeTiebreaker.LOWEST_QRR, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "LOW_QRR"


# ---------------------------------------------------------------------------
# Capacitor fixtures
# ---------------------------------------------------------------------------


def _cap_row(
    *,
    mpn: str,
    mfr: str = "Murata",
    capacitance: float,
    v_rated: float,
    ripple: float = 0.0,
    esr: float = 0.0,
    tech: str = "Ceramic Capacitors",
    case: str = "0805",
    status: str = "production",
) -> dict:
    return {
        "capacitor": {
            "manufacturerInfo": {
                "name": mfr,
                "reference": mpn,
                "status": status,
                "datasheetUrl": "https://example.invalid",
                "datasheetInfo": {
                    "electrical": {
                        "capacitance": {"nominal": capacitance},
                        "ratedVoltage": v_rated,
                        "rippleCurrent": ripple,
                        "esr": esr,
                    },
                    "part": {
                        "partNumber": mpn,
                        "family": tech,
                        "case": case,
                    },
                },
            },
        },
    }


def test_cap_from_envelope_handles_nominal_dict_and_scalar() -> None:
    c1 = Capacitor.from_envelope(_cap_row(mpn="A", capacitance=10e-6, v_rated=50))
    assert c1 is not None and c1.capacitance == 10e-6
    raw = _cap_row(mpn="B", capacitance=10e-6, v_rated=50)
    # Replace dict with bare scalar to test the alternate shape.
    raw["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["capacitance"] = 22e-6
    c2 = Capacitor.from_envelope(raw)
    assert c2 is not None and c2.capacitance == 22e-6


def test_cap_select_picks_lowest_esr(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "capacitors.ndjson",
        [
            _cap_row(mpn="HI_ESR", capacitance=10e-6, v_rated=50, esr=0.5),
            _cap_row(mpn="LO_ESR", capacitance=10e-6, v_rated=50, esr=0.01),
        ],
    )
    c = CapacitorConstraints(
        capacitance_min=8e-6,
        capacitance_max=22e-6,
        v_rated_min=25,
    )
    sel = select_capacitor(c, tiebreaker=CapacitorTiebreaker.LOWEST_ESR, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "LO_ESR"


def test_cap_ripple_filter_skipped_when_none(tmp_path: Path) -> None:
    """MLCC rows have ripple=0 in TAS; constraint must not reject them."""
    _write(
        tmp_path,
        "capacitors.ndjson",
        [
            _cap_row(mpn="MLCC", capacitance=10e-6, v_rated=50, ripple=0.0),
        ],
    )
    c = CapacitorConstraints(
        capacitance_min=8e-6,
        capacitance_max=22e-6,
        v_rated_min=25,
        ripple_current_min=None,  # don't filter
    )
    sel = select_capacitor(c, tiebreaker=CapacitorTiebreaker.LOWEST_ESR, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "MLCC"


def test_cap_ripple_filter_active_when_set(tmp_path: Path) -> None:
    """When explicitly set, ripple filter rejects low-ripple rows."""
    _write(
        tmp_path,
        "capacitors.ndjson",
        [
            _cap_row(mpn="LOW_RIPPLE", capacitance=100e-6, v_rated=50, ripple=0.5),
            _cap_row(mpn="HIGH_RIPPLE", capacitance=100e-6, v_rated=50, ripple=2.5),
        ],
    )
    c = CapacitorConstraints(
        capacitance_min=50e-6,
        capacitance_max=470e-6,
        v_rated_min=25,
        ripple_current_min=2.0,
    )
    sel = select_capacitor(c, tiebreaker=CapacitorTiebreaker.LOWEST_ESR, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "HIGH_RIPPLE"


def test_cap_constraints_reject_inverted_band() -> None:
    with pytest.raises(ValueError, match="capacitance_min > capacitance_max"):
        CapacitorConstraints(
            capacitance_min=100e-6,
            capacitance_max=10e-6,
            v_rated_min=25,
        )


def test_cap_constraints_reject_negative_ripple() -> None:
    with pytest.raises(ValueError, match="ripple_current_min"):
        CapacitorConstraints(
            capacitance_min=10e-6,
            capacitance_max=22e-6,
            v_rated_min=25,
            ripple_current_min=-0.5,
        )
