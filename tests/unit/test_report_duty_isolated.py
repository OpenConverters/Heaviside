"""An isolated converter's reported duty cycle must NOT print the bare
``D = V_out / V_in`` substitution: for a transformer-isolated topology D depends
on the turns ratio too, so ``5 / 48 = 0.10`` was being printed next to a
regulated D of 0.36 (a self-contradicting report). The regulated
operating-point duty is shown instead, with no wrong closed form."""
from types import SimpleNamespace as NS

import pytest

from heaviside.report.model import ReportModel


def _model(topology: str, *, duty: float = 0.356) -> ReportModel:
    tas = {
        "inputs": {
            "designRequirements": {
                "inputVoltage": {"nominal": 48.0},
                "outputs": [{"name": "out", "voltage": {"nominal": 5.0}}],
            },
            "operatingPoints": [{"outputs": [{"power": 50.0}]}],
        },
        "duty": duty,
        "topology": {"stages": []},
    }
    outcome = NS(
        pick=NS(topology=NS(name=topology)),
        tas=tas,
        verdict_dict={"verdict": "pass", "checks": []},
    )
    return ReportModel(outcome)


def _duty_item(m: ReportModel) -> dict:
    items = [it for it in m.design_calc_items() if "Duty" in it["name"]]
    assert len(items) == 1, items
    return items[0]


@pytest.mark.parametrize("topology", ["push_pull", "llc", "phase_shifted_full_bridge"])
def test_isolated_duty_shows_regulated_value_not_vout_over_vin(topology):
    m = _model(topology)
    assert m.isolated
    it = _duty_item(m)
    assert it["name"] == "Duty cycle (regulated)"
    # No misleading V_out/V_in substitution (which would read 5/48 = 0.10).
    assert "/" not in it["eq_html"]
    assert "frac" not in it["eq_tex"]
    assert it["result"] == ("num", 0.356, 3)   # the true regulated duty


def test_non_isolated_buck_keeps_the_vout_over_vin_form():
    m = _model("buck")
    assert not m.isolated
    it = _duty_item(m)
    assert it["name"] == "Duty cycle"
    assert "V<sub>out</sub> / V<sub>in</sub>" in it["eq_html"]
