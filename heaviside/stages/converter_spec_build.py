"""converter_spec_build — assemble the MKF converter-input spec for a topology.

Engine (deterministic, this module): given a bare electrical spec
(``inputVoltage`` + ``operatingPoints``) and a topology, add the
converter-level design constraints MKF's models need — the duty-cycle
ceiling, the FET Vds budget, and the per-model operating-point keys
(AHB ``dutyCycle``/``rectifierType``, PSFB ``phaseShift``, resonant
``min/maxSwitchingFrequency``, CLLLC HV/LV bus voltages). No LLM.

This is the BASE-schema builder: it intentionally does NOT inject
``desiredInductance``/``desiredMagnetizingInductance``. MKF derives the
magnetizing inductance itself from the operating point + ``currentRippleRatio``
(verified: passing ``desiredInductance`` is *ignored* by the base buck/flyback
models — `design_magnetics_from_converter` re-derives L regardless). The
authoritative L is harvested back from the MKF result post-design
(``full_design`` re-stamps ``desiredInductance`` with ``L_authoritative``).

There is no LLM layer here — the converter-level constraint *guessing*
(``maximumDutyCycle``/``maximumDrainSourceVoltage`` from a switch class) is a
separate Strands agent (the topology-constraint-proposer); this stage only
applies deterministic defaults when those constraints are absent.
"""
from __future__ import annotations

from typing import Any


def build(spec: dict[str, Any], topology: str | None = None) -> dict[str, Any]:
    """Return ``spec`` augmented with the converter-level constraints MKF
    requires. Mutates and returns the passed dict (callers pass a copy).

    ``topology`` enables model-specific augmentation (AHB rectifier type,
    PSFB phase shift, resonant fsw window, CLLLC bus voltages) without leaking
    a key into other converter models' specs.
    """
    spec.setdefault("maximumDutyCycle", 0.5)
    if "maximumDrainSourceVoltage" not in spec:
        iv = spec.get("inputVoltage") or {}
        vmax = iv.get("maximum") or iv.get("nominal")
        if vmax:
            # Generous Vds budget so MKF can pick a sensible reflected voltage.
            spec["maximumDrainSourceVoltage"] = round(float(vmax) * 3.0, 1)

    # MKF's AsymmetricHalfBridge requires a per-operating-point ``dutyCycle``
    # (AhbOperatingPoint.from_json calls j.at("dutyCycle")). It is the
    # *commanded* operating duty used for component sizing; MKF derives the
    # turns ratio from ``maximumDutyCycle`` (sized at min Vin for headroom)
    # and then sizes Lo/Lm/Cb/Co at this operating duty (falling back to
    # maximumDutyCycle when the OP value is out of (0,1)). Setting the OP duty
    # to the design's own duty ceiling makes the sizing self-consistent with
    # the turns-ratio derivation. Harmless for other converter models —
    # nlohmann from_json ignores keys it does not read.
    max_d = float(spec.get("maximumDutyCycle", 0.5))
    for op in spec.get("operatingPoints") or []:
        if isinstance(op, dict):
            op.setdefault("dutyCycle", max_d)

    # AsymmetricHalfBridge: the decomposer stencil binds a full-bridge
    # secondary rectifier (D_r1..D_r4). MKF's AHB model defaults to
    # CENTER_TAPPED, which duplicates the single-output turns ratio to size 2
    # and then trips its own ``turnsRatios.size() == numOutputs`` guard. Pin
    # the rectifier to the full-bridge variant the stencil expects so the
    # turns-ratio count matches the output count. AHB-only — other converter
    # models do not read this key (or use an incompatible enum), so it is
    # applied only when the topology is known to be the AHB.
    if topology == "asymmetric_half_bridge":
        spec.setdefault("rectifierType", "fullBridge")

    # MKF's PhaseShiftedFullBridge requires a per-operating-point ``phaseShift``
    # (PsfbOperatingPoint.from_json calls j.at("phaseShift"), degrees in
    # [0,180]). It is the commanded phase shift between the two bridge legs;
    # MKF maps it to the effective duty cycle D_eff = phaseShift/180 and sizes
    # the turns ratio + magnetising/output inductance from it. MKF's own design
    # path defaults the commanded duty to 0.7 when no phase shift is supplied,
    # so command the equivalent 0.7·180 = 126° here. PSFB-only — other
    # converter models do not read this key (nlohmann from_json ignores it).
    if topology == "phase_shifted_full_bridge":
        psfb_phase_shift = 0.7 * 180.0
        for op in spec.get("operatingPoints") or []:
            if isinstance(op, dict):
                op.setdefault("phaseShift", psfb_phase_shift)

    # Frequency-modulated resonant converters (SRC, LLC, …) are sized by
    # MKF from a switching-frequency *window* [minSwitchingFrequency,
    # maxSwitchingFrequency] rather than a single fsw. Both Src::from_json
    # and Llc::from_json read these via ``j.at(...)`` (required), and SRC's
    # ``get_effective_resonant_frequency()`` seeds the tank's resonant
    # frequency from the geometric mean ``sqrt(fmin·fmax)`` when no explicit
    # resonantFrequency is given. The MKF reference designs (TestSrc.cpp)
    # bracket the resonant frequency as fr·0.5 … fr·2.0; mirror that by
    # centring the window (geometric mean) on the design's nominal operating
    # fsw, so sqrt(fmin·fmax) == fsw and the per-OP fsw lands inside the
    # [min·0.99, max·1.01] range guard SRC/LLC enforce in run_checks(). Only
    # applied to resonant-family topologies — other converter models do not
    # read these keys (nlohmann from_json ignores them).
    try:
        from heaviside.topologies import get as _get_topology

        _fam = _get_topology(topology).family if topology else ""
    except Exception:
        _fam = ""
    if _fam == "resonant":
        fsws = [
            float(op["switchingFrequency"])
            for op in (spec.get("operatingPoints") or [])
            if isinstance(op, dict)
            and isinstance(op.get("switchingFrequency"), (int, float))
            and float(op.get("switchingFrequency")) > 0
        ]
        if fsws:
            spec.setdefault("minSwitchingFrequency", min(fsws) * 0.5)
            spec.setdefault("maxSwitchingFrequency", max(fsws) * 2.0)

    # MKF's CLLLC (bidirectional symmetric resonant) is specified by the two
    # DC bus voltages rather than a single input/output pair: AdvancedClllc /
    # ClllcResonant from_json read ``highVoltageBusVoltage`` and
    # ``lowVoltageBusVoltage`` via ``j.at(...)`` (both DimensionWithTolerance).
    # The HV bus IS the converter's input voltage window; the LV bus is the
    # regulated output rail. Mirror the spec's own values — no fabricated
    # numbers. CLLLC-only: other converter models do not read these keys
    # (nlohmann from_json ignores them), so this is harmless elsewhere.
    if topology == "clllc":
        iv = spec.get("inputVoltage")
        if isinstance(iv, dict) and "highVoltageBusVoltage" not in spec:
            spec["highVoltageBusVoltage"] = dict(iv)
        if "lowVoltageBusVoltage" not in spec:
            vouts = [
                float(op["outputVoltages"][0])
                for op in (spec.get("operatingPoints") or [])
                if isinstance(op, dict)
                and isinstance(op.get("outputVoltages"), (list, tuple))
                and op["outputVoltages"]
                and isinstance(op["outputVoltages"][0], (int, float))
            ]
            if vouts:
                vlv = sum(vouts) / len(vouts)
                spec["lowVoltageBusVoltage"] = {
                    "minimum": min(vouts),
                    "nominal": vlv,
                    "maximum": max(vouts),
                }
    return spec
