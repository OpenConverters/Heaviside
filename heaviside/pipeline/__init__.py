"""heaviside.pipeline — post-decompose validation gates.

Currently exposes the :mod:`heaviside.pipeline.realism` gate (ported from
``proteus/validators/physics.py``).  Future gates (cost, sourceability,
standards-compliance) will land here as additional modules.
"""

from heaviside.pipeline.realism import (
    CheckResult,
    CheckStatus,
    RealismError,
    RealismReport,
    RealismVerdict,
    evaluate_tas,
)

__all__ = (
    "CheckResult",
    "CheckStatus",
    "RealismError",
    "RealismReport",
    "RealismVerdict",
    "evaluate_tas",
)
