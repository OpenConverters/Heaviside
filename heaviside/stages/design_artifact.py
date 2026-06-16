"""design_artifact — serialise the designer's auditable outputs (master-plan B9).

Two JSON-able artifacts the minimal-input ``/design`` flow returns alongside the
BOM:

* :func:`loss_curve_artifact` — the loss-vs-frequency curve the sweep produced
  (the evidence that ``fsw*`` is a real total-loss minimum, not a guess), plus
  the chosen point and the real envelope FET whose Qg bounded the switching
  surrogate.
* :func:`design_provenance` — a top-level provenance envelope over the WHOLE
  design: where the magnetic, fsw*, and switch class each came from, using the
  uniform ``{producer, method, source_ref, inputs_hash}`` shape (B1) so the
  design as a whole is as auditable as each stamped part.

Both operate on a ``frequency_sweep.FrequencySweepResult`` (duck-typed) so they
are unit-testable without MKF.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def loss_curve_artifact(result: Any) -> dict[str, Any]:
    """JSON-able loss-vs-fsw curve + the chosen operating point.

    The curve is every frequency the sweep evaluated (coarse grid + golden
    refinement), each with its minimum feasible total loss and the loss split,
    plus a ``feasible`` flag and the infeasibility ``reason`` where it applies —
    so a reviewer sees exactly where the design space opens and closes."""
    curve = [
        {
            "fsw_hz": round(float(p.fsw_hz), 1),
            "feasible": bool(p.feasible),
            "n_feasible": int(p.n_feasible),
            "n_candidates": int(p.n_candidates),
            "total_loss_w": _r(p.total_loss_w),
            "magnetic_loss_w": _r(p.magnetic_loss_w),
            "switching_loss_w": _r(p.switching_loss_w),
            "reason": p.reason,
        }
        for p in result.loss_curve
    ]
    fet = result.envelope_fet
    best = result.best
    return {
        "fsw_star_hz": round(float(result.fsw_star_hz), 1),
        "chosen": {
            "total_loss_w": _r(best.total_loss_w),
            "magnetic_loss_w": _r(best.magnetic_loss_w),
            "switching_loss_w": _r(best.switching_loss_w),
            "core_shape": best.core_shape,
            "core_material": best.core_material,
            "inductance_uh": _r(best.inductance_h * 1e6, 3),
            "isat_a": _r(best.isat_a, 3),
            "ipeak_worst_a": _r(best.ipeak_worst_a, 3),
        },
        "switch_loss_envelope_fet": {
            "mpn": fet.mpn, "manufacturer": fet.manufacturer,
            "qg_total_c": fet.qg_total_c, "technology": fet.technology,
        },
        "worst_vds_v": _r(result.worst_vds_v, 2),
        "worst_id_a": _r(result.worst_id_a, 3),
        "min_isat_ratio": result.min_isat_ratio,
        "loss_curve": curve,
    }


def design_provenance(result: Any, *, topology: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Top-level provenance for the whole design — magnetic, fsw*, switch class
    — each in the uniform B1 envelope, so the design is auditable end-to-end."""
    from heaviside import provenance

    best = result.best
    fet = result.envelope_fet
    inputs = {
        "topology": topology,
        "inputVoltage": spec.get("inputVoltage"),
        "operatingPoints": spec.get("operatingPoints"),
        "min_isat_ratio": result.min_isat_ratio,
    }
    return {
        "magnetic": provenance.make(
            producer="MKF.design_magnetics_from_converter",
            method="total_loss_argmin_over_fsw",
            source_ref=f"{best.core_shape}/{best.core_material}",
            inputs=inputs,
        ),
        "switching_frequency": provenance.make(
            producer="heaviside.stages.frequency_sweep",
            method="coarse_grid+golden_section",
            source_ref=f"{round(float(result.fsw_star_hz))}Hz",
            inputs=inputs,
        ),
        "switch_class": provenance.make(
            producer="heaviside.stages.frequency_sweep.select_envelope_fet",
            method="lowest_qg_envelope",
            source_ref=fet.mpn,
            inputs={"worst_vds_v": result.worst_vds_v, "worst_id_a": result.worst_id_a},
        ),
    }


def _r(x: Any, n: int = 4) -> Any:
    return round(float(x), n) if isinstance(x, (int, float)) else None
