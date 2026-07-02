"""design_artifact — loss-vs-fsw curve + design_provenance (master-plan step B9).

Tested with a fake FrequencySweepResult (no MKF); the design_provenance shape
uses the uniform B1 envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from heaviside import provenance
from heaviside.stages import design_artifact as da


@dataclass
class _Cand:
    total_loss_w: float = 1.10
    magnetic_loss_w: float = 0.80
    switching_loss_w: float = 0.30
    isat_a: float = 5.0
    ipeak_worst_a: float = 3.2
    inductance_h: float = 22e-6
    core_shape: str = "PQ 20/16"
    core_material: str = "3C95"


@dataclass
class _Point:
    fsw_hz: float
    feasible: bool
    n_feasible: int
    n_candidates: int
    total_loss_w: float | None
    magnetic_loss_w: float | None
    switching_loss_w: float | None
    reason: str | None


@dataclass
class _Fet:
    mpn: str = "AO4423"
    manufacturer: str = "Alpha and Omega"
    qg_total_c: float = 1.3e-9
    technology: str = "Si"


@dataclass
class _Result:
    fsw_star_hz: float = 250_000.0
    worst_vds_v: float = 48.0
    worst_id_a: float = 3.2
    min_isat_ratio: float = 1.2
    front: list = field(default_factory=lambda: [_Cand()])
    loss_curve: list = field(
        default_factory=lambda: [
            _Point(150_000, True, 2, 3, 1.30, 1.05, 0.25, None),
            _Point(250_000, True, 3, 3, 1.10, 0.80, 0.30, None),
            _Point(600_000, False, 0, 3, None, None, None, "3 under 1.2x isat margin"),
        ]
    )
    envelope_fet: _Fet = field(default_factory=_Fet)

    @property
    def best(self):
        return self.front[0]


def _spec():
    return {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [{"outputVoltages": [3.3], "outputCurrents": [3]}],
    }


def test_loss_curve_artifact_shape():
    art = da.loss_curve_artifact(_Result())
    assert art["fsw_star_hz"] == 250_000.0
    assert art["chosen"]["core_shape"] == "PQ 20/16"
    assert art["chosen"]["inductance_uh"] == pytest.approx(22.0)
    assert art["switch_loss_envelope_fet"]["mpn"] == "AO4423"
    assert len(art["loss_curve"]) == 3
    # infeasible point carries its reason, None losses
    last = art["loss_curve"][-1]
    assert last["feasible"] is False
    assert last["total_loss_w"] is None
    assert "isat margin" in last["reason"]


def test_loss_curve_chosen_total_is_split_sum():
    art = da.loss_curve_artifact(_Result())
    c = art["chosen"]
    assert c["total_loss_w"] == pytest.approx(c["magnetic_loss_w"] + c["switching_loss_w"])


def test_design_provenance_uses_uniform_envelope():
    prov = da.design_provenance(_Result(), topology="buck", spec=_spec())
    for key in ("magnetic", "switching_frequency", "switch_class"):
        assert provenance.is_complete(prov[key]), key
    assert prov["switch_class"]["source_ref"] == "AO4423"
    assert prov["magnetic"]["source_ref"] == "PQ 20/16/3C95"
    assert "250000Hz" in prov["switching_frequency"]["source_ref"]


def test_design_provenance_is_deterministic():
    # same inputs ⇒ same inputs_hash (re-derivable)
    a = da.design_provenance(_Result(), topology="buck", spec=_spec())
    b = da.design_provenance(_Result(), topology="buck", spec=_spec())
    assert a["magnetic"]["inputs_hash"] == b["magnetic"]["inputs_hash"]
