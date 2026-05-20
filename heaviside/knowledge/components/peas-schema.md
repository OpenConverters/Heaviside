# PEAS (Power Electronics Agnostic Structure) - Base Schema

PEAS is the universal container schema for all electronic components in the OpenConverters ecosystem. Every component document is a valid PEAS document that contains exactly ONE component type.

## Schema Location

`/home/alf/Power-Supply-Manufacturers-Association/PEAS/schemas/peas.json`

## Top-Level Envelope

```json
{
  "inputs": { "designRequirements": {} },
  "<component_type>": { ... },
  "outputs": {}
}
```

Where `<component_type>` is exactly ONE of:
- `"mosfet"` -- MOSFETs (SAS)
- `"diode"` -- Diodes (SAS)
- `"igbt"` -- IGBTs (SAS)
- `"capacitor"` -- All capacitor types (CAS)
- `"resistor"` -- All resistor types (RAS)
- `"magnetic"` -- Inductors, transformers, chokes, ferrite beads (MAS)

## Discriminator Pattern

PEAS uses a `oneOf` discriminator: a valid document contains exactly one component type key. The presence of `"mosfet"`, `"diode"`, `"igbt"`, `"capacitor"`, `"resistor"`, or `"magnetic"` determines which sub-schema validates the document.

## Inputs / Outputs

- `inputs`: Design context (optional). May contain `designRequirements` with target specs the component was selected for.
- `outputs`: Computed results (optional). May contain simulation results, loss calculations, thermal estimates.

These are part of the standard PEAS envelope. **They are NOT errors.** Every component type can have them.

## Hierarchy

```
PEAS (base)
├── SAS (semiconductor) -- MOSFETs (mosfet), diodes (diode), IGBTs (igbt)
├── CAS (capacitor)     -- MLCC, electrolytic, film, polymer
├── RAS (resistor)      -- thin film, thick film, shunt, wirewound
└── MAS (magnetic)      -- inductors, transformers, chokes, ferrite beads
```

## TAS (Topology Agnostic Structure)

TAS extends PEAS for finished converter designs. A TAS document contains:

```json
{
  "inputs": { /* converter requirements */ },
  "components": {
    "componentList": [
      {
        "name": "Q1",
        "role": "highSideSwitch",
        "quantity": 1,
        "data": { /* inline PEAS document or path reference */ }
      }
    ],
    "netlist": { "nodes": [...], "connections": [...] }
  },
  "outputs": [ /* simulation/analysis results */ ]
}
```

## TAS/data/ NDJSON Files

The production component database uses NDJSON format (one JSON document per line, SI units).

**All files use v2 per-discriminator wrappers — NO flat structures, NO `inputs`/`outputs` envelope:**

| File | Top-Level Keys |
|------|----------------|
| `mosfets.ndjson` | `mosfet` |
| `diodes.ndjson` | `diode`, `distributorsInfo` |
| `igbts.ndjson` | `igbt` |
| `capacitors.ndjson` | `capacitor` |
| `resistors.ndjson` | `resistor` |
| `magnetics.ndjson` | `magnetic` |
| `converters.ndjson` | `inputs`, `topology` (+ optional `outputs`, `simulation`) |

## SI Units

ALL values in the database MUST be in SI base units:
- Voltage: V (not mV, kV)
- Current: A (not mA)
- Resistance: Ohm (not mOhm, kOhm)
- Capacitance: F (not uF, nF, pF)
- Inductance: H (not uH, mH)
- Frequency: Hz (not MHz, kHz)
- Time: s (not us, ns)
- Charge: C (not nC, uC)
- Energy: J (not mJ, uJ)
- Temperature: C (degrees Celsius)
- Dimensions: m (not mm)

## Common Mistakes

1. **Double unit conversion**: Converting MHz->Hz twice, resulting in values 1e6 too high
2. **Mixing envelope styles**: Using flat structure for MOSFETs (should have `inputs`/`semiconductor`/`outputs`)
3. **Using wrong field names**: Each device type has its own electrical field names (see SAS/CAS/RAS/MAS schemas)
4. **Skeleton entries in production**: Entries with `dataCompleteness: "skeleton"` or NULL electrical values must go to `quarantine.ndjson`, never production files
5. **Forgetting SI units**: Storing capacitance in uF instead of F, or resistance in mOhm instead of Ohm
