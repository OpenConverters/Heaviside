"""TAS component selector.

Queries the local ``TAS/data/*.ndjson`` catalogue for a real MPN
satisfying stress constraints derived analytically from a converter spec.
Stress -> Constraints -> Selection: every input field is required and
typed; every output carries provenance for the realism gate.

Companion to ``heaviside.librarian`` (which fetches & writes TAS) and
``heaviside.pipeline.realism`` (which checks the picked component
against operating stress). The selector itself touches NO network and
NO write paths — it is a pure read-only function of the TAS DB.
"""

from heaviside.catalogue.assemble import assemble_bom_from_tas
from heaviside.catalogue.selector import (
    Mosfet,
    MosfetConstraints,
    MosfetSelection,
    MosfetTiebreaker,
    SelectionError,
    select_mosfet,
)

__all__ = [
    "Mosfet",
    "MosfetConstraints",
    "MosfetSelection",
    "MosfetTiebreaker",
    "SelectionError",
    "assemble_bom_from_tas",
    "select_mosfet",
]
