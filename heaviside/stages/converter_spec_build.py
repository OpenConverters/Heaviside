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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heaviside.stages.topology_constraints import DesignConstraints


def _seed_turns_ratio(spec: dict[str, Any], topology: str) -> None:
    """Seed ``desiredTurnsRatios`` for an isolated/transformer topology when the
    spec omits it (the Heaviside stress engine needs it; MKF re-derives its own
    for the magnetic). Forward/bridge/push-pull: n = Vin_min·D_max/Vout;
    flyback-family: n = Vin_min·D_nom/(Vout·(1−D_nom)).

    The turns ratio is sized for a NOMINAL duty 10% below the duty ceiling, not the ceiling
    itself (ABT #45): sizing for D_max exactly leaves zero headroom, so rounding the ratio — or
    any real rectifier drop / conduction loss — pushes the required duty just over the ceiling and
    MKF rejects the spec ("required dutyCycle 0.4501 exceeds maximumDutyCycle 0.4500"). A nominal
    duty below the ceiling lets the converter reach target with margin to spare."""
    try:
        from heaviside.topologies import get
        fam = get(topology).family
    except Exception:
        return
    if not fam.startswith("isolated"):
        return  # non-isolated topologies have no turns ratio
    iv = spec.get("inputVoltage") or {}
    vmin = iv.get("minimum") or iv.get("nominal") if isinstance(iv, dict) else None
    ops = spec.get("operatingPoints") or []
    op = ops[0] if ops and isinstance(ops[0], dict) else {}
    vouts = op.get("outputVoltages") or []
    vout = vouts[0] if vouts and isinstance(vouts[0], (int, float)) else None
    d_max = spec.get("maximumDutyCycle", 0.5)
    if not (isinstance(vmin, (int, float)) and vmin > 0 and vout and vout > 0 and 0 < d_max < 1):
        return
    d_nom = float(d_max) * 0.9  # headroom below the duty ceiling (ABT #45)
    if "flyback" in topology:
        n = float(vmin) * d_nom / (float(vout) * (1.0 - d_nom))
    else:  # forward / bridge / push-pull
        n = float(vmin) * d_nom / float(vout)
    if n > 0:
        spec["desiredTurnsRatios"] = [round(n, 4)]


def build(
    spec: dict[str, Any],
    topology: str | None = None,
    *,
    constraints: DesignConstraints | None = None,
) -> dict[str, Any]:
    """Return ``spec`` augmented with the converter-level constraints MKF
    requires. Mutates and returns the passed dict (callers pass a copy).

    ``topology`` enables model-specific augmentation (AHB rectifier type,
    PSFB phase shift, resonant fsw window, CLLLC bus voltages) without leaking
    a key into other converter models' specs.

    ``constraints`` (master-plan B2) supplies ``maximumDutyCycle`` /
    ``maximumDrainSourceVoltage`` from the ``topology-constraint-proposer``;
    when ``None`` the band-guarded deterministic fallback
    (``topology_constraints.deterministic`` — 0.5 / 3·Vmax) is used. Either way
    the two values now live in one place, not as literals here. An explicit
    caller-set value on ``spec`` still wins (``stamp`` uses ``setdefault``).
    """
    from heaviside.stages import topology_constraints

    if constraints is None:
        # inputVoltage may be absent on non-converter specs reaching this thin
        # builder; only derive defaults when a Vmax exists (matches the prior
        # guarded behaviour — no silent fabrication otherwise).
        iv = spec.get("inputVoltage") or {}
        has_vmax = isinstance(iv, dict) and (iv.get("maximum") or iv.get("nominal"))
        if has_vmax:
            constraints = topology_constraints.deterministic(spec, topology)
    if constraints is not None:
        constraints.stamp(spec)

    # Converter-level seeds MKF's base models read to derive L / duty / conduction
    # mode (Buck::process_design_requirements reads diodeVoltageDrop for the duty
    # calc; the loss/Pin balance reads efficiency). A minimal user spec (Vin +
    # rails) carries neither, so seed them — the same values + rationale the RE
    # path uses (re_state.py: Si rectifier ~0.7 V, 90% first-pass efficiency target).
    # Design *seeds* (like maximumDutyCycle), refined once a real rectifier/loss
    # budget exists — NOT a physics result. Explicit caller values win.
    if "inputVoltage" in spec and spec.get("operatingPoints"):
        spec.setdefault("diodeVoltageDrop", 0.7)
        spec.setdefault("efficiency", 0.9)

    # Isolated/transformer topologies need a primary:secondary turns ratio. It
    # is a DESIGN OUTPUT (MKF derives it on the base path), but Heaviside's
    # analytical stress engine needs it as an input to size the switch/rectifier,
    # so seed a sensible first-pass value when the user spec omits it. The ratio
    # is picked so that at the worst case (Vin_min, duty ceiling) the secondary
    # still reaches Vout: forward/bridge/push-pull n = Vin_min·D_max/Vout;
    # flyback-family n = Vin_min·D_max/(Vout·(1−D_max)). Seed only — explicit
    # caller turns ratios win, and MKF re-derives its own for the magnetic.
    if topology and "desiredTurnsRatios" not in spec:
        _seed_turns_ratio(spec, topology)

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
