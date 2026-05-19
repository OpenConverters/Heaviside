"""heaviside.pipeline — post-decompose validation gates.

Currently exposes:

  * :mod:`heaviside.pipeline.realism` — physics invariant checker ported
    from ``proteus/validators/physics.py``.
  * :mod:`heaviside.pipeline.extract` — topology-aware enrichment that
    stamps derived stresses / duty / scalar Isat onto a populated TAS so
    the realism gate has data to check.  Today covers ``buck``; other
    topologies pass through unchanged.

Future gates (cost, sourceability, standards-compliance) will land here
as additional modules.
"""

from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
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
    "EnrichmentError",
    "RealismError",
    "RealismReport",
    "RealismVerdict",
    "enrich_tas_for_realism",
    "evaluate_tas",
)
