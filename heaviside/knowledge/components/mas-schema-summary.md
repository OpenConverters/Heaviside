# MAS (Magnetic Agnostic Structure) - Quick Reference

**For the full detailed MAS reference, see `knowledge/magnetics/mas-schema.md`.** This file is a summary for component-librarian and component-auditor agents who need to validate magnetic entries in TAS/data/magnetics.ndjson.

## Schema Locations

- `/home/alf/OpenConverters/Proteus/MAS/schemas/MAS.json` -- top-level envelope
- `/home/alf/OpenConverters/Proteus/MAS/schemas/magnetic.json` -- magnetic detail
- `/home/alf/OpenConverters/Proteus/MAS/schemas/inputs.json` -- design requirements
- `/home/alf/OpenConverters/Proteus/MAS/schemas/outputs.json` -- computed results

## Envelope Structure

```json
{
  "inputs": {
    "designRequirements": {
      "magnetizingInductance": {"nominal": 3.3e-7},
      "turnsRatios": [6.0],
      "topology": "Flyback"
    },
    "operatingPoints": []
  },
  "magnetic": {
    "core": { ... },
    "coil": { ... },
    "manufacturerInfo": { ... }
  },
  "outputs": []
}
```

## TAS/data/magnetics.ndjson Structure

```json
{
  "inputs": {
    "designRequirements": {
      "magnetizingInductance": {"nominal": 3.3e-7},
      "topology": "Buck Converter"
    }
  },
  "magnetic": {
    "manufacturerInfo": {
      "name": "Wurth Elektronik",
      "reference": "744383560R33",
      "status": "production",
      "family": "WE-MAPI",
      "datasheetUrl": "https://...",
      "datasheetInfo": {
        "part": {
          "description": "WE-MAPI 4020 0.33uH Metal Alloy Power Inductor",
          "caseCode": "4020",
          "material": "Metal Alloy (Iron)",
          "windingStyle": "round",
          "shielded": true,
          "numberOfWindings": 1,
          "insulationGrade": "functional"
        },
        "electrical": {
          "inductance": {"nominal": 3.3e-7},
          "dcResistance": {"maximum": 0.0085},
          "ratedCurrent": 9.6,
          "saturationCurrentPeak": 12.4,
          "selfResonantFrequency": 250e6
        },
        "thermal": {
          "operatingTemperature": {"minimum": -40, "maximum": 125}
        },
        "mechanical": {
          "length": {"nominal": 0.004},
          "width": {"nominal": 0.004},
          "height": {"nominal": 0.002}
        }
      }
    }
  },
  "outputs": []
}
```

## Electrical Fields

```json
"electrical": {
  "inductance": {
    "nominal": 3.3e-7,      // H -- 330 nH in SI
    "minimum": 2.97e-7,     // H
    "maximum": 3.63e-7      // H
  },
  "dcResistance": {
    "nominal": 0.006,        // Ohm
    "maximum": 0.0085        // Ohm
  },
  "ratedCurrent": 9.6,                // A
  "saturationCurrentPeak": 12.4,      // A -- Isat (30% inductance drop)
  "selfResonantFrequency": 250e6,     // Hz -- SRF
  "leakageInductance": 0,              // H (transformers only)
  "turnsRatio": [6.0],                 // array (transformers only)
  "couplingCoefficient": 0.995         // (transformers only)
}
```

## Ferrite Beads / Common Mode Chokes

Ferrite beads and CMFs are stored differently -- they use impedance at frequency, NOT inductance:

```json
{
  "magnetic": {
    "manufacturerInfo": {
      "datasheetInfo": {
        "part": {
          "componentSubType": "ferrite_bead",
          "description": "Ferrite Bead 600 Ohm @ 100 MHz"
        },
        "electrical": {
          "impedanceAtFrequency": {
            "frequency": 100e6,  // Hz
            "impedance": 600     // Ohm
          },
          "ratedCurrent": 2,
          "dcResistance": {"maximum": 0.05}
        }
      }
    }
  }
}
```

**IMPORTANT**: Ferrite beads do NOT have an `inductance` field. Auditors must EXCLUDE ferrite beads when checking for missing inductance (filter by `componentSubType == "ferrite_bead"`).

## Physics Sanity Checks

| Check | Rule | Violation Means |
|-------|------|-----------------|
| Inductance | 1 nH to 10 H typical | Missing data or wrong units |
| DCR | 1 uOhm to 10 kOhm typical | Wrong units (stored in mOhm?) |
| Rated current | 1 mA to 500 A typical | Missing data |
| SRF | 100 kHz to 10 GHz typical (usually < 1 GHz for power inductors) | Unit error (THz is impossible) |
| Saturation current | > rated current (typically 1.2x to 2x) | Wrong value |
| Coupling coefficient | 0.9 to 0.9999 (transformers) | Outside range means wrong value |

## PyOpenMagnetics Integration

Material and shape names MUST match the PyOpenMagnetics database:

```python
import PyOpenMagnetics as PyOM
materials = PyOM.get_core_materials()  # validate material names
shapes = PyOM.get_core_shapes()        # validate shape names
wires = PyOM.get_wires()                # validate wire names (Litz or solid)
```

Wire names follow the format:
- `"Round 0.5 - Grade 1"` -- solid round wire
- `"Litz 100x0.1 - Grade 1"` -- Litz wire (100 strands of 0.1mm)

## Critical Unit Rules

- **Gap length**: meters (0.0002 = 0.2mm gap), NOT mm
- **Inductance**: Henries, NOT uH or mH
- **DCR**: Ohms, NOT mOhm
- **Frequency**: Hz, NOT MHz or kHz
- **SRF**: Hz (a 22uH inductor at 22.5 MHz SRF is stored as 22500000)

## Common Mistakes

1. **Double unit conversion** -- MHz -> Hz applied twice gives TeraHz values (e.g., 22.5 THz instead of 22.5 MHz). A 22uH inductor cannot self-resonate above ~50 MHz -- THz values are physically impossible.
2. **Gap in mm instead of m** -- 0.2 mm stored as 0.2 gives a 20cm gap. Always store gap in meters.
3. **Forgetting componentSubType for ferrite beads** -- leads to false positives in "missing inductance" checks.
4. **Wrong turns ratio direction** -- `[6.0]` means primary:secondary = 6:1 (step-down). `[0.167]` is step-up.
5. **Skeleton entries with null inductance** -- should go to quarantine, not production.
