"""Contract tests for the quicktype-generated schema-class layer.

``heaviside/types/_generated/`` is never committed — ``make types``
(scripts/gen_types.py) regenerates it from the MAS/PEAS/SAS/CAS/RAS
schema submodules, and CI generates it before this suite runs. These
tests pin the contract the rest of the codebase relies on:

* the ``heaviside.types`` façade exposes every advertised top-level,
* ``from_dict`` round-trips a real, PyOM-autocompleted magnetic,
* ``from_dict`` fails loudly on malformed payloads (no silent defaults),
* signatures can be written ``def f(mas: Magnetic)`` (the user-facing
  point of the layer).
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

import pytest

import heaviside.types as types_facade

if TYPE_CHECKING:
    from heaviside.types import Magnetic

pytestmark = pytest.mark.unit

_generated_missing = (
    importlib.util.find_spec("heaviside.types._generated") is None  # type: ignore[attr-defined]
)


@pytest.mark.skipif(
    _generated_missing,
    reason="generated types absent — run `make types` (CI always generates them)",
)
class TestGeneratedLayer:
    def test_facade_exposes_every_advertised_export(self) -> None:
        for name in types_facade._EXPORTS:
            cls = getattr(types_facade, name)
            assert isinstance(cls, type), f"{name} is not a class"
            assert hasattr(cls, "from_dict") and hasattr(cls, "to_dict"), (
                f"{name} lacks from_dict/to_dict converters"
            )

    def test_real_magnetic_round_trips_through_magnetic_class(self) -> None:
        """A complete PyOM-autocompleted magnetic survives
        Magnetic.from_dict → to_dict with the harvest paths intact."""
        pytest.importorskip("PyOpenMagnetics")
        from heaviside.types import Magnetic
        from tests.unit._real_mas import real_magnetic

        m = real_magnetic(
            shape="ETD 29/16/10",
            windings=[{"name": "primary", "turns": 20, "side": "primary"}],
        )
        rt = Magnetic.from_dict(m).to_dict()

        shape = rt["core"]["functionalDescription"]["shape"]
        shape_name = shape["name"] if isinstance(shape, dict) else shape
        assert shape_name == "ETD 29/16/10"
        assert rt["coil"]["functionalDescription"][0]["numberTurns"] == 20

    def test_malformed_payload_is_rejected_loudly(self) -> None:
        """No-fallback rule: a corrupt magnetic must raise, never produce
        a defaulted instance."""
        from heaviside.types import Magnetic

        bad: dict[str, Any] = {"core": {"functionalDescription": {"shape": 42}}}
        with pytest.raises((AssertionError, KeyError, AttributeError, TypeError)):
            Magnetic.from_dict(bad)

    def test_signature_annotation_pattern(self) -> None:
        """The layer's raison d'être: `def f(mas: Magnetic)` type-checks
        and works at runtime with a real instance."""
        from heaviside.types import Magnetic

        def n_windings(mas: Magnetic) -> int:
            return len(mas.coil.functional_description)

        pytest.importorskip("PyOpenMagnetics")
        from tests.unit._real_mas import real_magnetic

        m = Magnetic.from_dict(
            real_magnetic(
                shape="ETD 29/16/10",
                windings=[{"name": "primary", "turns": 20, "side": "primary"}],
            )
        )
        assert n_windings(m) == 1


def test_facade_unknown_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = types_facade.NotARealSchemaClass


_TAS_CATEGORIES = {
    "mosfets": "Mosfet",
    "diodes": "Diode",
    "igbts": "Igbt",
    "capacitors": "Capacitor",
    "resistors": "Resistor",
    "magnetics": "Magnetic",
}


@pytest.mark.skipif(
    _generated_missing,
    reason="generated types absent — run `make types` (CI always generates them)",
)
@pytest.mark.parametrize("category", sorted(_TAS_CATEGORIES))
def test_schema_valid_tas_rows_round_trip_generated_class(category: str) -> None:
    """Every TAS row the JSON schema accepts must also be accepted by the
    quicktype class for that category — the generated layer may be more
    lenient than the schema, never stricter. (Full-DB sweep:
    ``scripts/validate_tas.py``.)"""
    from itertools import islice
    from pathlib import Path

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.librarian.tas import SCHEMA_MAP, ValidationError, validate_component

    path = Path(__file__).resolve().parents[2] / "TAS" / "data" / f"{category}.ndjson"
    if not path.exists():
        pytest.skip(f"TAS submodule data missing: {path}")

    cls = getattr(types_facade, _TAS_CATEGORIES[category])
    _, unwrap = SCHEMA_MAP[category]

    checked = 0
    for _lineno, env in islice(iter_envelopes(path), 300):
        try:
            validate_component(category, env)
        except ValidationError:
            continue  # pre-existing data/schema drift — not this layer's bug
        cls.from_dict(unwrap(env))  # must not raise
        checked += 1
    assert checked > 0, f"no schema-valid rows in the first 300 of {category}"


@pytest.mark.skipif(
    _generated_missing,
    reason="generated types absent — run `make types` (CI always generates them)",
)
class TestDesignedMagneticGate:
    """enrich_tas_for_realism validates attached magnetics via Magnetic.from_dict."""

    def test_real_designed_magnetic_passes_the_gate(self) -> None:
        pytest.importorskip("PyOpenMagnetics")
        from heaviside.pipeline.extract import enrich_tas_for_realism
        from tests.unit._real_mas import real_magnetic

        magnetic = real_magnetic(
            shape="ETD 29/16/10",
            windings=[{"name": "primary", "turns": 9, "side": "primary"}],
        )
        tas = {
            "topology": {
                "stages": [
                    {
                        "circuit": {
                            "components": [
                                {
                                    "name": "L1",
                                    "data": {"inputs": {}, "magnetic": magnetic, "outputs": {}},
                                },
                            ]
                        }
                    }
                ]
            }
        }
        enrich_tas_for_realism(tas, topology="not_a_registered_topology", spec={})

    def test_malformed_designed_magnetic_raises_loudly(self) -> None:
        from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism

        tas = {
            "topology": {
                "stages": [
                    {
                        "circuit": {
                            "components": [
                                {
                                    "name": "L1",
                                    "data": {
                                        "magnetic": {
                                            "core": {"functionalDescription": {"shape": 42}}
                                        }
                                    },
                                },
                            ]
                        }
                    }
                ]
            }
        }
        with pytest.raises(EnrichmentError, match="designed magnetic 'L1'"):
            enrich_tas_for_realism(tas, topology="buck", spec={})
