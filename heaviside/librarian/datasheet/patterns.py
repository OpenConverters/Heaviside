"""Per-category datasheet parameter patterns + required-field sets.

These are the **standardised parameter keys** the extractor emits
and the **regex patterns** it uses to recognise them in the
parameter-name column of a datasheet's Electrical Characteristics
table.

The key set deliberately overlaps the SAS / RAS / CAS electrical
schemas — e.g. a MOSFET datasheet's "RDS(ON)" row produces
``"onResistance"``, matching ``sas.mosfet.electrical.onResistance``.
The extractor itself is schema-agnostic; the *caller* (typically an
``enrich_*`` helper, or the ``component-librarian`` agent) is
responsible for splicing extracted values into a TAS envelope.

Required-field sets
-------------------

``REQUIRED_<CAT>`` mirrors the schema-required electrical fields the
:mod:`heaviside.librarian.fetcher.convert` converters enforce — so
that a datasheet-only enrichment path can fail with the exact same
``IncompleteSourceError.missing_field`` strings as a distributor-only
path.  This makes the auditor → librarian repair-recipe loop coherent
across both enrichment sources.

Why explicit patterns instead of a fuzzy LLM matcher?
-----------------------------------------------------

Regex patterns are deterministic and auditable: when the librarian
agent reports "datasheet missing reverseRecoveryCharge", we can show
exactly which strings *would* have matched.  The Proteus reader used
a similar regex approach; we keep it and tighten the failure path
(strict, no silent ``None``).
"""

from __future__ import annotations

__all__ = [
    "CATEGORY_PATTERNS",
    "PARAM_UNITS",
    "REQUIRED_BY_CATEGORY",
]


# Parameter name → list of regex patterns matching the *first*
# column ("Parameter" / "Characteristic") of an Electrical
# Characteristics table.  Patterns are tested case-insensitively
# against both the raw cell text and a whitespace-stripped form (so
# "V\nDS" matches "VDS"-style patterns).
#
# Order within each list is significant — the *first* matching
# pattern wins.  We list the most specific patterns first to avoid
# e.g. a bare "Drain Current" row swallowing a "Pulsed Drain
# Current" row.

_MOSFET_PATTERNS: dict[str, list[str]] = {
    "drainSourceVoltage": [
        r"Drain[-\s]?Source\s+Voltage",
        r"V\(BR\)DSS",
        r"VDS\s*\(BR\)",
        r"VDSS",
        r"Breakdown\s+Voltage",
    ],
    "onResistance": [
        r"Static\s+Drain[-\s]?Source\s+On[-\s]?Resistance",
        r"Drain[-\s]?Source\s+On[-\s]?Resistance",
        r"RDS\(ON\)",
        r"Rds\(on\)",
        r"On[-\s]?Resistance",
    ],
    "continuousDrainCurrent": [
        r"Continuous\s+Drain\s+Current",
        r"ID\s*\(Continuous\)",
        r"Drain\s+Current\s+\(Continuous\)",
    ],
    "totalGateCharge": [
        r"Total\s+Gate\s+Charge",
        r"Qg\s*\(total\)",
        r"\bQg\b",
    ],
    "gateThresholdVoltage": [
        r"Gate\s+Threshold\s+Voltage",
        r"VGS\(th\)",
        r"V\(GS\)\(th\)",
        r"\bVTH\b",
    ],
    "outputCapacitance": [
        r"Output\s+Capacitance",
        r"\bCoss\b",
    ],
    "reverseTransferCapacitance": [
        r"Reverse\s+Transfer\s+Capacitance",
        r"\bCrss\b",
    ],
    "inputCapacitance": [
        r"Input\s+Capacitance",
        r"\bCiss\b",
    ],
    "gateDrainCharge": [
        r"Gate[-\s]?Drain\s+Charge",
        r"\bQgd\b",
        r"Miller\s+Charge",
    ],
    "gateSourceCharge": [
        r"Gate[-\s]?Source\s+Charge",
        r"\bQgs\b",
    ],
    "reverseRecoveryCharge": [
        r"Reverse\s+Recovery\s+Charge",
        r"\bQrr\b",
    ],
    "reverseRecoveryTime": [
        r"Reverse\s+Recovery\s+Time",
        r"\btrr\b",
    ],
    "bodyDiodeForwardVoltage": [
        r"Body\s+Diode\s+Forward\s+Voltage",
        r"Source[-\s]?Drain\s+Diode\s+Forward\s+Voltage",
        r"\bVSD\b",
    ],
}


_DIODE_PATTERNS: dict[str, list[str]] = {
    "reverseVoltage": [
        r"Repetitive\s+Peak\s+Reverse\s+Voltage",
        r"DC\s+Reverse\s+Voltage",
        r"VRRM",
        r"\bVR\b",
    ],
    "forwardVoltage": [
        r"Forward\s+Voltage",
        r"\bVF\b",
    ],
    "forwardCurrent": [
        r"Average\s+Rectified\s+Forward\s+Current",
        r"Forward\s+Current\s+\(Average\)",
        r"IF\(AV\)",
        r"\bIO\b",
    ],
    "reverseRecoveryCharge": [
        r"Reverse\s+Recovery\s+Charge",
        r"\bQrr\b",
    ],
    "reverseRecoveryTime": [
        r"Reverse\s+Recovery\s+Time",
        r"\btrr\b",
    ],
    "junctionCapacitance": [
        r"Junction\s+Capacitance",
        r"\bCj\b",
        r"\bCt\b",
    ],
}


_IGBT_PATTERNS: dict[str, list[str]] = {
    "collectorEmitterVoltage": [
        r"Collector[-\s]?Emitter\s+Voltage",
        r"VCES",
        r"V\(BR\)CES",
    ],
    "collectorEmitterSaturation": [
        r"Collector[-\s]?Emitter\s+Saturation\s+Voltage",
        r"VCE\(sat\)",
        r"VCE\(on\)",
    ],
    "continuousCollectorCurrent": [
        r"Continuous\s+Collector\s+Current",
        r"IC\s*\(continuous\)",
        r"IC25",  # Ic @ Tc=25°C — common datasheet shorthand
    ],
    "gateEmitterThreshold": [
        r"Gate[-\s]?Emitter\s+Threshold\s+Voltage",
        r"VGE\(th\)",
    ],
    "turnOnEnergy": [
        r"Turn[-\s]?On\s+Switching\s+Energy",
        r"\bEon\b",
    ],
    "turnOffEnergy": [
        r"Turn[-\s]?Off\s+Switching\s+Energy",
        r"\bEoff\b",
    ],
}


_CAPACITOR_PATTERNS: dict[str, list[str]] = {
    "capacitance": [
        r"^Capacitance$",
        r"Nominal\s+Capacitance",
        r"\bCAP\b",
    ],
    "ratedVoltage": [
        r"Rated\s+Voltage",
        r"Working\s+Voltage",
        r"WVDC",
        r"\bUR\b",
    ],
    "esr": [
        r"Equivalent\s+Series\s+Resistance",
        r"\bESR\b",
    ],
    "rippleCurrent": [
        r"Rated\s+Ripple\s+Current",
        r"Ripple\s+Current",
        r"\bIR\b",
    ],
    "leakageCurrent": [
        r"Leakage\s+Current",
        r"\bILC\b",
    ],
    "dissipationFactor": [
        r"Dissipation\s+Factor",
        r"\btan\s*\xce\xb4\b",  # tan δ
        r"\bDF\b",
    ],
}


_RESISTOR_PATTERNS: dict[str, list[str]] = {
    "resistance": [
        r"Resistance\s+Value",
        r"^Resistance$",
        r"Nominal\s+Resistance",
    ],
    "tolerance": [
        r"^Tolerance$",
        r"Resistance\s+Tolerance",
    ],
    "powerRating": [
        r"Power\s+Rating",
        r"Rated\s+Power",
        r"^Power$",
    ],
    "temperatureCoefficient": [
        r"Temperature\s+Coefficient",
        r"\bTCR\b",
    ],
    "maximumVoltage": [
        r"Maximum\s+Working\s+Voltage",
        r"Limiting\s+Element\s+Voltage",
    ],
}


CATEGORY_PATTERNS: dict[str, dict[str, list[str]]] = {
    "mosfets": _MOSFET_PATTERNS,
    "diodes": _DIODE_PATTERNS,
    "igbts": _IGBT_PATTERNS,
    "capacitors": _CAPACITOR_PATTERNS,
    "resistors": _RESISTOR_PATTERNS,
}


# Schema-required electrical fields per category — mirrors the
# tightened SAS / RAS / CAS schemas (May 2026 audit; see AGENTS.md
# "Schema Validation Status").  These are the fields the
# converters in :mod:`heaviside.librarian.fetcher.convert` refuse to
# default and the auditor flags as critical gaps.
REQUIRED_BY_CATEGORY: dict[str, frozenset[str]] = {
    "mosfets": frozenset(
        {
            "drainSourceVoltage",
            "onResistance",
            "continuousDrainCurrent",
            "gateThresholdVoltage",
            "outputCapacitance",
            "totalGateCharge",
        }
    ),
    "diodes": frozenset(
        {
            "reverseVoltage",
            "forwardVoltage",
            "forwardCurrent",
            "reverseRecoveryCharge",
        }
    ),
    "igbts": frozenset(
        {
            "collectorEmitterVoltage",
            "collectorEmitterSaturation",
            "continuousCollectorCurrent",
        }
    ),
    "capacitors": frozenset(
        {
            "capacitance",
            "ratedVoltage",
            "esr",
            "rippleCurrent",
        }
    ),
    "resistors": frozenset(
        {
            "resistance",
            "tolerance",
            "powerRating",
        }
    ),
}


# SI unit hint per extracted parameter — used by the extractor to
# disambiguate units when the value cell omits them (rare but
# happens, e.g. when the unit is in the column header instead of
# inline with the value).
#
# Values are the *target* SI unit; the extractor doesn't apply a
# conversion when the cell already carries an SI-prefixed unit
# (``"230 pF"`` → 230e-12), it only uses this hint when the cell is
# a bare number and the unit must be inferred from the parameter
# semantic.
PARAM_UNITS: dict[str, str] = {
    # Voltage
    "drainSourceVoltage": "V",
    "gateThresholdVoltage": "V",
    "bodyDiodeForwardVoltage": "V",
    "reverseVoltage": "V",
    "forwardVoltage": "V",
    "collectorEmitterVoltage": "V",
    "collectorEmitterSaturation": "V",
    "gateEmitterThreshold": "V",
    "ratedVoltage": "V",
    "maximumVoltage": "V",
    # Resistance
    "onResistance": "Ω",
    "resistance": "Ω",
    "esr": "Ω",
    # Current
    "continuousDrainCurrent": "A",
    "forwardCurrent": "A",
    "continuousCollectorCurrent": "A",
    "rippleCurrent": "A",
    "leakageCurrent": "A",
    "reverseLeakageCurrent": "A",
    # Charge
    "totalGateCharge": "C",
    "gateDrainCharge": "C",
    "gateSourceCharge": "C",
    "reverseRecoveryCharge": "C",
    # Capacitance
    "outputCapacitance": "F",
    "reverseTransferCapacitance": "F",
    "inputCapacitance": "F",
    "junctionCapacitance": "F",
    "capacitance": "F",
    # Time
    "reverseRecoveryTime": "s",
    # Power / energy
    "powerRating": "W",
    "turnOnEnergy": "J",
    "turnOffEnergy": "J",
    # Dimensionless
    "tolerance": "",
    "dissipationFactor": "",
    "temperatureCoefficient": "ppm/K",
}
