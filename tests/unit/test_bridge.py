"""Unit tests for ``heaviside.bridge`` — no PyOpenMagnetics required.

Uses ``monkeypatch`` to replace the lazy PyOM import with a fake
extension that returns canned responses. This keeps the test suite
fast (~ms) while still exercising the dispatch loop, error handling,
and TAS annotation logic. Real end-to-end coverage against PyOM lives
in ``tests/integration/test_bridge_integration.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside import bridge


def _mas(*, scoring: float, shape: str, material: str, n_windings: int) -> dict:
    """Build a minimal MAS-shaped design dict."""
    return {
        "scoring": scoring,
        "mas": {
            "inputs": {
                "designRequirements": {
                    # The L the magnetic was sized at; harvested as the authoritative inductance.
                    "magnetizingInductance": {"nominal": 200e-6},
                },
            },
            "magnetic": {
                "core": {
                    "functionalDescription": {
                        "shape": {"name": shape},
                        "material": {"name": material},
                        "gapping": [],
                    },
                },
                "coil": {
                    "functionalDescription": [
                        {
                            "name": (["pri", "sec0", "sec1"][i] if i < 3 else f"w{i}"),
                            "numberTurns": 10 * (i + 1),
                            "wire": {"name": f"AWG{20 + i}"},
                        }
                        for i in range(n_windings)
                    ],
                },
            },
            "outputs": {},
        },
    }


def _single_magnetic_tas() -> dict:
    """Minimal TAS with one magnetic (placeholder URL pattern)."""
    return {
        "topology": {
            "stages": [
                {
                    "name": "power_stage",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "TAS/data/mosfets.ndjson?placeholder"},
                            {"name": "L1", "data": "TAS/data/magnetics.ndjson?placeholder"},
                            {"name": "C_out", "data": "TAS/data/capacitors.ndjson?placeholder"},
                        ],
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def test_attach_single_magnetic_replaces_placeholder() -> None:
    tas = _single_magnetic_tas()
    designs = [
        bridge.MagneticDesign(
            scoring=4.2,
            mas=_mas(scoring=4.2, shape="PQ 20/16", material="3C95", n_windings=1)["mas"],
            elapsed_s=1.5,
        ),
    ]
    out = bridge.attach_magnetics_to_tas(tas, designs)

    # Same object (mutated in-place).
    assert out is tas

    components = tas["topology"]["stages"][0]["circuit"]["components"]
    l1 = next(c for c in components if c["name"] == "L1")
    # New PEAS-shaped emission: data is the full MAS envelope (dict),
    # not the placeholder URL string. Scoring is stashed as a TAS-extra
    # sibling. Legacy `category`/`mas`/`mas_scoring` fields are gone.
    assert isinstance(l1["data"], dict), "placeholder URL must be replaced by PEAS doc"
    assert "magnetic" in l1["data"]
    assert "category" not in l1
    assert "mas" not in l1
    assert "mas_scoring" not in l1
    assert l1["scoring"] == 4.2
    assert l1["data"]["magnetic"]["core"]["functionalDescription"]["shape"]["name"] == "PQ 20/16"

    # Non-magnetic components untouched.
    q1 = next(c for c in components if c["name"] == "Q1")
    assert q1["data"] == "TAS/data/mosfets.ndjson?placeholder"


def test_attach_picks_up_inline_category_magnetic() -> None:
    """Magnetics declared by inline ``category=='magnetic'`` are also bound."""
    tas = {
        "topology": {
            "stages": [
                {
                    "name": "x",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {
                                "name": "T1",
                                "category": "magnetic",
                                "inductances": [1e-3, 250e-6],
                                "coupling": 0.999,
                            },
                        ]
                    },
                }
            ],
            "interStageConnections": [],
        }
    }
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="ETD 29", material="N87", n_windings=2)["mas"],
            elapsed_s=0.1,
        ),
    ]
    bridge.attach_magnetics_to_tas(tas, designs)

    t1 = tas["topology"]["stages"][0]["circuit"]["components"][0]
    assert t1["data"]["magnetic"]["core"]["functionalDescription"]["shape"]["name"] == "ETD 29"
    # Inline numeric inductances are preserved alongside the PEAS data —
    # both the round-trip writer (which reads `inductances`) and the
    # agent layer (which reads `data.magnetic`) can coexist.
    assert t1["inductances"] == [1e-3, 250e-6]


def test_attach_empty_designs_raises() -> None:
    with pytest.raises(bridge.BridgeError, match="'designs' is empty"):
        bridge.attach_magnetics_to_tas(_single_magnetic_tas(), [])


def test_attach_no_magnetics_in_tas_raises() -> None:
    tas = {
        "topology": {
            "stages": [
                {
                    "name": "x",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "TAS/data/mosfets.ndjson?p"},
                        ]
                    },
                }
            ],
            "interStageConnections": [],
        }
    }
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
            elapsed_s=0.1,
        ),
    ]
    with pytest.raises(bridge.BridgeError, match="zero magnetic components"):
        bridge.attach_magnetics_to_tas(tas, designs)


# -----------------------------------------------------------------------------
# attach_magnetics_to_tas — multi-magnetic
# -----------------------------------------------------------------------------


def _multi_magnetic_tas() -> dict:
    """Two magnetics: T1 + L_out0 (ACF-style)."""
    return {
        "topology": {
            "stages": [
                {
                    "name": "iso",
                    "role": "isolation",
                    "circuit": {
                        "components": [
                            {"name": "T1", "data": "TAS/data/magnetics.ndjson?placeholder=T1"},
                        ]
                    },
                },
                {
                    "name": "out0",
                    "role": "outputRectifier",
                    "circuit": {
                        "components": [
                            {
                                "name": "L_out0",
                                "data": "TAS/data/magnetics.ndjson?placeholder=L_out0",
                            },
                            {"name": "C_out0", "data": "TAS/data/capacitors.ndjson?p"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def test_attach_multi_magnetic_without_mapping_raises() -> None:
    tas = _multi_magnetic_tas()
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="x", material="y", n_windings=2)["mas"],
            elapsed_s=0.1,
        ),
    ]
    with pytest.raises(bridge.BridgeError, match="2 magnetic components"):
        bridge.attach_magnetics_to_tas(tas, designs)


def test_attach_multi_magnetic_with_mapping_succeeds() -> None:
    tas = _multi_magnetic_tas()
    designs = [
        bridge.MagneticDesign(
            scoring=5.0,
            mas=_mas(scoring=5.0, shape="ETD 39", material="N87", n_windings=2)["mas"],
            elapsed_s=0.2,
        ),
        bridge.MagneticDesign(
            scoring=3.0,
            mas=_mas(scoring=3.0, shape="PQ 26/25", material="3C95", n_windings=1)["mas"],
            elapsed_s=0.2,
        ),
    ]
    bridge.attach_magnetics_to_tas(
        tas,
        designs,
        mapping={"T1": 0, "L_out0": 1},
    )

    t1 = tas["topology"]["stages"][0]["circuit"]["components"][0]
    lout = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["data"]["magnetic"]["core"]["functionalDescription"]["shape"]["name"] == "ETD 39"
    assert lout["data"]["magnetic"]["core"]["functionalDescription"]["shape"]["name"] == "PQ 26/25"


def test_attach_multi_magnetic_mapping_missing_key_raises() -> None:
    tas = _multi_magnetic_tas()
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
            elapsed_s=0.1,
        ),
    ]
    with pytest.raises(bridge.BridgeError, match="mapping mismatch"):
        bridge.attach_magnetics_to_tas(tas, designs, mapping={"T1": 0})


def test_attach_multi_magnetic_mapping_bad_index_raises() -> None:
    tas = _multi_magnetic_tas()
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
            elapsed_s=0.1,
        ),
    ]
    with pytest.raises(bridge.BridgeError, match="out of range"):
        bridge.attach_magnetics_to_tas(
            tas,
            designs,
            mapping={"T1": 0, "L_out0": 5},
        )


# ---------------------------------------------------------------------------
# abt #48 cutover: the frequency-sweep seam designs the MAIN magnetic from a
# Kirchhoff per-topology seed (not MKF's process_converter converter model).
# ---------------------------------------------------------------------------


def _ktas(*magnetics: tuple[str, int]) -> dict[str, Any]:
    """A minimal Kirchhoff-shaped TAS carrying ``magnetics`` as
    ``(name, n_windings)`` — each a magnetic component with a MAS Inputs seed
    whose turnsRatios length encodes the winding count (windings - 1 entries)."""
    comps = []
    for name, nw in magnetics:
        seed = {
            "designRequirements": {
                "magnetizingInductance": {"nominal": 1e-4},
                "turnsRatios": [{"nominal": 2.0} for _ in range(max(0, nw - 1))],
            },
            "operatingPoints": [{"excitationsPerWinding": [{} for _ in range(nw)]}],
        }
        comps.append({"name": name, "data": {"magnetic": {}, "inputs": seed}})
    return {"topology": {"stages": [{"circuit": {"components": comps}}]}}


def test_main_magnetic_seed_picks_registry_main_transformer():
    from heaviside.topologies import get

    entry = get("push_pull")  # magnetic_binding {"T1": None, "L_out0": "outputInductor"}
    # Kirchhoff names the output inductor "Lout" (not the registry "L_out0"); the
    # main "T1" coincides, so the registry main role wins by name.
    name, seed = bridge._main_magnetic_seed_from_ktas(entry, _ktas(("T1", 4), ("Lout", 1)))
    assert name == "T1"
    assert len(seed["operatingPoints"][0]["excitationsPerWinding"]) == 4


def test_main_magnetic_seed_sole_inductor_is_main():
    from heaviside.topologies import get

    entry = get("buck")  # magnetic_binding {"L1": None}
    name, _ = bridge._main_magnetic_seed_from_ktas(entry, _ktas(("L1", 1)))
    assert name == "L1"


def test_main_magnetic_seed_structural_fallback_to_transformer():
    from heaviside.topologies import get

    entry = get("push_pull")
    # Names drift entirely (no "T1"): the structural rule picks the lone
    # multi-winding transformer over the single-winding inductor.
    name, seed = bridge._main_magnetic_seed_from_ktas(entry, _ktas(("XF", 3), ("LO", 1)))
    assert name == "XF"
    assert len(seed["designRequirements"]["turnsRatios"]) == 2


def test_main_magnetic_seed_ambiguous_raises():
    from heaviside.topologies import get

    entry = get("push_pull")
    # No name match + two transformers ⇒ ambiguous ⇒ loud, no silent guess.
    with pytest.raises(bridge.BridgeError, match="cannot identify the main magnetic"):
        bridge._main_magnetic_seed_from_ktas(entry, _ktas(("A", 3), ("B", 4)))


def test_main_magnetic_seed_missing_inputs_raises():
    from heaviside.topologies import get

    entry = get("buck")
    tas = {
        "topology": {
            "stages": [
                {
                    "circuit": {
                        "components": [
                            {"name": "L1", "data": {"magnetic": {}}}  # no "inputs" seed
                        ]
                    }
                }
            ]
        }
    }
    with pytest.raises(bridge.BridgeError, match="no MAS Inputs seed"):
        bridge._main_magnetic_seed_from_ktas(entry, tas)


def test_design_magnetics_at_fsw_rejects_slow_path():
    # fast=False (full-sim from a MAS seed) is not wired; it must surface loudly
    # rather than fall back to the retired MKF process_converter converter model.
    spec = {"operatingPoints": [{"outputVoltages": [12], "outputCurrents": [1]}]}
    with pytest.raises(bridge.BridgeError, match="fast=False"):
        bridge.design_magnetics_at_fsw("buck", spec, 100_000.0, fast=False)


def test_design_magnetics_at_fsw_rejects_desired_inductance():
    # The BASE-schema guard stays load-bearing under the Kirchhoff seam: an
    # injected desiredInductance would make Kirchhoff size around a pre-set L.
    spec = {
        "desiredInductance": 1e-4,
        "operatingPoints": [{"outputVoltages": [12], "outputCurrents": [1]}],
    }
    with pytest.raises(bridge.BridgeError, match="BASE-schema"):
        bridge.design_magnetics_at_fsw("buck", spec, 100_000.0)
