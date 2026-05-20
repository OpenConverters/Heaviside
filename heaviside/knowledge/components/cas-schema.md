# CAS (Capacitor Agnostic Structure) - Schema Reference

CAS defines the schema for all capacitor types: MLCC, aluminum electrolytic, polymer, hybrid polymer, and film.

## Schema Locations

- `/home/alf/OpenConverters/CAS/schemas/CAS.json` -- top-level envelope
- `/home/alf/OpenConverters/CAS/schemas/capacitor.json` -- component detail

## Full Envelope Structure (CAS)

```json
{
  "inputs": { "designRequirements": {} },
  "capacitor": {
    "manufacturerInfo": {
      "datasheetInfo": {
        "part": { ... },
        "electrical": { ... },
        "thermal": { ... },
        "mechanical": { ... },
        "business": { ... },
        "lifetime": { ... },
        "modelParams": { ... },
        "factors": { ... }
      }
    }
  },
  "outputs": {}
}
```

## TAS/data/capacitors.ndjson Structure (FLAT)

In the TAS database, capacitors use a **flat structure** -- no `"capacitor"` wrapper:

```json
{
  "manufacturerInfo": {
    "datasheetInfo": {
      "part": { ... },
      "electrical": { ... },
      "thermal": { ... },
      "mechanical": { ... },
      "business": { ... },
      "lifetime": { ... },
      "modelParams": { ... },
      "factors": { ... }
    }
  }
}
```

## Part Identification

```json
"part": {
  "partNumber": "UPW1H102MHD",
  "series": "UPW",
  "technology": "Alum. Electrolytic",  // see Technology Enum below
  "case": "16x25",
  "matchcodeDescription": "1000uF 50V Electrolytic",
  "useInDcTool": true,
  "internalViewOnly": null
}
```

### Technology Enum

- `"Alum. Electrolytic"` -- Standard aluminum electrolytic
- `"Alum. Polymer"` -- Aluminum polymer (conductive polymer electrolyte)
- `"Hybrid Polymer"` -- Hybrid polymer/electrolytic
- `"Film Capacitor"` -- Polypropylene, polyester, etc.
- `"MLCC Class I"` -- NP0/C0G ceramic (stable, low loss)
- `"MLCC Class II"` -- X5R/X7R/Y5V ceramic (high capacitance, voltage-dependent)
- `"Other"` -- Mica, tantalum, supercap, etc.

## Electrical Fields

```json
"electrical": {
  // CORE SPECS
  "capacitance": {
    "nominal": 0.001,       // F -- 1000uF in SI
    "minimum": 0.0008,      // F -- -20% tolerance
    "maximum": 0.0012       // F -- +20% tolerance
  },
  "ratedVoltage": 50,       // V -- max DC voltage

  // LOSS / ESR
  "dissipationFactor": 0.12,        // ratio (NOT percent) -- tan(delta)
  "dissipationFactorFrequency": 120, // Hz -- frequency for DF measurement
  "esr": 0.034,                     // Ohm -- ESR at esrFrequency
  "esrFrequency": 100000,           // Hz -- ESR measurement frequency
  "esrForLosses": 0.034,            // Ohm -- ESR used for loss calculations

  // CURRENT
  "rippleCurrent": 2.235,           // A -- max ripple current
  "rippleCurrentFrequency": 100000, // Hz -- ripple current test frequency
  "rippleCurrentTemperature": 105,  // C -- ripple current test temperature (nullable)

  // LEAKAGE / INSULATION
  "leakageCurrent": 500e-6,         // A -- DC leakage
  "insulationResistance": 1e9,      // Ohm -- insulation resistance

  // LONG-TERM DRIFT
  "capacitanceDriftLongTermPercent": -15,  // % -- end-of-life capacitance change
  "capacitanceMinimumLongTerm": 0.00085,   // F -- minimum C at end of life

  // THERMAL
  "thermalResistance": 12.5,        // K/W -- Rth junction to ambient (nullable)

  // MLCC-SPECIFIC (nullable for non-MLCC)
  "capacitanceSaturationMLCC": 0.6,  // ratio -- C remaining at rated voltage
  "vthMLCC": 10,                     // V -- voltage at 50% capacitance loss

  // DERATING CURVES
  "rippleCurrentFrequencyPoints": {
    "xData": [1000, 10000, 100000, 500000],     // Hz
    "yData": [0.4, 0.7, 1.0, 1.15]              // multiplier
  },
  "rippleCurrentTemperaturePoints": {
    "xData": [40, 60, 85, 105],                  // C
    "yData": [1.4, 1.2, 1.0, 0.7]               // multiplier
  }
}
```

## Lifetime Fields (Electrolytic)

```json
"lifetime": {
  "lifetimeEndurance": 8000,         // hours -- rated endurance at max temp
  "lifetimeMaximumYears": 15,        // years -- shelf life
  "aexp": 2,                         // -- Arrhenius acceleration exponent
  "bexp": 0.6,                       // -- voltage acceleration exponent
  "deltaT0": 10,                     // K -- temperature doubling interval
  "kfactor": 1,                      // -- ripple current weighting factor
  "vxfactor": 0.8,                   // -- voltage stress factor
  "endDefinitionC": -20,             // % -- end-of-life capacitance change
  "endDefinitionEsr": 200,           // % -- end-of-life ESR increase
  "usefulLifeHours": 50000,          // hours -- expected useful life
  "usefulLifeComments": "at 85C, rated ripple"
}
```

## SPICE Model Parameters

```json
"modelParams": {
  "rs": 0.034,        // Ohm -- series resistance (ESR)
  "cs": 0.001,        // F -- series capacitance
  "ls": 15e-9,        // H -- series inductance (ESL)
  "riso": 1e9         // Ohm -- parallel insulation resistance
}
```

## Physics Sanity Checks

| Check | Rule | Violation Means |
|-------|------|-----------------|
| Capacitance | Must be > 0 and < 1 F | Wrong units (stored in uF?) |
| Rated voltage | Must be > 0 | Missing data |
| ESR for 1nF MLCC | 12-20 Ohm is CORRECT (high impedance at low C) | NOT an error |
| Dissipation factor | Must be < 0.5 (ratio, not percent) | Wrong units |
| Ripple current | Must be > 0 for electrolytic/film | Missing data |
| MLCC saturation | 0.3-0.9 typical for Class II | Wrong value if outside range |

## Common Mistakes

1. **Wrapping in `"capacitor"` key in NDJSON** -- TAS/data/capacitors.ndjson is FLAT (no wrapper)
2. **ESR in milliohms** -- must be in Ohms
3. **Capacitance in microfarads** -- must be in Farads
4. **Skeleton entries** -- entries with `dataCompleteness: "skeleton"` and NULL electrical values must go to quarantine.ndjson
5. **Flagging high ESR on small MLCCs as errors** -- 1nF at 1MHz has Z=159 Ohm, so ESR of 12-20 Ohm with DF=0.08-0.13 is physically correct
