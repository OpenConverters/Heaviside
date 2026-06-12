"""PyOpenMagnetics dispatcher.

Calls ``PyOpenMagnetics.process_converter()`` for any topology in the registry.
Tries each candidate ``pyom_names`` string in order; raises a single, loud
``TopologyDispatchError`` if every variant is rejected by PyOpenMagnetics with
"Unknown topology". Errors from valid topologies (missing fields, bad data)
propagate unchanged.

Per the project's "no fallbacks" rule, this module never:

* substitutes default values for missing fields,
* swallows engine errors and returns a placeholder result, or
* falls back to a different topology when one fails.

It only translates a canonical Python name into the string PyOpenMagnetics
recognises today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, cast

from heaviside.topologies.registry import TopologyEntry, get

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping


class ProcessConverterResult(TypedDict, total=False):
    """Result envelope from ``PyOpenMagnetics.process_converter``.

    PyOpenMagnetics returns either a populated ``Inputs`` JSON or
    ``{"error": "..."}``. We keep the envelope as a TypedDict so call-sites
    type-check; downstream code branches on ``"error" in result``.
    """

    error: str
    inputs: Mapping[str, Any]


class TopologyDispatchError(RuntimeError):
    """Raised when none of a topology's PyOM name variants are recognised."""

    def __init__(self, entry: TopologyEntry, attempted: tuple[str, ...]) -> None:
        super().__init__(
            f"PyOpenMagnetics does not recognise any variant of topology "
            f"{entry.name!r}. Tried: {attempted}. "
            f"This binding is missing upstream — add it to "
            f"vendor/PyOpenMagnetics/ and rebuild (do not work around it here)."
        )
        self.entry = entry
        self.attempted = attempted


def _import_pyom() -> Any:
    """Import the bound PyOpenMagnetics extension via the bridge gateway.

    All production PyOM access flows through ``heaviside.bridge`` so the
    Heaviside settings (saturation + mutual-resistance modelling) are
    applied and verified exactly once. Imported lazily so that static
    analysis and `make types` work in environments without the wheel.
    """
    from heaviside.bridge import _import_pyom as _gateway

    return _gateway()


def design(
    topology: str | TopologyEntry,
    converter_json: Mapping[str, Any],
    *,
    use_ngspice: bool = True,
) -> ProcessConverterResult:
    """Dispatch a converter spec to PyOpenMagnetics.

    Parameters
    ----------
    topology:
        Either the canonical Python name (e.g. ``"buck"``, ``"phase_shifted_full_bridge"``)
        or a ``TopologyEntry`` from the registry.
    converter_json:
        The converter-shaped JSON dict to pass through. Validation against
        the MAS schema is the caller's responsibility (the generated
        classes in ``heaviside.types`` give a loud ``from_dict`` gate).
    use_ngspice:
        Forwarded to PyOpenMagnetics. Set to ``False`` for fast analytical
        runs that skip the ngspice subcircuit invocation.

    Returns
    -------
    The result dict from PyOpenMagnetics. Note this may contain ``{"error": ...}``
    — that is a *valid* response indicating PyOpenMagnetics rejected the
    inputs, distinct from a missing topology binding.

    Raises
    ------
    TopologyDispatchError:
        If every name variant returns the engine's "Unknown topology" error.
        Any other engine error is returned in the envelope, not raised, so the
        caller can decide whether it is fatal.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    pyom = _import_pyom()

    last_error: str | None = None
    for variant in entry.pyom_names:
        raw = pyom.process_converter(variant, dict(converter_json), use_ngspice)
        # PyOpenMagnetics may return a dict or a json string depending on
        # build; normalise once.
        result: dict[str, Any]
        if isinstance(raw, str):
            import json

            result = json.loads(raw)
        elif isinstance(raw, dict):
            result = raw
        else:  # pragma: no cover — defensive
            raise TypeError(
                f"PyOpenMagnetics returned an unexpected type {type(raw).__name__} "
                f"for topology {entry.name!r}"
            )

        err = result.get("error", "")
        if isinstance(err, str) and err.startswith("Exception: Unknown topology"):
            last_error = err
            continue
        # `ProcessConverterResult` is total=False; cast through dict for type-safety.
        return cast(ProcessConverterResult, result)

    # Every variant said "Unknown topology". This is a real binding gap.
    assert last_error is not None  # invariant from loop above
    raise TopologyDispatchError(entry, entry.pyom_names)
