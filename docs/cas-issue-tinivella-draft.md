# Draft CAS GitHub issue — Tinivella feedback

Target repo: `OpenConverters/CAS`
Filed by: (user to post; this is a local draft for review)
Source: WhatsApp conversation with Tinivella (May 2026) + linked article
https://passive-components.eu/esr-of-capacitors-mechanisms-measurements-and-impact-to-applications/

---

## Title

Capacitor schema gaps: no polarization, scalar ESR/DF hide frequency dependence, free-form `technology` field

## Body

`CAS/schemas/capacitor.json` currently models a real capacitor with enough
fidelity for steady-state, in-band power-electronics use, but three
omissions make it unsafe to use the schema directly for selection logic
across the full chemistry range (especially MLCC vs aluminum vs film):

### A. No `polarized` flag

Aluminum electrolytic, tantalum, and tantalum-polymer capacitors are
polarized; ceramic, film, and most polymer-aluminum hybrids are not. The
schema today has no way to express this — `part.technology` is the only
hint, and it's a free-form string (see C below). A consumer of CAS data
cannot reliably reject a wet aluminum cap for a position that sees AC
swing.

**Proposed change:** add `electrical.polarized: boolean | null` (nullable
because legacy rows can't all be backfilled day-1). Librarian backfills
from `part.technology` heuristics where possible.

### B. ESR is a scalar + single frequency — wrong for MLCCs at line frequency

`electrical.esr` is a single number, paired with `electrical.esrFrequency`
(the one measurement point). For aluminum electrolytics with a relatively
flat ESR-vs-frequency curve in the 10 kHz–100 kHz region, this is fine.

For MLCCs (X7R, X5R), it is dangerously wrong: ESR at 100 kHz can be
~10 mΩ while ESR at 50 Hz can be >1 Ω — see Figure 4 of the linked
article. Selecting an X7R cap for a 50 Hz mains-bypass duty by its
100 kHz ESR datasheet number gives a loss estimate two orders of
magnitude too optimistic, with corresponding heating.

The schema already has `rippleCurrentFrequencyPoints` (curve, `xData`/
`yData`) as the right pattern. Mirror it for ESR.

**Proposed change:** add `electrical.esrPoints` as a curve
(`$ref: https://psma.com/peas/utils.json#/$defs/curve`) — `xData` =
frequency (Hz), `yData` = ESR (Ω). Keep the scalar `esr` / `esrFrequency`
fields (legacy, single-point datasheet value) but document that selection
logic must prefer `esrPoints` when present.

### C. Dissipation factor — same scalar-vs-curve bug as ESR

`electrical.dissipationFactor` + `electrical.dissipationFactorFrequency`
has the same single-point limitation. DF varies similarly with frequency
for the same physics reasons (loss is ESR × ωC). For consistency:

**Proposed change:** add `electrical.dissipationFactorPoints` as a curve,
same pattern as B. Keep the scalar legacy fields.

### D. `part.technology` is free-form — should be a closed enum

`part.technology` accepts any string. Sample values currently in TAS:
`"Alum. Electrolytic"`, `"Aluminum Electrolytic"`, `"Ceramic"`, `"MLCC"`,
`"X7R"`, `"C0G"`, etc. — same chemistry written multiple ways, ceramic
class-2 dielectrics conflated with the chemistry family, and no machine-
readable way to ask "is this a polymer hybrid?".

**Proposed change:** replace `part.technology: string` with a closed enum:

- `aluminum-electrolytic-wet`
- `aluminum-electrolytic-polymer`
- `aluminum-hybrid-polymer`         (= "rubycon-zlh"-style)
- `tantalum-wet`
- `tantalum-mno2`
- `tantalum-polymer`
- `niobium-oxide`
- `ceramic-class-1`                 (C0G/NP0 — temperature-stable)
- `ceramic-class-2`                 (X7R/X5R/Y5V — high-K, voltage- and
                                     temperature-dependent)
- `ceramic-class-3`                 (Y5U etc.)
- `film-polypropylene`              (MKP, PP)
- `film-polyester`                  (MKT, PET)
- `film-polyphenylene-sulfide`      (MKI/PPS)
- `film-paper`
- `mica`
- `supercapacitor-edlc`
- `supercapacitor-hybrid`
- `vacuum`

Add a sibling `part.dielectricCode: string | null` for the standard
EIA/MIL code (`"X7R"`, `"C0G"`, `"NP0"`, `"Y5V"`, etc.) — applies to
ceramic-class-1/2/3 only.

D is the most invasive change because it requires the librarian to
backfill 22,708 existing capacitor rows. A/B/C are additive (nullable
fields, no migration needed).

---

## Scope / migration plan

Acceptance criteria for each item:

1. **A — `polarized` flag**
   - Add to `electrical.properties` as `{"type": ["boolean", "null"]}`.
   - Not in `electrical.required`.
   - Librarian backfill: derive from `part.technology` substring match
     (electrolytic/tantalum/polymer-tantalum → `true`; ceramic/film/mica
     → `false`; otherwise `null`).

2. **B — `esrPoints` curve**
   - Add to `electrical.properties` as
     `{"$ref": "https://psma.com/peas/utils.json#/$defs/curve"}`.
   - Not in `electrical.required`.
   - Librarian backfill: where the datasheet provides an
     impedance-vs-frequency or ESR-vs-frequency plot, digitize 6–10
     points spanning the useful band.

3. **C — `dissipationFactorPoints` curve**
   - Same shape as B.

4. **D — `technology` enum + `dielectricCode`**
   - Replace `technology` schema with `{"enum": [...18 values...]}`.
   - Add `dielectricCode: {"type": ["string", "null"]}`.
   - **Migration: required, atomic.** Librarian builds a mapping table
     from current free-form values to the enum, dry-runs across all
     22,708 rows, hand-reviews unmapped rows, then commits.
   - Schema bump goes in the same commit as the data migration.

Items A/B/C can land independently in any order. Item D should be the
last and gated on the librarian migration script being ready.

---

## Why this matters for downstream consumers

Heaviside's component selector currently has to guess polarization from
the technology string (lossy regex) and treats ESR as flat-vs-frequency,
which produces wrong loss estimates for MLCC bypass / film snubber
selection. With this schema, selection becomes a typed query instead of a
heuristic.

---

## Out of scope for this issue

- Voltage-coefficient curve (MLCC DC-bias derating) — separate issue, same
  curve-shape pattern.
- Temperature coefficient of capacitance for ceramic class-2 — already
  partially covered by `thermal.tcc`; revisit if A/B/C/D land cleanly.
- Lifetime model (Arrhenius for electrolytics) — separate issue.
