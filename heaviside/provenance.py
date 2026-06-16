"""Uniform provenance envelope for stamped design numbers (master-plan B1).

Every number the designer stamps onto a component — a selected real part, a
junction-temperature estimate, a saturation current — must be *auditable*: a
reviewer should be able to see WHO produced it, by WHAT method, traceable to
WHAT source, and a hash of the inputs that produced it. This module defines
that uniform four-key envelope and the helpers that stamp / read it:

    {producer, method, source_ref, inputs_hash}

* ``producer``   — the code/agent that computed the number
                   (e.g. ``"catalogue.select_mosfet"``, ``"MKF.calculate_saturation_current"``).
* ``method``     — how (the tiebreaker, the model, the sizing rule).
* ``source_ref`` — what it's traceable to (an MPN, a MAS core name, a datasheet).
* ``inputs_hash``— a deterministic short hash of the inputs, so two runs with
                   the same inputs produce the same provenance (re-derivable).

The realism gate consumes this: a stamped real part with no complete
provenance is reported ``UNAVAILABLE`` (origin cannot be audited) — never
silently trusted. House rule: surface the gap, don't paper over it.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REQUIRED_KEYS: tuple[str, ...] = ("producer", "method", "source_ref", "inputs_hash")


@dataclass(frozen=True, slots=True)
class Provenance:
    """The uniform provenance envelope for one stamped number."""

    producer: str
    method: str
    source_ref: str
    inputs_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "producer": self.producer,
            "method": self.method,
            "source_ref": self.source_ref,
            "inputs_hash": self.inputs_hash,
        }


def inputs_hash(inputs: Any) -> str:
    """Deterministic short hash of the inputs that produced a number.

    Canonicalised with sorted keys so dict ordering never changes the hash;
    ``default=str`` lets non-JSON values (enums, frozensets via ``sorted``)
    serialise. Stable across runs and processes — the whole point is
    re-derivability, so this must NOT use ``hash()`` (salted per process).
    """
    canon = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def make(*, producer: str, method: str, source_ref: str, inputs: Any) -> dict[str, str]:
    """Build a complete provenance envelope dict."""
    if not (producer and method and source_ref):
        raise ValueError(
            f"provenance.make: producer/method/source_ref must be non-empty, "
            f"got producer={producer!r} method={method!r} source_ref={source_ref!r}"
        )
    return Provenance(
        producer=str(producer),
        method=str(method),
        source_ref=str(source_ref),
        inputs_hash=inputs_hash(inputs),
    ).as_dict()


def is_complete(prov: Any) -> bool:
    """True iff ``prov`` is a mapping carrying every required key as a
    non-empty string. A partial or absent block is NOT complete — the realism
    gate treats that as ``UNAVAILABLE`` (origin un-auditable)."""
    return isinstance(prov, Mapping) and all(
        isinstance(prov.get(k), str) and prov.get(k) for k in REQUIRED_KEYS
    )


def ensure_selection_canonical(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Add the canonical envelope to an existing ``selection_provenance`` block,
    derived from the detail it already carries, WITHOUT discarding that detail.

    Idempotent: if the block is already complete it is returned unchanged
    (apart from being copied). Returns ``None`` for a non-mapping input so the
    caller can skip it. The existing selection blocks carry ``category`` /
    ``mpn`` / ``manufacturer`` / a ``tiebreaker`` or ``sizing`` rule / a
    ``constraints`` sub-dict — enough to reconstruct a real provenance.
    """
    if not isinstance(block, Mapping):
        return None
    out = dict(block)
    if is_complete(out):
        return out

    category = out.get("category") or "component"
    mpn = out.get("mpn") or out.get("value") or "?"
    # method: the explicit tiebreaker, else the sizing rule, else "selected".
    method = out.get("tiebreaker") or out.get("sizing") or out.get("method") or "selected"
    # inputs: the constraints sub-dict if present, else the whole block minus
    # the canonical keys (so the hash reflects what actually drove the pick).
    inputs = out.get("constraints")
    if inputs is None:
        inputs = {k: v for k, v in out.items() if k not in REQUIRED_KEYS}

    out.setdefault("producer", f"catalogue.select_{category}")
    out.setdefault("method", str(method))
    out.setdefault("source_ref", str(mpn))
    out.setdefault("inputs_hash", inputs_hash(inputs))
    return out


def stamp_components(tas: Mapping[str, Any]) -> int:
    """Walk a decomposed TAS and canonicalise every component's
    ``selection_provenance`` (and ``tj_provenance``) in place.

    Returns the number of provenance blocks canonicalised. Run once after the
    BOM is assembled so every stamped real part carries the uniform envelope
    the realism gate audits.
    """
    n = 0
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        return 0
    stages = topology.get("stages")
    if not isinstance(stages, list):
        return 0
    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        circuit = stage.get("circuit")
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for comp in comps:
            if not isinstance(comp, dict):
                continue
            for key in ("selection_provenance", "tj_provenance"):
                canon = ensure_selection_canonical(comp.get(key))
                if canon is not None:
                    comp[key] = canon
                    n += 1
    return n
