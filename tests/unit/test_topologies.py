"""Unit tests for ``heaviside.topologies``."""

from __future__ import annotations

import importlib

import pytest

from heaviside import topologies
from heaviside.topologies import dispatch
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
class TestTopologyModules:
    @pytest.mark.parametrize("name", [t.name for t in TOPOLOGIES])
    def test_module_importable(self, name: str) -> None:
        mod = importlib.import_module(f"heaviside.topologies.{name}")
        assert hasattr(mod, "design"), f"{name} missing design()"
        assert hasattr(mod, "ENTRY"), f"{name} missing ENTRY"
        assert mod.ENTRY.name == name


@pytest.mark.unit
class TestPublicAPI:
    def test_design_and_get_exported(self) -> None:
        assert hasattr(topologies, "design")
        assert hasattr(topologies, "get")
        assert hasattr(topologies, "TOPOLOGIES")

    def test_names_returns_27(self) -> None:
        assert len(topologies.names()) == 27
        assert "buck" in topologies.names()
        assert "vienna" in topologies.names()


@pytest.mark.unit
class TestDispatchErrors:
    def test_topology_dispatch_error_message(self) -> None:
        # Construct manually without engine to validate the error message format.
        entry = get("vienna")
        err = dispatch.TopologyDispatchError(entry, entry.pyom_names)
        assert "vienna" in str(err)
        assert "vendor/PyOpenMagnetics" in str(err)
