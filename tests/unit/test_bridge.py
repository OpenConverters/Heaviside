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


# -----------------------------------------------------------------------------
# Fake PyOpenMagnetics extension
# -----------------------------------------------------------------------------


class _FakePyOM:
    """Records every call and returns programmed responses.

    Responses for ``design_magnetics_from_converter`` come from the
    ``responses`` queue. Responses for ``get_extra_components_inputs``
    come from ``extras_responses`` (queue). Responses for
    ``calculate_advised_magnetics`` come from ``advised_responses``
    (queue).
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        extras_responses: list[Any] | None = None,
        advised_responses: list[Any] | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._extras = list(extras_responses or [])
        self._advised = list(advised_responses or [])
        self.calls: list[tuple] = []
        self.extras_calls: list[tuple] = []
        self.advised_calls: list[tuple] = []

    def design_magnetics_from_converter(
        self, name, spec, max_results, core_mode, use_ngspice, weights
    ):
        self.calls.append(
            (name, dict(spec), max_results, core_mode, use_ngspice, weights)
        )
        if not self._responses:
            raise AssertionError("FakePyOM ran out of programmed responses.")
        return self._responses.pop(0)

    def get_extra_components_inputs(self, name, spec, mode, magnetic_json):
        self.extras_calls.append(
            (name, dict(spec), mode, magnetic_json if magnetic_json is None else dict(magnetic_json))
        )
        if not self._extras:
            raise AssertionError("FakePyOM ran out of extras responses.")
        return self._extras.pop(0)

    def calculate_advised_magnetics(self, inputs, max_results, core_mode):
        self.advised_calls.append((dict(inputs), max_results, core_mode))
        if not self._advised:
            raise AssertionError("FakePyOM ran out of advised responses.")
        return self._advised.pop(0)


def _patch_pyom(monkeypatch: pytest.MonkeyPatch, fake: _FakePyOM) -> None:
    monkeypatch.setattr(bridge, "_import_pyom", lambda: fake)


def _mas(*, scoring: float, shape: str, material: str, n_windings: int) -> dict:
    """Build a minimal MAS-shaped design dict."""
    return {
        "scoring": scoring,
        "mas": {
            "inputs": {},
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
                        {"name": (["pri", "sec0", "sec1"][i] if i < 3 else f"w{i}"),
                         "numberTurns": 10 * (i + 1),
                         "wire": {"name": f"AWG{20 + i}"}}
                        for i in range(n_windings)
                    ],
                },
            },
            "outputs": {},
        },
    }


# -----------------------------------------------------------------------------
# design_magnetics
# -----------------------------------------------------------------------------


def test_design_magnetics_returns_sorted_designs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Top-scoring design comes first, even if PyOM returns them out of order."""
    fake = _FakePyOM(
        responses=[
            {"data": [
                _mas(scoring=2.0, shape="PQ 26/25", material="3C95", n_windings=1),
                _mas(scoring=5.0, shape="RM 8/I",   material="3C97", n_windings=1),
                _mas(scoring=3.5, shape="ETD 29",   material="N87",  n_windings=1),
            ]}
        ],
    )
    _patch_pyom(monkeypatch, fake)

    designs = bridge.design_magnetics("buck", {"any": "spec"}, max_results=3)

    assert [d.scoring for d in designs] == [5.0, 3.5, 2.0]
    assert designs[0].core_shape_name == "RM 8/I"
    assert designs[0].core_material_name == "3C97"
    assert designs[0].winding_names == ("pri",)
    assert designs[0].elapsed_s >= 0.0

    # Verify dispatch sent the right call to PyOM.
    assert len(fake.calls) == 1
    name, spec, n, mode, ngs, w = fake.calls[0]
    assert name == "buck"
    assert spec == {"any": "spec"}
    assert n == 3
    assert mode == "available cores"
    assert ngs is False
    assert w is None


def test_design_magnetics_retries_unknown_topology_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the first variant returns 'Unknown topology', the second is tried."""
    fake = _FakePyOM(
        responses=[
            {"error": "Exception: Unknown topology cuk"},
            {"data": [_mas(scoring=1.0, shape="PQ 20/16", material="3C95", n_windings=2)]},
        ],
    )
    _patch_pyom(monkeypatch, fake)

    # cuk has two registered pyom_names: ("cuk", "cukConverter").
    designs = bridge.design_magnetics("cuk", {"any": "spec"})

    assert len(designs) == 1
    assert [call[0] for call in fake.calls] == ["cuk", "cukConverter"]


def test_design_magnetics_unknown_topology_all_variants_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every variant says 'Unknown topology', BridgeError is raised."""
    fake = _FakePyOM(
        responses=[
            {"error": "Exception: Unknown topology cuk"},
            {"error": "Exception: Unknown topology cukConverter"},
        ],
    )
    _patch_pyom(monkeypatch, fake)

    with pytest.raises(bridge.BridgeError, match="upstream binding gap"):
        bridge.design_magnetics("cuk", {"any": "spec"})


def test_design_magnetics_real_engine_error_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-'Unknown topology' error propagates immediately."""
    fake = _FakePyOM(
        responses=[{"error": "Exception: Input JSON does not conform to schema!"}],
    )
    _patch_pyom(monkeypatch, fake)

    with pytest.raises(bridge.BridgeError, match="does not conform to schema"):
        bridge.design_magnetics("buck", {"bad": "spec"})

    # No retry attempted — buck only has one variant anyway, but the
    # point is the error was reported, not silently skipped.
    assert len(fake.calls) == 1


def test_design_magnetics_empty_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePyOM(responses=[{"data": []}])
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="zero designs"):
        bridge.design_magnetics("buck", {"any": "spec"})


def test_design_magnetics_unexpected_shape_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(responses=[{"data": "not a list"}])
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="expected list"):
        bridge.design_magnetics("buck", {"any": "spec"})


def test_design_magnetics_missing_mas_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        responses=[{"data": [{"scoring": 1.0, "no_mas_here": True}]}],
    )
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="no 'mas' field"):
        bridge.design_magnetics("buck", {"any": "spec"})


# -----------------------------------------------------------------------------
# attach_magnetics_to_tas — single magnetic
# -----------------------------------------------------------------------------


def _single_magnetic_tas() -> dict:
    """Minimal TAS with one magnetic (placeholder URL pattern)."""
    return {
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
        "interStageCircuit": [],
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

    components = tas["stages"][0]["circuit"]["components"]
    l1 = next(c for c in components if c["name"] == "L1")
    assert "data" not in l1, "placeholder URL must be removed"
    assert l1["category"] == "magnetic"
    assert l1["mas_scoring"] == 4.2
    assert l1["mas"]["core"]["functionalDescription"]["shape"]["name"] == "PQ 20/16"

    # Non-magnetic components untouched.
    q1 = next(c for c in components if c["name"] == "Q1")
    assert q1["data"] == "TAS/data/mosfets.ndjson?placeholder"


def test_attach_picks_up_inline_category_magnetic() -> None:
    """Magnetics declared by inline ``category=='magnetic'`` are also bound."""
    tas = {
        "stages": [{
            "name": "x", "role": "switchingCell",
            "circuit": {"components": [
                {"name": "T1", "category": "magnetic",
                 "inductances": [1e-3, 250e-6], "coupling": 0.999},
            ]},
        }],
        "interStageCircuit": [],
    }
    designs = [
        bridge.MagneticDesign(
            scoring=1.0,
            mas=_mas(scoring=1.0, shape="ETD 29", material="N87", n_windings=2)["mas"],
            elapsed_s=0.1,
        ),
    ]
    bridge.attach_magnetics_to_tas(tas, designs)

    t1 = tas["stages"][0]["circuit"]["components"][0]
    assert t1["mas"]["core"]["functionalDescription"]["shape"]["name"] == "ETD 29"
    # Inline numeric inductances are preserved alongside the MAS — both
    # the round-trip writer (which reads `inductances`) and the agent
    # layer (which reads `mas`) can coexist.
    assert t1["inductances"] == [1e-3, 250e-6]


def test_attach_empty_designs_raises() -> None:
    with pytest.raises(bridge.BridgeError, match="'designs' is empty"):
        bridge.attach_magnetics_to_tas(_single_magnetic_tas(), [])


def test_attach_no_magnetics_in_tas_raises() -> None:
    tas = {
        "stages": [{
            "name": "x", "role": "switchingCell",
            "circuit": {"components": [
                {"name": "Q1", "data": "TAS/data/mosfets.ndjson?p"},
            ]},
        }],
        "interStageCircuit": [],
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
        "stages": [
            {
                "name": "iso", "role": "isolation",
                "circuit": {"components": [
                    {"name": "T1", "data": "TAS/data/magnetics.ndjson?placeholder=T1"},
                ]},
            },
            {
                "name": "out0", "role": "outputRectifier",
                "circuit": {"components": [
                    {"name": "L_out0", "data": "TAS/data/magnetics.ndjson?placeholder=L_out0"},
                    {"name": "C_out0", "data": "TAS/data/capacitors.ndjson?p"},
                ]},
            },
        ],
        "interStageCircuit": [],
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
        tas, designs, mapping={"T1": 0, "L_out0": 1},
    )

    t1 = tas["stages"][0]["circuit"]["components"][0]
    lout = tas["stages"][1]["circuit"]["components"][0]
    assert t1["mas"]["core"]["functionalDescription"]["shape"]["name"] == "ETD 39"
    assert lout["mas"]["core"]["functionalDescription"]["shape"]["name"] == "PQ 26/25"


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
            tas, designs, mapping={"T1": 0, "L_out0": 5},
        )


# -----------------------------------------------------------------------------
# extra_components — Phase B probing
# -----------------------------------------------------------------------------


def _extras_inputs(*, name: str, kind: str = "magnetic") -> dict:
    """Build a minimal extras inputs envelope as PyOM emits it."""
    return {
        "kind": kind,
        "inputs": {
            "designRequirements": {"name": name},
            "operatingPoints": [{"switchingFrequency": 250000}],
        },
    }


def test_extra_components_parses_magnetic_and_capacitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        extras_responses=[
            [
                _extras_inputs(name="outputInductor", kind="magnetic"),
                _extras_inputs(name="clampCapacitor", kind="capacitor"),
            ],
        ],
    )
    _patch_pyom(monkeypatch, fake)

    main_mas = {"magnetic": {"some": "design"}}
    mags, caps = bridge.extra_components(
        "active_clamp_forward",
        {"any": "spec"},
        mode="REAL",
        main_magnetic_mas=main_mas,
    )
    assert [m.name for m in mags] == ["outputInductor"]
    assert [c.name for c in caps] == ["clampCapacitor"]
    # All extras have inputs preserved verbatim.
    assert mags[0].inputs["designRequirements"]["name"] == "outputInductor"

    # Confirm REAL mode passed the magnetic through.
    assert len(fake.extras_calls) == 1
    name, spec, mode, mag = fake.extras_calls[0]
    assert mode == "REAL"
    assert mag == main_mas


def test_extra_components_real_mode_without_main_magnetic_raises() -> None:
    with pytest.raises(bridge.BridgeError, match="requires main_magnetic_mas"):
        bridge.extra_components("buck", {"any": "spec"}, mode="REAL")


def test_extra_components_ideal_mode_no_main_magnetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(extras_responses=[[]])
    _patch_pyom(monkeypatch, fake)

    mags, caps = bridge.extra_components("buck", {"any": "spec"}, mode="IDEAL")
    assert mags == [] and caps == []
    assert fake.extras_calls[0][2] == "IDEAL"
    assert fake.extras_calls[0][3] is None


def test_extra_components_engine_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        extras_responses=[{"error": "missing dutyCycle"}],
    )
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="missing dutyCycle"):
        bridge.extra_components("buck", {"any": "spec"}, mode="IDEAL")


def test_extra_components_unknown_kind_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        extras_responses=[
            [{"kind": "resistor", "inputs": {"designRequirements": {"name": "R1"}}}],
        ],
    )
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="unknown kind"):
        bridge.extra_components("buck", {"any": "spec"}, mode="IDEAL")


def test_extra_components_missing_name_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        extras_responses=[[{"kind": "magnetic", "inputs": {"designRequirements": {}}}]],
    )
    _patch_pyom(monkeypatch, fake)
    with pytest.raises(bridge.BridgeError, match="designRequirements.name"):
        bridge.extra_components("buck", {"any": "spec"}, mode="IDEAL")


# -----------------------------------------------------------------------------
# design_extra_magnetic
# -----------------------------------------------------------------------------


def test_design_extra_magnetic_returns_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        advised_responses=[
            {"data": [
                _mas(scoring=1.0, shape="A", material="X", n_windings=1),
                _mas(scoring=4.0, shape="B", material="Y", n_windings=1),
                _mas(scoring=2.5, shape="C", material="Z", n_windings=1),
            ]},
        ],
    )
    _patch_pyom(monkeypatch, fake)

    spec = bridge.ExtraMagneticSpec(
        name="outputInductor",
        inputs={"designRequirements": {"name": "outputInductor"}},
    )
    designs = bridge.design_extra_magnetic(spec, max_results=3)
    assert [d.scoring for d in designs] == [4.0, 2.5, 1.0]
    assert fake.advised_calls[0][2] == "available cores"


def test_design_extra_magnetic_engine_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(advised_responses=[{"data": "Exception: catalog empty"}])
    _patch_pyom(monkeypatch, fake)
    spec = bridge.ExtraMagneticSpec(
        name="outputInductor",
        inputs={"designRequirements": {"name": "outputInductor"}},
    )
    with pytest.raises(bridge.BridgeError, match="catalog empty"):
        bridge.design_extra_magnetic(spec)


def test_design_extra_magnetic_empty_data_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(advised_responses=[{"data": []}])
    _patch_pyom(monkeypatch, fake)
    spec = bridge.ExtraMagneticSpec(
        name="outputInductor",
        inputs={"designRequirements": {"name": "outputInductor"}},
    )
    with pytest.raises(bridge.BridgeError, match="zero"):
        bridge.design_extra_magnetic(spec)


# -----------------------------------------------------------------------------
# design_converter_components — orchestrator
# -----------------------------------------------------------------------------


def test_design_converter_components_buck_no_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        responses=[
            {"data": [_mas(scoring=4.0, shape="PQ 20/16", material="3C95", n_windings=1)]},
        ],
        extras_responses=[[]],  # buck has zero extras
    )
    _patch_pyom(monkeypatch, fake)

    components = bridge.design_converter_components("buck", {"any": "spec"})
    assert components.main_magnetic.scoring == 4.0
    assert components.extra_magnetics == {}
    assert components.extra_capacitors == ()
    # No advised_magnetics call because no magnetic extras.
    assert len(fake.advised_calls) == 0


def test_design_converter_components_acf_two_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePyOM(
        responses=[
            {"data": [_mas(scoring=5.0, shape="ETD 39", material="N87", n_windings=2)]},
        ],
        extras_responses=[
            [
                _extras_inputs(name="outputInductor", kind="magnetic"),
                _extras_inputs(name="clampCapacitor", kind="capacitor"),
            ],
        ],
        advised_responses=[
            {"data": [_mas(scoring=3.0, shape="RM 8", material="3C97", n_windings=1)]},
        ],
    )
    _patch_pyom(monkeypatch, fake)

    components = bridge.design_converter_components(
        "active_clamp_forward", {"any": "spec"}
    )
    assert components.main_magnetic.core_shape_name == "ETD 39"
    assert "outputInductor" in components.extra_magnetics
    assert components.extra_magnetics["outputInductor"].core_shape_name == "RM 8"
    assert len(components.extra_capacitors) == 1
    assert components.extra_capacitors[0].name == "clampCapacitor"


# -----------------------------------------------------------------------------
# attach_components_to_tas
# -----------------------------------------------------------------------------


def test_attach_components_acf_binds_main_and_extras() -> None:
    tas = {
        "stages": [
            {"name": "iso", "role": "isolation", "circuit": {"components": [
                {"name": "T1", "data": "TAS/data/magnetics.ndjson?p=T1"},
            ]}},
            {"name": "out0", "role": "outputRectifier", "circuit": {"components": [
                {"name": "L_out0", "data": "TAS/data/magnetics.ndjson?p=L_out0"},
            ]}},
        ],
        "interStageCircuit": [],
    }
    main = bridge.MagneticDesign(
        scoring=5.0,
        mas=_mas(scoring=5.0, shape="ETD 39", material="N87", n_windings=2)["mas"],
        elapsed_s=0.1,
    )
    out_ind = bridge.MagneticDesign(
        scoring=3.0,
        mas=_mas(scoring=3.0, shape="RM 8", material="3C97", n_windings=1)["mas"],
        elapsed_s=0.1,
    )
    components = bridge.ConverterComponents(
        main_magnetic=main,
        extra_magnetics={"outputInductor": out_ind},
        extra_capacitors=(),
    )
    bridge.attach_components_to_tas(tas, components, topology="active_clamp_forward")

    t1 = tas["stages"][0]["circuit"]["components"][0]
    lout = tas["stages"][1]["circuit"]["components"][0]
    assert t1["mas"]["core"]["functionalDescription"]["shape"]["name"] == "ETD 39"
    assert lout["mas"]["core"]["functionalDescription"]["shape"]["name"] == "RM 8"


def test_attach_components_unknown_tas_magnetic_raises() -> None:
    """TAS has a magnetic not in the registry binding."""
    tas = {
        "stages": [{"name": "x", "role": "isolation", "circuit": {"components": [
            {"name": "T1", "data": "TAS/data/magnetics.ndjson?p"},
            {"name": "BOGUS", "data": "TAS/data/magnetics.ndjson?p"},
        ]}}],
        "interStageCircuit": [],
    }
    main = bridge.MagneticDesign(
        scoring=1.0,
        mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
        elapsed_s=0.1,
    )
    components = bridge.ConverterComponents(main_magnetic=main)
    with pytest.raises(bridge.BridgeError, match="have no entry"):
        bridge.attach_components_to_tas(tas, components, topology="flyback")


def test_attach_components_missing_extras_raises() -> None:
    """Registry binds L_out0→outputInductor but components lacks it."""
    tas = {
        "stages": [
            {"name": "iso", "role": "iso", "circuit": {"components": [
                {"name": "T1", "data": "TAS/data/magnetics.ndjson?p"},
            ]}},
            {"name": "out", "role": "out", "circuit": {"components": [
                {"name": "L_out0", "data": "TAS/data/magnetics.ndjson?p"},
            ]}},
        ],
        "interStageCircuit": [],
    }
    main = bridge.MagneticDesign(
        scoring=1.0,
        mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
        elapsed_s=0.1,
    )
    components = bridge.ConverterComponents(main_magnetic=main)  # no extras
    with pytest.raises(bridge.BridgeError, match="outputInductor"):
        bridge.attach_components_to_tas(
            tas, components, topology="active_clamp_forward"
        )


def test_attach_components_topology_without_binding_raises() -> None:
    """A registry entry with empty magnetic_binding cannot auto-bind."""
    tas = {
        "stages": [{"name": "x", "role": "x", "circuit": {"components": [
            {"name": "T1", "data": "TAS/data/magnetics.ndjson?p"},
        ]}}],
        "interStageCircuit": [],
    }
    main = bridge.MagneticDesign(
        scoring=1.0,
        mas=_mas(scoring=1.0, shape="x", material="y", n_windings=1)["mas"],
        elapsed_s=0.1,
    )
    components = bridge.ConverterComponents(main_magnetic=main)
    with pytest.raises(bridge.BridgeError, match="no magnetic_binding"):
        bridge.attach_components_to_tas(
            tas, components, topology="phase_shifted_half_bridge"
        )
