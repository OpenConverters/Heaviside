"""Unit tests for ``heaviside.topologies``."""

from __future__ import annotations

import pytest

from heaviside import topologies
from heaviside.topologies.registry import CONVERTERS, MAGNETICS_ONLY, TOPOLOGIES, get


@pytest.mark.unit
class TestRegistry:
    def test_total_count(self) -> None:
        assert len(TOPOLOGIES) == 27

    def test_24_converters(self) -> None:
        assert len(CONVERTERS) == 24

    def test_3_magnetic_only(self) -> None:
        assert len(MAGNETICS_ONLY) == 3

    def test_unique_names(self) -> None:
        names = [t.name for t in TOPOLOGIES]
        assert len(names) == len(set(names))

    def test_unique_mas_schemas_per_kind(self) -> None:
        # Forward variants share forward.json — that is the only allowed collision.
        schemas = [(t.mas_schema, t.name) for t in CONVERTERS]
        forward_users = [n for s, n in schemas if s == "forward"]
        assert set(forward_users) == {
            "single_switch_forward",
            "two_switch_forward",
            "active_clamp_forward",
        }

    def test_get_known(self) -> None:
        assert get("buck").name == "buck"

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown topology 'nonsense'"):
            get("nonsense")


@pytest.mark.unit
class TestPublicAPI:
    # della-Pollock cutover (abt #48): the per-topology ``design()`` dispatch (process_converter)
    # is gone — ``heaviside.topologies`` is now purely the registry (get / names / TOPOLOGIES).
    def test_registry_exported(self) -> None:
        assert hasattr(topologies, "get")
        assert hasattr(topologies, "names")
        assert hasattr(topologies, "TOPOLOGIES")
        assert not hasattr(topologies, "design"), "the retired process_converter dispatch must be gone"

    def test_names_returns_27(self) -> None:
        assert len(topologies.names()) == 27
        assert "buck" in topologies.names()
        assert "vienna" in topologies.names()
