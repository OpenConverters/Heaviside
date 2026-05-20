# RAS (Resistor Agnostic Structure) - Schema Reference

RAS defines the schema for all resistor types: thin film, thick film, wirewound, shunt, metal oxide, carbon composition, and foil.

## Schema Locations

- `/home/alf/OpenConverters/RAS/schemas/RAS.json` -- top-level envelope
- `/home/alf/OpenConverters/RAS/schemas/resistor.json` -- component detail

## Envelope Structure

```json
{
  "inputs": { "designRequirements": {} },
  "resistor": {
    "manufacturerInfo": {
      "datasheetInfo": {
        "part": { ... },
        "electrical": { ... },
        "thermal": { ... },
        "mechanical": { ... },
        "business": { ... },
        "modelParams": { ... },
        "factors": { ... }
      }
    }
  },
  "outputs": {}
}
```

## TAS/data/resistors.ndjson Structure

Resistors in TAS/data/ use the `"resistor"` wrapper:

```json
{
  "resistor": {
    "manufacturerInfo": {
      "datasheetInfo": {
        "part": {
          "partNumber": "WSK25125L000FEA",
          "series": "WSK2512",
          "technology": "shunt",
          "case": "2512",
          "matchcodeDescription": "5mOhm 1% 1W Current Sense"
        },
        "electrical": {
          "resistance": { "nominal": 0.005 },
          "tolerance": 0.01,
          "powerRating": 1.0
        }
      },
      "datasheetUrl": "https://...",
      "manufacturer": "Vishay"
    }
  }
}
```

## Technology Enum

- `"thinFilm"` -- Precision, low noise, tight tolerance (0.1-1%)
- `"thickFilm"` -- General purpose, wider tolerance (1-5%)
- `"wirewound"` -- High power, precision, inductive
- `"carbonComposition"` -- Pulse handling, vintage
- `"metalOxide"` -- High voltage, flame proof
- `"foil"` -- Ultra-precision (0.01%), lowest TCR
- `"shunt"` -- Current sensing, very low resistance (< 1 Ohm)

## Electrical Fields

```json
"electrical": {
  "resistance": {
    "nominal": 0.005,       // Ohm -- resistance value
    "minimum": 0.00495,     // Ohm -- min with tolerance (nullable)
    "maximum": 0.00505      // Ohm -- max with tolerance (nullable)
  },
  "tolerance": 0.01,        // ratio -- 0.01 = 1% (NOT percent)
  "temperatureCoefficient": 50,  // ppm/K -- TCR (nullable)
  "powerRating": 1.0,       // W -- max continuous power
  "powerRatingTemperature": 70,  // C -- temp for power rating (nullable)
  "maxVoltage": 50,         // V -- max working voltage (nullable)
  "maxOverloadVoltage": 100, // V -- max overload voltage (nullable)
  "insulationResistance": 1e9,  // Ohm -- (nullable)
  "noiseIndex": -30          // dB -- current noise index (nullable)
}
```

## SPICE Model Parameters

```json
"modelParams": {
  "r": 0.005,          // Ohm -- nominal resistance
  "tcr1": 50e-6,       // 1/K -- first-order temperature coefficient
  "tcr2": 0            // 1/K^2 -- second-order coefficient
}
```

## Power Derating Factors

```json
"factors": {
  "powerDeratingTemperature": {
    "xData": [25, 70, 105, 155],   // C -- temperature points
    "yData": [1.0, 1.0, 0.5, 0.0] // ratio -- power multiplier
  }
}
```

## Physics Sanity Checks

| Check | Rule | Violation Means |
|-------|------|-----------------|
| Resistance | Must be > 0 (NEVER zero) | CRITICAL: zero-ohm resistors are jumpers, not resistors |
| Tolerance | 0.0001 to 0.2 typical | Wrong units (stored as percent?) |
| Power rating | 0.05W to 50W typical | Wrong units or exotic part |
| TCR | 1-200 ppm/K typical | Check if stored as ratio instead of ppm |
| Shunt resistance | 0.5 mOhm to 1 Ohm | Below 0.5 mOhm is unusual |

## Common Mistakes

1. **Zero resistance** -- CRITICAL error. A resistor with R=0 is a jumper, not a valid resistor entry
2. **Tolerance as percentage** -- Must be stored as ratio (0.01 = 1%, NOT 1 = 1%)
3. **Missing manufacturer** -- Many bulk-imported resistors lack manufacturer identification
4. **Power rating without temperature** -- Should include `powerRatingTemperature` to know the derating reference
