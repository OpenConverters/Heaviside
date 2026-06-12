"""Auxiliary-BOM synthesis: bootstrap / VCC bypass / soft-start / R_sense.

These functions extend the designer's BOM beyond the power stage. Every
one of them is evidence-gated: it sizes from PICKED component datasheet
values (FET Qg) or controller catalog fields populated by datasheet
extraction, and SKIPS with a diagnostic — never guesses — when the data
is absent (CLAUDE.md: no fallbacks).
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.catalogue.assemble import (
    _add_bootstrap_capacitor,
    _add_current_sense_resistor,
    _add_soft_start_capacitor,
    _add_vcc_bypass_capacitor,
)

pytestmark = pytest.mark.unit


def _tas(controller: dict[str, Any] | None = None, fet_qg: float | None = None) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    if fet_qg is not None:
        components.append(
            {
                "name": "Q1",
                "data": {
                    "semiconductor": {
                        "mosfet": {
                            "manufacturerInfo": {
                                "datasheetInfo": {
                                    "electrical": {"totalGateCharge": fet_qg}
                                }
                            }
                        }
                    }
                },
            }
        )
    control_components: list[dict[str, Any]] = []
    if controller is not None:
        control_components.append({"name": "U1", "data": controller})
    return {
        "topology": {
            "stages": [
                {"role": "switchingCell", "circuit": {"components": components}},
                {
                    "role": "control",
                    "circuit": {"components": control_components},
                    "drives": [{"component": "Q1", "signal": "gate"}],
                },
            ]
        }
    }


_SPEC = {
    "inputVoltage": {"nominal": 48.0, "maximum": 60.0},
    "operatingPoints": [
        {
            "switchingFrequency": 200_000.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
        }
    ],
}

_CTRL_DRIVER = {
    "type": "controller",
    "topologies": ["buck"],
    "integratedDriver": True,
    "integratedFET": False,
    "feedbackReferenceVoltage": 0.8,
}


def _names(tas: dict[str, Any]) -> set[str]:
    return {
        c["name"]
        for s in tas["topology"]["stages"]
        for c in s["circuit"]["components"]
        if isinstance(c, dict)
    }


class TestBootstrapCapacitor:
    def test_fires_from_picked_fet_gate_charge(self) -> None:
        tas = _tas(controller=dict(_CTRL_DRIVER), fet_qg=30e-9)
        added = _add_bootstrap_capacitor(tas, topology="buck", spec=_SPEC)
        assert added, tas.get("diagnostics")
        assert "C_boot" in _names(tas)
        comp = next(
            c
            for s in tas["topology"]["stages"]
            for c in s["circuit"]["components"]
            if c.get("name") == "C_boot"
        )
        prov = comp["selection_provenance"]
        assert prov["qg_worst_c"] == 30e-9
        assert prov["v_working_basis"].startswith("conservative Vin_max")
        # C_boot lands in the control stage (the one with drives).
        control = tas["topology"]["stages"][1]
        assert any(c.get("name") == "C_boot" for c in control["circuit"]["components"])

    def test_skips_with_diagnostic_when_fet_qg_missing(self) -> None:
        tas = _tas(controller=dict(_CTRL_DRIVER), fet_qg=None)
        assert not _add_bootstrap_capacitor(tas, topology="buck", spec=_SPEC)
        assert any("totalGateCharge" in d for d in tas.get("diagnostics", []))

    def test_skips_for_monolithic_controller(self) -> None:
        ctrl = dict(_CTRL_DRIVER, integratedFET=True)
        tas = _tas(controller=ctrl, fet_qg=30e-9)
        assert not _add_bootstrap_capacitor(tas, topology="buck", spec=_SPEC)
        assert "C_boot" not in _names(tas)

    def test_skips_for_non_buck(self) -> None:
        tas = _tas(controller=dict(_CTRL_DRIVER), fet_qg=30e-9)
        assert not _add_bootstrap_capacitor(tas, topology="flyback", spec=_SPEC)

    def test_uses_controller_gate_drive_voltage_when_present(self) -> None:
        ctrl = dict(_CTRL_DRIVER, gateDriveVoltage=7.5)
        tas = _tas(controller=ctrl, fet_qg=30e-9)
        assert _add_bootstrap_capacitor(tas, topology="buck", spec=_SPEC)
        comp = next(
            c
            for s in tas["topology"]["stages"]
            for c in s["circuit"]["components"]
            if c.get("name") == "C_boot"
        )
        assert comp["selection_provenance"]["v_working_basis"] == "controller gateDriveVoltage"


class TestVccBypass:
    def test_data_gated_skip(self) -> None:
        tas = _tas(controller=dict(_CTRL_DRIVER))
        assert not _add_vcc_bypass_capacitor(tas, spec=_SPEC)
        assert any("vccBypassCapacitance" in d for d in tas.get("diagnostics", []))

    def test_fires_on_datasheet_value(self) -> None:
        ctrl = dict(_CTRL_DRIVER, vccBypassCapacitance=1e-6, vccVoltage=12.0)
        tas = _tas(controller=ctrl)
        assert _add_vcc_bypass_capacitor(tas, spec=_SPEC), tas.get("diagnostics")
        assert "C_vcc" in _names(tas)


class TestSoftStart:
    def test_data_gated_skip(self) -> None:
        tas = _tas(controller=dict(_CTRL_DRIVER))
        assert not _add_soft_start_capacitor(tas, spec=_SPEC)
        assert any("softStartCurrent" in d for d in tas.get("diagnostics", []))

    def test_fires_on_datasheet_value(self) -> None:
        ctrl = dict(_CTRL_DRIVER, softStartCurrent=10e-6)
        tas = _tas(controller=ctrl)
        assert _add_soft_start_capacitor(tas, spec=_SPEC), tas.get("diagnostics")
        comp = next(
            c
            for s in tas["topology"]["stages"]
            for c in s["circuit"]["components"]
            if c.get("name") == "C_ss"
        )
        prov = comp["selection_provenance"]
        # C_ss = Iss*tss/Vref = 10µA * 5ms / 0.8V = 62.5 nF target
        assert prov["soft_start_current_a"] == 10e-6
        assert prov["vref"] == 0.8


class TestCurrentSense:
    def test_silent_noop_for_rdson_sensing_controller(self) -> None:
        """No currentSenseThresholdVoltage = Rds(on)-sensing part: nothing
        added AND no diagnostic (this is by design, not missing data)."""
        tas = _tas(controller=dict(_CTRL_DRIVER))
        assert not _add_current_sense_resistor(tas, spec=_SPEC, i_peak=6.0)
        assert "R_sense" not in _names(tas)
        assert not tas.get("diagnostics")

    def test_fires_on_threshold_voltage(self) -> None:
        ctrl = dict(_CTRL_DRIVER, currentSenseThresholdVoltage=0.05)
        tas = _tas(controller=ctrl)
        assert _add_current_sense_resistor(tas, spec=_SPEC, i_peak=6.0), tas.get(
            "diagnostics"
        )
        comp = next(
            c
            for s in tas["topology"]["stages"]
            for c in s["circuit"]["components"]
            if c.get("name") == "R_sense"
        )
        prov = comp["selection_provenance"]
        assert prov["target_ohms"] == pytest.approx(0.05 / 6.0)

    def test_diagnostic_when_peak_unknown(self) -> None:
        ctrl = dict(_CTRL_DRIVER, currentSenseThresholdVoltage=0.05)
        tas = _tas(controller=ctrl)
        assert not _add_current_sense_resistor(tas, spec=_SPEC, i_peak=None)
        assert any("peak current" in d for d in tas.get("diagnostics", []))
