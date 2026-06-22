"""Canonical TAS physics-validator gateway.

Wraps the C++/pybind11 ``tas_validator`` module shipped in the TAS repo
(``TAS/validator/``) — the single source of truth for whether a catalog part is
*physically* possible (Rds_on / Vf / ESR / Isat / energy-density vs bounds).

This is **distinct from and complementary to** the JSON-Schema *structural* gate
in :func:`heaviside.librarian.tas.validate_component`:

* schema gate  → "does this JSON match the TAS/PEAS schema stack?"  (structure)
* physics gate → "are these *values* physically possible?"          (this module)

The physics rules live in canonical C++ (per CLAUDE.md "don't re-implement, use
the canonical source"); the librarian/auditor must call them rather than
re-reasoning physics in Python or an LLM prompt — that is exactly the kind of
parallel, drifting logic path the project forbids.

Per CLAUDE.md "no fallbacks, throw": if the compiled module is absent we raise
:class:`PhysicsValidatorUnavailable` with a build instruction — we never silently
skip the physics gate (a skipped gate would let an impossible part into the DB).
The underlying validator likewise records checks it could not run in
``skipped`` rather than treating missing input as valid.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# heaviside/librarian/physics_validator.py -> repo root is two parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
#: The TAS submodule ships the validator; its build dir holds the compiled module
#: (gitignored build artifact — vendored like PyOpenMagnetics, built in CI).
_VALIDATOR_BUILD = _REPO_ROOT / "TAS" / "validator" / "build"

_MODULE: Any = None


class PhysicsValidatorUnavailable(RuntimeError):
    """The compiled ``tas_validator`` module could not be loaded.

    Raised (never swallowed) so a missing physics gate is loud, not silent.
    """


class PhysicsInvalidError(RuntimeError):
    """A part carries at least one ``IMPOSSIBLE`` physics finding."""

    def __init__(self, mpn: str, findings: "tuple[PhysicsFinding, ...]") -> None:
        self.mpn = mpn
        self.findings = findings
        detail = "; ".join(f"{f.code}: {f.message}" for f in findings) or "(no detail)"
        super().__init__(f"physically invalid part {mpn!r}: {detail}")


def _load() -> Any:
    """Import the ``tas_validator`` module, caching it. Tries a normal import
    first (installed / on PYTHONPATH), then the TAS submodule build dir."""
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    try:
        import tas_validator as _m  # type: ignore[import-not-found]

        _MODULE = _m
        return _MODULE
    except ModuleNotFoundError:
        pass
    matches = sorted(_VALIDATOR_BUILD.glob("tas_validator*.so"))
    if not matches:
        raise PhysicsValidatorUnavailable(
            f"tas_validator compiled module not found in {_VALIDATOR_BUILD}. "
            "Build it:  cd TAS/validator && cmake -B build -G Ninja && cmake --build build  "
            "(see TAS/validator/BUILD.md). The physics gate is mandatory — it is not skipped."
        )
    so = matches[0]
    spec = importlib.util.spec_from_file_location("tas_validator", so)
    if spec is None or spec.loader is None:
        raise PhysicsValidatorUnavailable(f"could not create import spec for {so}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tas_validator"] = module
    spec.loader.exec_module(module)
    _MODULE = module
    return _MODULE


def _severity_name(sev: Any) -> str:
    """Normalise a pybind ``Severity`` enum (or string) to its bare name."""
    return str(getattr(sev, "name", sev)).rsplit(".", 1)[-1].upper()


@dataclass(frozen=True)
class PhysicsFinding:
    code: str
    severity: str
    component: str
    reference: str
    message: str
    value: float | None
    threshold: float | None


@dataclass(frozen=True)
class PhysicsVerdict:
    valid: bool
    findings: tuple[PhysicsFinding, ...]
    skipped: tuple[str, ...]

    @property
    def impossible(self) -> tuple[PhysicsFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "IMPOSSIBLE")

    @property
    def suspicious(self) -> tuple[PhysicsFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "SUSPICIOUS")


def _coerce(v: Any) -> PhysicsVerdict:
    findings = tuple(
        PhysicsFinding(
            code=str(getattr(f, "code", "")),
            severity=_severity_name(getattr(f, "severity", "")),
            component=str(getattr(f, "component", "")),
            reference=str(getattr(f, "reference", "")),
            message=str(getattr(f, "message", "")),
            value=getattr(f, "value", None),
            threshold=getattr(f, "threshold", None),
        )
        for f in v.findings
    )
    return PhysicsVerdict(valid=bool(v.valid), findings=findings, skipped=tuple(v.skipped))


def validate_physics(record: dict[str, Any]) -> PhysicsVerdict:
    """Return the physics verdict for one part envelope (e.g. ``{"capacitor": …}``).

    Raises :class:`PhysicsValidatorUnavailable` if the compiled module is missing
    (never returns a soft pass). A field present but malformed raises in the
    underlying C++ (``MalformedField``); that propagates as a ``RuntimeError``.
    """
    return _coerce(_load().validate(record))


def assert_physically_valid(
    record: dict[str, Any], *, mpn: str | None = None
) -> PhysicsVerdict:
    """Raise :class:`PhysicsInvalidError` if the part has any ``IMPOSSIBLE``
    finding; otherwise return the verdict (carrying any ``SUSPICIOUS`` findings
    and the list of ``skipped`` checks for the caller to log)."""
    verdict = validate_physics(record)
    if not verdict.valid:
        raise PhysicsInvalidError(mpn or "UNKNOWN", verdict.impossible)
    return verdict


def check_codes() -> list[str]:
    """Every physics check id the validator can emit (for docs/tests)."""
    return list(_load().check_codes())


def available() -> bool:
    """True iff the compiled validator can be loaded — for diagnostics only.
    Call sites must NOT use this to silently skip validation."""
    try:
        _load()
        return True
    except PhysicsValidatorUnavailable:
        return False
