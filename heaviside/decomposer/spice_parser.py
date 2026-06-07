"""Minimal ngspice deck parser.

Targets the deterministic output of MKF's ``generate_ngspice_circuit``.
Not a full SPICE-3 parser — we only handle the subset MKF emits today:

* Linear two-pin elements (R / L / C) with a numeric value.
* Voltage / current sources (V / I) with literal or ``PULSE(...)`` syntax.
* Behavioural sources (B) carrying a free-form expression.
* Switches (S / W) and diodes (D) that reference a model name.
* Two-port coupled inductors via ``K``.
* ``.model`` / ``.tran`` / ``.save`` / ``.options`` / ``.ic`` / ``.end`` control cards.
* Section banner comments of the form ``* <title>`` immediately before an element block.

Anything we don't recognise is raised — fail loudly per the project's
"no fallbacks" rule, because a silent skip would corrupt the
decomposition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ElementKind = Literal[
    "voltage_source",
    "current_source",
    "behavioural_source",
    "resistor",
    "inductor",
    "capacitor",
    "switch",
    "diode",
    "coupling",
]


@dataclass(frozen=True, slots=True)
class SpiceElement:
    """One parsed SPICE deck element.

    Attributes
    ----------
    refdes:
        The original reference designator (``S1``, ``L1``, ``Cout`` …).
    kind:
        High-level classification used by the stencils.
    nodes:
        Ordered tuple of net names this element connects to (length 2
        for R/L/C/D, 4 for S, etc.). For ``K`` couplings this is the
        tuple of referenced inductor refdeses (``("L_p", "L_s")``).
    value:
        Free-form value string as it appeared in the deck
        (``"2.200000e-05"``, ``"PULSE(0 5 0 ...)"``, ``"V=V(l_in)-V(vout)"``).
        ``None`` for elements whose value is a model name only.
    model:
        Model name for switches / diodes (``"SW1"``, ``"DIDEAL"``).
    section:
        The most-recent ``* <title>`` banner comment that preceded this
        element, or ``None`` if the deck had none.
    """

    refdes: str
    kind: ElementKind
    nodes: tuple[str, ...]
    value: str | None = None
    model: str | None = None
    section: str | None = None


@dataclass(slots=True)
class SpiceDeck:
    """Parsed view of a single ngspice deck."""

    title_comments: list[str] = field(default_factory=list)
    elements: list[SpiceElement] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)  # name → "D(IS=… RS=…)" body
    control_cards: list[str] = field(default_factory=list)  # .tran / .save / .ic / .options

    def by_refdes(self, refdes: str) -> SpiceElement:
        for el in self.elements:
            if el.refdes == refdes:
                return el
        raise KeyError(f"No element {refdes!r} in deck")

    def of_kind(self, kind: ElementKind) -> list[SpiceElement]:
        return [el for el in self.elements if el.kind == kind]


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------

_BANNER_RE = re.compile(r"^\*\s*(?P<title>\S.*?)\s*$")
_MODEL_RE = re.compile(r"^\.model\s+(?P<name>\S+)\s+(?P<body>.+)$", re.IGNORECASE)
_CONTROL_PREFIXES = (
    ".tran",
    ".save",
    ".options",
    ".ic",
    ".end",
    ".include",
    ".lib",
    ".nodeset",
    ".param",
    ".global",
)

# MKF emits 'PULSE(0 5 0 ...)' as one logical token but with spaces inside the
# parens. We must not split on the inner whitespace. Helper: collapse the
# parenthesised group into a single space-free token before re.split, then
# restore it.
_PAREN_RE = re.compile(r"\((?P<inner>[^()]*)\)")


def _tokenize(line: str) -> list[str]:
    """Split a SPICE line on whitespace, treating ``(...)`` groups as atomic."""
    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group("inner"))
        return f"§{len(placeholders) - 1}§"

    masked = _PAREN_RE.sub(_stash, line)
    tokens = masked.split()
    return [
        re.sub(r"§(\d+)§", lambda m: "(" + placeholders[int(m.group(1))] + ")", tok)
        for tok in tokens
    ]


def _parse_element(line: str, section: str | None) -> SpiceElement:
    tokens = _tokenize(line)
    refdes = tokens[0]
    first = refdes[0].upper()

    # Linear two-terminal: R/L/C  refdes n1 n2 value
    if first in {"R", "L", "C"}:
        if len(tokens) < 4:
            raise ValueError(f"Malformed {first} line: {line!r}")
        kind: ElementKind = {"R": "resistor", "L": "inductor", "C": "capacitor"}[first]
        # MKF sometimes adds 'IC=12' as trailing tokens on caps — keep value as
        # just the principal number; pack the rest into value too for fidelity.
        value = " ".join(tokens[3:])
        return SpiceElement(refdes, kind, (tokens[1], tokens[2]), value=value, section=section)

    # Voltage source: V refdes n+ n- value-or-PULSE
    if first == "V":
        if len(tokens) < 4:
            raise ValueError(f"Malformed V line: {line!r}")
        value = " ".join(tokens[3:])
        return SpiceElement(
            refdes, "voltage_source", (tokens[1], tokens[2]), value=value, section=section
        )

    # Current source: I refdes n+ n- value
    if first == "I":
        if len(tokens) < 4:
            raise ValueError(f"Malformed I line: {line!r}")
        value = " ".join(tokens[3:])
        return SpiceElement(
            refdes, "current_source", (tokens[1], tokens[2]), value=value, section=section
        )

    # Behavioural: B refdes n+ n- V=<expr>  or  I=<expr>
    if first == "B":
        if len(tokens) < 4:
            raise ValueError(f"Malformed B line: {line!r}")
        return SpiceElement(
            refdes,
            "behavioural_source",
            (tokens[1], tokens[2]),
            value=" ".join(tokens[3:]),
            section=section,
        )

    # Linear controlled sources (E=VCVS, F=CCCS, G=VCCS, H=CCVS). MKF
    # only emits these as testbench probes (e.g. Evab observing the
    # full-bridge midpoint differential); we classify them as
    # behavioural_source so the generic _is_testbench rule drops them.
    if first in {"E", "F", "G", "H"}:
        if len(tokens) < 4:
            raise ValueError(f"Malformed {first} line: {line!r}")
        return SpiceElement(
            refdes,
            "behavioural_source",
            (tokens[1], tokens[2]),
            value=" ".join(tokens[3:]),
            section=section,
        )

    # Switch: S refdes nd ns nc+ nc- modelname
    if first in {"S", "W"}:
        if len(tokens) < 6:
            raise ValueError(f"Malformed switch line: {line!r}")
        return SpiceElement(
            refdes,
            "switch",
            tuple(tokens[1:5]),
            model=tokens[5],
            section=section,
        )

    # Diode: D refdes n+ n- modelname
    if first == "D":
        if len(tokens) < 4:
            raise ValueError(f"Malformed D line: {line!r}")
        return SpiceElement(
            refdes,
            "diode",
            (tokens[1], tokens[2]),
            model=tokens[3],
            section=section,
        )

    # Mutual inductance: K refdes Lp Ls coupling
    if first == "K":
        if len(tokens) < 4:
            raise ValueError(f"Malformed K line: {line!r}")
        return SpiceElement(
            refdes,
            "coupling",
            (tokens[1], tokens[2]),
            value=tokens[3],
            section=section,
        )

    raise ValueError(f"Unrecognised SPICE element type {first!r} in line: {line!r}")


# -----------------------------------------------------------------------------
# Public parser
# -----------------------------------------------------------------------------


def parse_spice(text: str) -> SpiceDeck:
    """Parse an ngspice deck string into a :class:`SpiceDeck`.

    Raises ``ValueError`` for any line that isn't a comment, blank, control
    card, model card, or recognised element — silent skipping would corrupt
    the downstream stencil match.
    """
    deck = SpiceDeck()
    current_section: str | None = None

    # Pre-pass: fold ngspice ``+``-continuation lines into the preceding
    # logical line. CLLC emits multi-line ``.save`` directives this way;
    # without folding the parser would see "+ v(node_c) ..." as a standalone
    # element line and raise.
    folded: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("+") and folded:
            folded[-1] = folded[-1] + " " + stripped[1:].strip()
        else:
            folded.append(raw)

    for raw in folded:
        line = raw.strip()
        if not line:
            current_section = None  # blank line ends a section
            continue

        # Comments — either section banner or freeform.
        if line.startswith("*"):
            m = _BANNER_RE.match(line)
            title = m.group("title") if m else line[1:].strip()
            # The first comment in the deck is conventionally the deck
            # title; subsequent ones act as section banners.
            if not deck.elements and deck.title_comments == []:
                deck.title_comments.append(title)
            elif title.startswith(("Vin=", "L=", "Iout=", "Generated by", "switching frequency")):
                # Auxiliary metadata comment line in the deck preamble.
                deck.title_comments.append(title)
            else:
                current_section = title
            continue

        # Model cards
        m = _MODEL_RE.match(line)
        if m:
            deck.models[m.group("name")] = m.group("body").strip()
            continue

        # Control cards
        if line.lower().startswith(_CONTROL_PREFIXES):
            deck.control_cards.append(line)
            continue

        # Everything else must be an element.
        deck.elements.append(_parse_element(line, current_section))

    return deck
