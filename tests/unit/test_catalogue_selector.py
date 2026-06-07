"""Tests for ``heaviside.catalogue.selector``.

Uses a tmp_path-backed mock NDJSON so we exercise the typed Mosfet
projection + filter ordering + tiebreaker semantics deterministically,
no dependency on the real TAS DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.catalogue import (
    Mosfet,
    MosfetConstraints,
    MosfetTiebreaker,
    SelectionError,
    select_mosfet,
)
from heaviside.catalogue.selector import _DEFAULT_TAS_DATA_DIR

# ---------------------------------------------------------------------------
# Fixture rows
# ---------------------------------------------------------------------------


def _row(
    *,
    mpn: str,
    mfr: str = "ACME",
    vds: float,
    idc: float,
    rds_on: float,
    qg: float = 20e-9,
    vgs_th_max: float = 3.0,
    tech: str = "Si",
    case: str = "TO-220",
    status: str = "production",
    ds_url: str = "https://example.invalid/ds.pdf",
) -> dict:
    """Construct a TAS-shaped mosfet envelope."""
    return {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "name": mfr,
                    "reference": mpn,
                    "status": status,
                    "datasheetUrl": ds_url,
                    "datasheetInfo": {
                        "electrical": {
                            "drainSourceVoltage": vds,
                            "continuousDrainCurrent": idc,
                            "onResistance": rds_on,
                            "totalGateCharge": qg,
                            "gateThresholdVoltage": {"maximum": vgs_th_max},
                        },
                        "part": {
                            "partNumber": mpn,
                            "technology": tech,
                            "case": case,
                        },
                    },
                },
            },
        },
    }


def _write_ndjson(dir_: Path, rows: list[dict]) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / "mosfets.ndjson"
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


# ---------------------------------------------------------------------------
# Mosfet.from_envelope
# ---------------------------------------------------------------------------


def test_from_envelope_projects_basic_row() -> None:
    env = _row(mpn="X1", vds=100, idc=10, rds_on=0.05)
    m = Mosfet.from_envelope(env)
    assert m is not None
    assert m.mpn == "X1"
    assert m.vds_rated == 100.0
    assert m.id_continuous == 10.0
    assert m.rds_on == 0.05


def test_from_envelope_returns_none_on_missing_required_field() -> None:
    bad = _row(mpn="X1", vds=100, idc=10, rds_on=0.05)
    # Strip a required field
    del bad["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "onResistance"
    ]
    assert Mosfet.from_envelope(bad) is None


def test_from_envelope_returns_none_on_wrong_top_shape() -> None:
    assert Mosfet.from_envelope({"capacitor": {}}) is None
    assert Mosfet.from_envelope({}) is None


# ---------------------------------------------------------------------------
# MosfetConstraints validation
# ---------------------------------------------------------------------------


def test_constraints_reject_non_positive_field() -> None:
    with pytest.raises(ValueError, match="vds_min"):
        MosfetConstraints(vds_min=0, id_min=1, rds_on_max=0.1, qg_max=1e-9)


def test_constraints_reject_empty_technology() -> None:
    with pytest.raises(ValueError, match="technology_allowed"):
        MosfetConstraints(
            vds_min=10,
            id_min=1,
            rds_on_max=0.1,
            qg_max=1e-9,
            technology_allowed=frozenset(),
        )


# ---------------------------------------------------------------------------
# select_mosfet — happy paths
# ---------------------------------------------------------------------------


def test_select_picks_lowest_rds_on_among_passing(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="HIGH_RDS", vds=100, idc=10, rds_on=0.030),
            _row(mpn="LOW_RDS", vds=100, idc=10, rds_on=0.005),
            _row(mpn="MID_RDS", vds=100, idc=10, rds_on=0.015),
        ],
    )
    c = MosfetConstraints(vds_min=80, id_min=5, rds_on_max=0.040, qg_max=50e-9)
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "LOW_RDS"
    assert sel.alternatives_considered == 3


def test_select_picks_lowest_qg_when_requested(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="HIGH_QG", vds=100, idc=10, rds_on=0.010, qg=40e-9),
            _row(mpn="LOW_QG", vds=100, idc=10, rds_on=0.020, qg=10e-9),
        ],
    )
    c = MosfetConstraints(vds_min=80, id_min=5, rds_on_max=0.030, qg_max=50e-9)
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_QG, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "LOW_QG"


def test_margins_are_ratios_not_absolutes(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="X", vds=120, idc=20, rds_on=0.010, qg=20e-9),
        ],
    )
    c = MosfetConstraints(vds_min=60, id_min=5, rds_on_max=0.030, qg_max=40e-9)
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    assert sel.margins["vds_margin"] == pytest.approx(120 / 60)
    assert sel.margins["id_margin"] == pytest.approx(20 / 5)
    assert sel.margins["rds_on_headroom"] == pytest.approx(0.030 / 0.010)
    assert sel.margins["qg_headroom"] == pytest.approx(40e-9 / 20e-9)


# ---------------------------------------------------------------------------
# Rejection paths — verify the histogram is accurate
# ---------------------------------------------------------------------------


def test_select_raises_when_no_candidate_passes_vds(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="SMALL", vds=60, idc=10, rds_on=0.010),
            _row(mpn="ALSO_SMALL", vds=40, idc=10, rds_on=0.010),
        ],
    )
    c = MosfetConstraints(vds_min=100, id_min=5, rds_on_max=0.030, qg_max=40e-9)
    with pytest.raises(SelectionError) as exc:
        select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    assert exc.value.rejection_counts["vds_rated_low"] == 2
    assert exc.value.total_rows_considered == 2


def test_select_excludes_discontinued_when_flag_set(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="OLD", vds=100, idc=10, rds_on=0.005, status="discontinued"),
            _row(mpn="NEW", vds=100, idc=10, rds_on=0.020, status="production"),
        ],
    )
    c = MosfetConstraints(vds_min=80, id_min=5, rds_on_max=0.030, qg_max=50e-9)
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    # OLD has lower Rds_on but is discontinued; NEW must win.
    assert sel.chosen.mpn == "NEW"


def test_select_filters_by_technology_allowlist(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="GAN_PART", vds=100, idc=10, rds_on=0.005, tech="GaN"),
            _row(mpn="SI_PART", vds=100, idc=10, rds_on=0.020, tech="Si"),
        ],
    )
    c = MosfetConstraints(
        vds_min=80,
        id_min=5,
        rds_on_max=0.030,
        qg_max=50e-9,
        technology_allowed=frozenset({"Si"}),  # ban GaN
    )
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "SI_PART"


# ---------------------------------------------------------------------------
# Reader integration
# ---------------------------------------------------------------------------


def test_select_skips_unreadable_rows(tmp_path: Path) -> None:
    _write_ndjson(
        tmp_path,
        [
            _row(mpn="GOOD", vds=100, idc=10, rds_on=0.010),
            {"capacitor": {"reference": "wrong-shape"}},  # unreadable
        ],
    )
    c = MosfetConstraints(vds_min=80, id_min=5, rds_on_max=0.030, qg_max=50e-9)
    sel = select_mosfet(c, tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON, tas_data_dir=tmp_path)
    assert sel.chosen.mpn == "GOOD"


def test_default_tas_dir_points_at_repo_data() -> None:
    """Regression check: the module-level default must resolve to the repo's
    TAS/data, not somewhere else on the filesystem."""
    assert _DEFAULT_TAS_DATA_DIR.name == "data"
    assert _DEFAULT_TAS_DATA_DIR.parent.name == "TAS"
