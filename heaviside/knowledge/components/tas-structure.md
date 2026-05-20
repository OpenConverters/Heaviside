# TAS/data/ NDJSON Storage Reference

TAS (Topology Agnostic Structure) stores finished components and converter designs. The production database is in `TAS/data/` as NDJSON files (one JSON document per line, SI base units).

## v2 Schema — Per-Discriminator Wrappers

**All component files use v2 per-discriminator wrappers.** Each line is a JSON object whose top-level key is the component type. There is NO `inputs`/`outputs` envelope and NO flat structure in production files.

## File Index

| File | Count (approx.) | Top-Level Keys |
|------|-----------------|----------------|
| `mosfets.ndjson` | ~1,200 | `mosfet` |
| `diodes.ndjson` | ~1,100 | `diode`, `distributorsInfo` |
| `igbts.ndjson` | ~300 | `igbt` |
| `capacitors.ndjson` | ~9,500 | `capacitor` |
| `resistors.ndjson` | ~1,600 | `resistor` |
| `magnetics.ndjson` | ~9,600 | `magnetic` |
| `converters.ndjson` | ~47 | `inputs`, `topology` (+ optional `outputs`, `simulation`) |
| `controllers.ndjson` | ~90 | Flat (non-standard — no schema yet) |
| `quarantine.ndjson` | ~10,692 | mixed (incomplete entries awaiting recovery) |

## v2 Structures

### MOSFETs

```json
{
  "mosfet": {
    "manufacturerInfo": {
      "name": "EPC",
      "reference": "EPC2019",
      "status": "production",
      "datasheetUrl": "https://...",
      "datasheetInfo": {
        "part": {"partNumber": "EPC2019", "technology": "GaN", "subType": "nChannel"},
        "electrical": {"drainSourceVoltage": 200, "onResistance": 0.05, "continuousDrainCurrent": 8.5}
      }
    }
  }
}
```

### Diodes

```json
{
  "diode": {
    "manufacturerInfo": {
      "name": "STMicroelectronics",
      "reference": "STPS30L60CT",
      "status": "production",
      "datasheetUrl": "https://...",
      "datasheetInfo": {
        "part": {"partNumber": "STPS30L60CT", "technology": "Si", "subType": "schottky"},
        "electrical": {"reverseVoltage": 60, "forwardVoltage": 0.42, "forwardCurrent": 30}
      }
    }
  },
  "distributorsInfo": []
}
```

Note: `distributorsInfo` is a sibling of `diode` at the top level, NOT inside `manufacturerInfo`.

### IGBTs

```json
{
  "igbt": {
    "manufacturerInfo": {
      "name": "Fuji Electric",
      "reference": "2MBI100XAA120-50",
      "status": "production",
      "datasheetInfo": {
        "part": {"partNumber": "2MBI100XAA120-50", "technology": "Si"},
        "electrical": {"collectorEmitterVoltage": 1200, "continuousCollectorCurrent": 100}
      }
    }
  }
}
```

### Capacitors

```json
{
  "capacitor": {
    "manufacturerInfo": {
      "datasheetInfo": {
        "part": {"partNumber": "UPW1H102MHD", "technology": "Alum. Electrolytic"},
        "electrical": {"capacitance": {"nominal": 0.001}, "ratedVoltage": 50, "esr": 0.034}
      }
    }
  }
}
```

### Resistors

```json
{
  "resistor": {
    "manufacturerInfo": {
      "name": "Vishay",
      "reference": "WSK25125L000FEA",
      "datasheetInfo": {
        "part": {"partNumber": "WSK25125L000FEA", "technology": "currentSenseShunt"},
        "electrical": {"resistance": {"nominal": 0.005}, "tolerance": 0.01, "powerRating": 1.0}
      }
    }
  }
}
```

### Magnetics

```json
{
  "magnetic": {
    "manufacturerInfo": {
      "name": "Würth Elektronik",
      "reference": "744383560R33",
      "family": "WE-MAPI",
      "datasheetInfo": {
        "part": {"description": "WE-MAPI 4020 0.33uH"},
        "electrical": {
          "inductance": {"nominal": 3.3e-7},
          "dcResistance": {"maximum": 0.0085},
          "ratedCurrent": 9.6
        }
      }
    }
  }
}
```

### Converters

```json
{
  "inputs": {
    "designRequirements": {
      "inputType": "dc",
      "inputVoltage": {"nominal": 48.0},
      "outputs": [{"name": "out1", "voltage": {"nominal": 12.0}}],
      "switchingFrequency": {"nominal": 200000.0}
    },
    "operatingPoints": [
      {"name": "nominal", "inputVoltage": 48.0, "outputs": [{"name": "out1", "power": 150.0}]}
    ]
  },
  "topology": {
    "stages": [...],
    "interStageCircuit": []
  }
}
```

## Querying the Database

```python
# MOSFETs
mosfet_data = entry["mosfet"]["manufacturerInfo"]["datasheetInfo"]

# Diodes
diode_data = entry["diode"]["manufacturerInfo"]["datasheetInfo"]

# IGBTs
igbt_data = entry["igbt"]["manufacturerInfo"]["datasheetInfo"]

# Capacitors
cap_data = entry["capacitor"]["manufacturerInfo"]["datasheetInfo"]

# Resistors
res_data = entry["resistor"]["manufacturerInfo"]["datasheetInfo"]

# Magnetics
mag_data = entry["magnetic"]["manufacturerInfo"]["datasheetInfo"]
```

## quarantine.ndjson

Entries that cannot be validated (missing electrical specs, malformed data, unverified datasheets) go to `quarantine.ndjson`. Each entry MUST include:

```json
{
  "_quarantine": {
    "reason": "skeleton entry - empty electrical block",
    "originalFile": "mosfets.ndjson",
    "dateQuarantined": "2026-04-09",
    "requiredFields": ["drainSourceVoltage", "onResistance", "continuousDrainCurrent"]
  },
  "mosfet": { ... }
}
```

## Valid SI Units (MANDATORY)

| Quantity | Unit | Wrong |
|----------|------|-------|
| Voltage | V | mV, kV |
| Current | A | mA, uA |
| Resistance | Ohm | mOhm, kOhm |
| Capacitance | F | uF, nF, pF |
| Inductance | H | uH, mH, nH |
| Frequency | Hz | MHz, kHz |
| Time | s | us, ns, ms |
| Charge | C | nC, uC |
| Energy | J | mJ, uJ |
| Temperature | C (Celsius) | K |
| Dimensions | m | mm, cm |

## Converter BOM References

Components in `topology.stages[].circuit.components[].data` use URI references:

```json
{
  "name": "Q1",
  "role": "highSideSwitch",
  "data": "TAS/data/mosfets.ndjson?partNumber=EPC2019"
}
```

Or inline PEAS for custom magnetics:
```json
{
  "name": "T1",
  "role": "mainTransformer",
  "data": { "magnetic": { "manufacturerInfo": { "name": "custom" } } }
}
```

The reference format is `<file>?partNumber=<MPN>` and resolves to the matching entry.
