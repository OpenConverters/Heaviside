# SAS (Semiconductor Agnostic Structure) - Schema Reference

SAS defines the schema for all semiconductor components: MOSFETs, diodes, IGBTs, BJTs, and JFETs.

## Schema Locations

- `/home/alf/OpenConverters/SAS/schemas/SAS.json` -- top-level envelope
- `/home/alf/OpenConverters/SAS/schemas/semiconductor.json` -- component detail

## v2 Structure (TAS/data/)

MOSFETs in `TAS/data/mosfets.ndjson`:

```json
{
  "mosfet": {
    "manufacturerInfo": {
      "name": "Infineon",
      "reference": "IPB065N10N3G",
      "status": "production",
      "datasheetUrl": "https://...",
      "datasheetInfo": {
        "part": { ... },
        "electrical": { ... },
        "modelParams": { ... },
        "curves": { ... },
        "thermal": { ... },
        "mechanical": { ... },
        "business": { ... }
      }
    },
    "distributorsInfo": [],
    "substitutesInfo": []
  }
}
```

Diodes in `TAS/data/diodes.ndjson`:
```json
{
  "diode": { "manufacturerInfo": { ... } },
  "distributorsInfo": []
}
```

IGBTs in `TAS/data/igbts.ndjson`:
```json
{
  "igbt": { "manufacturerInfo": { ... } }
}
```

## Part Identification

```json
"part": {
  "partNumber": "IPB065N10N3G",
  "series": "OptiMOS 3",
  "deviceType": "mosfet",        // mosfet | diode | igbt | bjt | jfet
  "technology": "Si",            // Si | SiC | GaN | GaAs
  "subType": "nChannel",         // mosfet: nChannel | pChannel
                                 // diode: schottky | sicSchottky | ultrafast | standard | zener | tvs
                                 // igbt: (none)
  "case": "TO-263",
  "matchcodeDescription": "N-Channel MOSFET 100V 55A"
}
```

## MOSFET Electrical Fields

```json
"electrical": {
  "drainSourceVoltage": 100,           // V -- max Vds
  "gateSourceVoltageMax": 20,          // V -- max Vgs
  "continuousDrainCurrent": 55,        // A -- Id continuous
  "continuousDrainCurrentAt100C": 39,  // A -- Id at 100C (nullable)
  "pulsedDrainCurrent": 220,           // A -- Id pulsed (nullable)
  "powerDissipation": 136,             // W -- Pd max

  // ON-STATE
  "onResistance": 0.0065,             // Ohm -- Rds(on) *** CORRECT FIELD NAME ***
  "onResistanceVgs": 10,              // V -- Vgs at which Rds(on) is measured
  "onResistanceId": 27.5,             // A -- Id at which Rds(on) is measured

  // GATE
  "gateThresholdVoltage": {
    "minimum": 2.0, "nominal": 3.0, "maximum": 4.0  // V
  },
  "totalGateCharge": 57e-9,           // C -- Qg
  "gateSourceCharge": 14e-9,          // C -- Qgs
  "gateDrainCharge": 19e-9,           // C -- Qgd

  // CAPACITANCES
  "inputCapacitance": 4400e-12,       // F -- Ciss
  "outputCapacitance": 590e-12,       // F -- Coss
  "reverseTransferCapacitance": 44e-12, // F -- Crss
  "capacitanceMeasurementVds": 50,    // V -- Vds at which caps measured
  "outputCharge": 89e-9,              // C -- Qoss

  // SWITCHING
  "turnOnDelay": 15e-9,              // s -- td(on) (nullable)
  "riseTime": 9e-9,                  // s -- tr (nullable)
  "turnOffDelay": 50e-9,             // s -- td(off) (nullable)
  "fallTime": 8e-9,                  // s -- tf (nullable)

  // BODY DIODE
  "bodyDiodeForwardVoltage": 0.9,    // V -- Vsd
  "bodyDiodeContinuousCurrent": 55,  // A -- Is
  "reverseRecoveryTime": 52e-9,      // s -- trr (nullable)
  "reverseRecoveryCharge": 52e-9,    // C -- Qrr (nullable)

  // FIGURE OF MERIT
  "figureOfMerit": 3.7e-10           // Ohm*C -- Rds(on) * Qg (nullable)
}
```

## Diode Electrical Fields

```json
"electrical": {
  "reverseVoltage": 60,              // V -- Vrm max
  "forwardCurrent": 30,              // A -- If(av)
  "surgeCurrent": 350,               // A -- Ifsm (nullable)
  "forwardVoltage": 0.42,            // V -- Vf
  "forwardVoltageAt": 15,            // A -- If at which Vf measured
  "reverseLeakageCurrent": 200e-6,   // A -- Ir
  "reverseRecoveryTime": 35e-9,      // s -- trr (nullable)
  "reverseRecoveryCharge": 44e-9,    // C -- Qrr (nullable)
  "junctionCapacitance": 500e-12,    // F -- Cj (nullable)
  "junctionCapacitanceVr": 4,        // V -- Vr at which Cj measured (nullable)
  "powerDissipation": 100,           // W -- Pd max

  // TVS-specific fields (nullable for non-TVS)
  "clampingVoltage": null,           // V -- Vc
  "breakdownVoltage": {
    "minimum": 58, "nominal": 62, "maximum": 66  // V
  },
  "standoffVoltage": null,           // V -- Vwm
  "peakPulseCurrent": null           // A -- Ipp
}
```

## IGBT Electrical Fields

```json
"electrical": {
  "collectorEmitterVoltage": 1200,         // V -- Vce max
  "gateEmitterVoltageMax": 20,             // V -- Vge max
  "continuousCollectorCurrent": 100,       // A -- Ic
  "collectorEmitterSaturation": 2.0,       // V -- Vce(sat)
  "collectorEmitterSaturationIc": 50,      // A -- Ic at which Vce(sat) measured
  "turnOnEnergy": 12e-3,                   // J -- Eon
  "turnOffEnergy": 15e-3,                  // J -- Eoff
  "totalGateCharge": 350e-9,               // C -- Qg
  "gateThresholdVoltage": {
    "minimum": 4.5, "nominal": 5.8, "maximum": 6.5  // V
  },
  "inputCapacitance": 5200e-12,            // F -- Cies
  "powerDissipation": 300,                 // W -- Pd max
  "shortCircuitTime": 10e-6               // s -- tsc (nullable)
}
```

## TAS/data/ Storage Patterns (v2)

All semiconductor files use v2 per-discriminator wrappers:

**mosfets.ndjson**: `{"mosfet": {"manufacturerInfo": {...}}}`
**igbts.ndjson**: `{"igbt": {"manufacturerInfo": {...}}}`
**diodes.ndjson**: `{"diode": {"manufacturerInfo": {...}}, "distributorsInfo": [...]}`

There is NO `inputs`/`outputs` envelope and NO flat structure.

## Physics Sanity Checks

| Check | Rule | Violation Means |
|-------|------|-----------------|
| MOSFET Rds(on) vs Vds | Higher voltage = higher Rds(on) for same die area | Wrong Rds(on) or Vds |
| MOSFET FOM | Rds(on) * Qg typical range: 1e-12 to 1e-8 Ohm*C | Data error or exceptional part |
| GaN Qrr | Must be 0 or null (no body diode recovery) | Wrong technology classification |
| SiC Qrr | Must be < 1uC (near-zero reverse recovery) | Wrong technology or value |
| Body diode Vf | Si: 0.7-1.5V, SiC: 2.5-4.5V, GaN: 1.5-2.5V | Wrong technology or units |
| Diode Vf | Schottky: 0.2-0.8V, Si: 0.8-1.5V, SiC: 1.0-1.8V | Wrong subType or units |
| IGBT Vce(sat) | Typical 1.2-3.5V | Wrong units or wrong device |
| p-channel Vds | May use negative convention -- valid, not an error | -- |
| Cascode GaN Vgs | 18V is valid (Si MOSFET cascode gate) | -- |

## SPICE Model Parameters (modelParams)

```json
"modelParams": {
  "vto": 3.0,      // V -- threshold voltage
  "kp": 50,        // A/V^2 -- transconductance
  "lambda": 0.01,  // 1/V -- channel-length modulation
  "is": 1e-14,     // A -- body diode saturation current
  "n": 1.2,        // -- body diode ideality factor
  "rs": 0.01,      // Ohm -- source resistance
  "rd": 0.001,     // Ohm -- drain resistance
  "cbd": 590e-12,  // F -- drain-body capacitance
  "cgs": 4400e-12  // F -- gate-source capacitance
}
```

## Common Mistakes

1. **Using `onStateDrainSourceResistance` instead of `onResistance`** -- the correct field is `onResistance`
2. **Using `semiconductor` wrapper** -- v2 uses `mosfet`, `diode`, or `igbt` discriminators
3. **Adding `inputs`/`outputs` envelope to component files** -- v2 TAS/data does NOT have this
4. **Flat structure for diodes** -- v2 wraps diodes in `{"diode": {...}}`
5. **Missing test conditions** -- `onResistanceVgs` and `onResistanceId` should accompany `onResistance`
6. **Wrong units for charge** -- Qg/Qgs/Qgd/Qrr are in Coulombs (C), not nC
