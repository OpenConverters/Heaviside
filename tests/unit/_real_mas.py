"""Real, PyOM-evaluable magnetic fixtures for the extractor unit tests.

Background
----------
The extractor's Isat enrichment now delegates *entirely* to
``PyOpenMagnetics.calculate_saturation_current`` and **raises** if PyOM
rejects the input — the analytical ``B_sat·N·A_e/L`` fallback was deleted
because magnetics math must come from MKF (see ~/.claude/CLAUDE.md), and
the project rule forbids that formula even in test fixtures used as
ground truth.

The old extractor tests fed *synthetic minimal-MAS* shapes (just
``effectiveArea`` + a ``saturation`` curve + ``numberTurns``) that real
PyOM rejects (``key 'bobbin' not found``), so they silently exercised the
forbidden fallback. This module replaces those shapes with **complete**
magnetics: a real core shape + material + gap + winding list, completed
by ``magnetic_autocomplete`` so that ``calculate_saturation_current``
returns genuine, gap-aware MKF physics.

Usage
-----
    from tests.unit._real_mas import real_magnetic, isat_of

    mag = real_magnetic(
        shape="ETD 29/16/10", material="3C95", gap_mm=1.0,
        windings=[{"name": "a", "turns": 22, "side": "primary"},
                  {"name": "b", "turns": 22, "side": "primary"}],
    )
    # `mag` is a full magnetic dict (core+coil, processedDescription
    # populated, material expanded with its real B-H curve). Drop it in
    # wherever a fixture previously hand-built `core`+`coil`.

    expected = isat_of(mag, temperature_c=100.0)   # PyOM ground truth

``real_magnetic`` results are cached (autocomplete is deterministic), so
re-using the same spec across tests is cheap. Each call returns a fresh
deep copy, so tests may mutate the result without cross-test leakage.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path
from typing import Any

import pytest

# A known-good, schema-conforming MAS example we clone as a template so
# the partial we hand to ``magnetic_autocomplete`` always conforms (the
# winding-entry shape, ``bobbin: "basic"`` name-reference, etc. are
# fiddly to hand-roll correctly).
_BASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "MAS"
    / "examples"
    / "01_simple_inductor_etd34_n87_1Hz.json"
)


@functools.lru_cache(maxsize=1)
def _base_magnetic_json() -> str:
    return json.dumps(json.loads(_BASE_PATH.read_text())["magnetic"])


@functools.cache
def _autocomplete_json(spec_json: str) -> str:
    """Build + autocomplete a magnetic from a normalised spec; cached."""
    pyom = pytest.importorskip("PyOpenMagnetics")
    spec = json.loads(spec_json)
    base = json.loads(_base_magnetic_json())
    wire_template = base["coil"]["functionalDescription"][0]

    mag = copy.deepcopy(base)
    mag["core"]["functionalDescription"].update(
        shape=spec["shape"],
        material=spec["material"],
        gapping=(
            [{"type": "subtractive", "length": spec["gap_mm"] / 1000.0}]
            if spec["gap_mm"] > 0
            else []
        ),
    )
    mag["coil"]["functionalDescription"] = [
        {
            **copy.deepcopy(wire_template),
            "name": w["name"],
            "numberTurns": int(w["turns"]),
            "isolationSide": w["side"],
            "numberParallels": int(w.get("parallels", 1)),
        }
        for w in spec["windings"]
    ]
    # Keep ``bobbin: "basic"`` (a name reference) so autocomplete
    # regenerates the bobbin geometry for the chosen shape; dropping it
    # makes autocomplete fail with ``key 'bobbin' not found``.

    full = pyom.PyOpenMagnetics.magnetic_autocomplete(mag, {})
    if not (isinstance(full, dict) and "coil" in full and "core" in full):
        # autocomplete signals a schema/processing failure by returning
        # ``{"data": "Exception: ..."}`` — surface it loudly.
        raise RuntimeError(f"magnetic_autocomplete rejected fixture spec {spec!r}: {full!r}")
    return json.dumps(full)


def real_magnetic(
    *,
    shape: str,
    material: str = "3C95",
    gap_mm: float = 0.0,
    windings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a complete, PyOM-evaluable magnetic dict.

    ``windings`` is a list of ``{"name", "turns", "side"[, "parallels"]}``
    where ``side`` is ``"primary"`` / ``"secondary"``. The returned dict
    has core + coil with ``processedDescription`` populated and the
    material expanded to its real B-H curve, so the extractor's
    ``calculate_saturation_current`` call succeeds and
    ``core.functionalDescription.material.saturation`` /
    ``core.processedDescription.effectiveParameters.effectiveArea`` /
    ``coil.functionalDescription[*]`` all read back as the harvest paths
    expect.
    """
    spec = {
        "shape": shape,
        "material": material,
        "gap_mm": float(gap_mm),
        "windings": [
            {
                "name": w["name"],
                "turns": int(w["turns"]),
                "side": w["side"],
                "parallels": int(w.get("parallels", 1)),
            }
            for w in windings
        ],
    }
    return json.loads(_autocomplete_json(json.dumps(spec, sort_keys=True)))


def isat_of(magnetic: dict[str, Any], temperature_c: float = 100.0) -> float:
    """PyOM ground-truth saturation current for a complete magnetic.

    Use in tests to compute the expected Isat the extractor should stamp
    (ground truth = MKF, never the analytical formula).
    """
    pyom = pytest.importorskip("PyOpenMagnetics")
    return float(
        pyom.PyOpenMagnetics.calculate_saturation_current(dict(magnetic), float(temperature_c))
    )
