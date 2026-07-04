# Crossref v2 — Scoring, Dimensions, LLM Rules, and the FAE Adversary Loop

Status: PROPOSAL (2026-07-04) — awaiting approval.
Trigger: IHLP1616ABER1R5M11 (1.5 µH) crossed to 744383560R33 (330 nH) as "partial",
with the note "deterministic in-kind rescue: … meets voltage/value/chemistry criteria".
None of those three criteria actually ran.

---

## Part 1 — Diagnosis: what is broken today

### 1.1 The screenshot bug (root cause, verified in code)

`_best_inkind_candidate` (crossref_pipeline.py:4220-4222) reads the original's value from
`comp["value_si"]` / `comp["original_value"]` — keys that do not exist on normalized BOM
rows (`_normalize_bom` stores it under `comp["value"]`). Consequences, in order:

1. `orig_vsi = None` → the entire value-window check (4241) is skipped, `within_tight=False`.
2. Voltage floor (4231) is skipped too — inductors carry no voltage rating.
3. Chemistry check (4234) is capacitor-only.
4. The function returns the **first candidate of an unsorted list** — `_rank_candidates`
   (1724-1725) also returns `all_candidates[:max_results]` **unsorted** when it can't parse
   the original's value.
5. The note claims "meets voltage/value/chemistry criteria" — vacuously true: all three
   checks were no-ops. This violates the house no-silent-fallbacks rule twice over.

### 1.2 Structural gaps (independent of the bug)

| # | Gap | Where |
|---|-----|-------|
| G1 | **Primary value (R/L/C/Z/f) is not in `PARAM_SPECS` for any category.** It is compared only in the descriptive "why" text (`build_match_detail`, 3905-3990) and never gates the verdict. A 4.5× L error cannot demote a row. | param_check.py:129-360 |
| G2 | **No proximity scoring in the verdict** — param_check is binary PASS/WARN/FAIL. 12.4 A Isat vs a 3.25 A original "passes" identically to 3.5 A. No over-dimensioning penalty anywhere in the verdict path (only a mild over-cap ranking nudge for capacitors, 1762). | param_check.py:387-412 |
| G3 | **No compensation model.** A slightly-worse critical param (Isat −5 %) is a hard WARN/FAIL regardless of how much better the part is elsewhere; conversely nothing rewards closeness. | param_check.py |
| G4 | **MOSFET/diode candidates are ranked unsorted** unless sim-stress penalties apply (`target_val is None` path). No parametric distance, no FOM. | crossref_pipeline.py:1724 |
| G5 | **Case→dimensions fallback covers only 11 imperial EIA chip codes** (01005…2225). "4020" (WE-MAPI 4.0×2.0), molded tantalum A/B/C/D, aluminum-can "16x25", SOT/TO/DPAK, metric codes — all map to `None` → footprint silently "not enforced". | crossref_pipeline.py:790-831 |
| G6 | **Ingestion never populates L/W/H** on the generic path (capacitors explicitly emptied, convert.py:1627-1632); the datasheet extractor *stops reading* at "mechanical/package/outline" sections (extract.py:84-92, SECTION_TERMINATORS). Dimensions exist only where a one-off vendor script backfilled them. | librarian |
| G7 | **Metric/imperial case-code ambiguity unhandled** (0603-metric = 0201-imperial). | crossref_pipeline.py:803 |
| G8 | **Tests reward quantity, not quality.** The only end-to-end LLM test scores `substituted/scope` count vs Proteus (test_cr_vs_proteus_llm.py:559-663); a wrong-value "partial" counts as a win. The 30 Proteus golden BOMs' known picks are never used as an answer key. No test asserts "original X → good sub Y, bad sub Z rejected" through the assembled pipeline. | tests/ |
| G9 | **Rescue notes and match detail can assert checks that never ran** ("n/a" rows, vacuous "meets criteria"). Verdict text is not evidence-labeled. | crossref_pipeline.py:4366 |

### 1.3 What is already good (keep)

- The four-direction `ParamSpec` engine exists and is declarative — v2 extends it, no rewrite.
- Identity-gated categories (connector/analog/timeBase) with hard exact gates + the
  **connector mating check** — no incumbent tool closes the mating loop; we already do.
- MLCC DC-bias effective-capacitance model (`mlcc_bias_param`) — industry best practice
  (compare at operating point) already partially in place.
- Footprint penalty asymmetry (fits ≫ one-size-up ≫ oversize) and the caveat stage.
- Chemistry/dielectric family gates for capacitors.
- The comparator unit tests are genuinely regression-driven; they just cover mechanisms,
  not the assembled pipeline.

---

## Part 2 — Industry benchmark: what the best tools do, and our open lanes

From a verified survey of SiliconExpert/Z2Data/Accuris, Digi-Key, TI/Nexperia/Infineon,
TDK/Murata/KEMET/Vishay, Bourns/Coilcraft, TE/Molex/Samtec, Abracon/ECS:

1. **Two-axis grading is the industry reference model** (SiliconExpert A/B/C/D/F ×
   upgrade/downgrade; TI's drop-in / pin-for-pin / same-function / similar-function ladder):
   *(form-fit tier)* × *(parametric direction)*. Package/pinout is a **gate**, not a weighted
   term — no parametric score can promote a wrong-footprint part to "drop-in".
2. **"Equivalent" ≠ "upgrade"** — SE grades A vs A/U separately; Arrow caps any downgrade at
   grade C. Over-spec'd is explicitly not the same grade as exact.
3. **Direction per parameter is MIL-STD-280A one-way substitutability**: ratings ≥,
   parasitics ≤, ranges ⊇, tolerance ⊆, identity for class attributes (dielectric class,
   rectifier class, shielding, AEC-Q, anti-surge, current-sense construction).
4. **Known direction-breakers the incumbents get wrong**: LDO output-cap ESR is a *window*
   (too-low oscillates — TI SLVA115: ~0.1–20 Ω); Rds(on)/Qg and Vf/leakage are trade-off
   pairs (FOM = Rds×Qg, Vishay AN605); Isat is definition-dependent (10/20/30 % drop —
   IEC 62024-2) so raw Isat numbers are incomparable across vendors.
5. **Compare at the operating point, not the headline** (MLCC bias, L(I) at Ipk, Rds at the
   actual Vgs, crystal gain margin ≥5× ESR — ST AN2867). REDEXPERT is the strongest
   published version; nobody applies it to cross-referencing.
6. **Evidence provenance is part of the grade** (Digi-Key: Direct / Parametric / MFR-PCN /
   Similar). Label how each cross was derived.
7. **Nobody publishes a numeric over-dimensioning penalty or closest-fit ranking** — open
   lane #1. **Nobody encodes courtyard+height envelope fit** — open lane #2. **No connector
   tool closes the mating loop** — open lane #3 (we already have it).
8. Every credible tool ships the side-by-side parametric diff and an explicit "what we did
   NOT check" statement (Vishay's tantalum cross doc is the honesty model).

Target: two-axis grading + operating-point comparison + closest-fit ranking with
diminishing-returns over-dimensioning penalty + honest evidence labels = better than
anything shipping.

---

## Part 3 — The v2 scoring engine

### 3.1 Four comparison modes (user requirement, formalized)

Every parameter spec gets one of four modes — the existing enum, completed and made
score-producing instead of only verdict-producing:

| Mode | Semantics | Examples |
|------|-----------|----------|
| `EXACT` | equal within tolerance window; mismatch is a gate-fail or heavy penalty | dielectric class, positions, pitch, crystal frequency, package-for-drop-in |
| `HIGHER_BETTER` | s ≥ o required; small deficit = soft penalty; large surplus = diminishing-returns penalty | V rating, Isat, Irms, power, If, Vrrm, drive level, SRF |
| `LOWER_BETTER` | s ≤ o required; mirrored | DCR, ESR (usually), Rds(on), Qg, Qrr, trr, TCR, Vf, IQ |
| `RANGE` | must lie in [lo, hi]; zero penalty inside, steep outside | LDO ESR window, L window when a converter design gives one, supply range ⊇ |

### 3.2 Per-parameter utility curve (proximity + diminishing returns)

Let `x = ln(s/o)` (log-ratio: symmetric, unit-free, compresses big ratios naturally).
Each param contributes a penalty `p = w · f(x)`:

```
HIGHER_BETTER:
  x ≥ 0 (surplus):  f = k_over · min(x, x_cap)         # gentle, log-scaled, capped
  x < 0 (deficit):  f = k_def · (e^(-x·s_def) − 1)      # steep exponential
                    hard-fail gate when s < g·o         # g per param, e.g. 0.8

LOWER_BETTER:  mirror (swap sign of x)

EXACT:   f = 0 inside tol window; gate-fail outside (numeric: |x| ≤ ln(1+tol))

RANGE:   f = 0 inside [lo,hi]; k_def·(distance ratio) outside; gate at hard bounds
```

Properties this delivers (all user requirements):

- **Closest value wins with no design context**: the primary value uses `EXACT`-with-window
  or a near-symmetric curve, so with no converter reference the ranker maximizes proximity
  — a 1.5 µH original prefers 1.5 µH > 1.2/1.8 µH > 1.0/2.2 µH > everything else. With a
  converter design reference (CRE stress path), the primary value spec switches to the
  design's `RANGE` (e.g. L ∈ [L_min(ripple), L_max(dynamics)]).
- **Over-dimensioning is penalized with diminishing returns**: the `min(x, x_cap)` log term
  means 2× Isat costs a little, 4× a bit more, 10× barely more than 4× — but a 10×
  oversized part can never beat an otherwise-equal 1.2× part. Physically motivated: gross
  over-spec correlates with size/cost/parasitic penalties that usually show up in other
  params anyway; the term breaks ties toward right-sizing.
- **Near-miss soft-fail, compensable**: Isat 3.1 A vs 3.25 A required lands in the deficit
  exponential (bounded penalty, verdict cap "partial") instead of a rejection — acceptable
  if the part is much better elsewhere. Beyond the gate (e.g. < 0.8×), it is a hard fail,
  never compensable. Gates encode physics (saturation, dielectric class, voltage floor,
  mating); penalties encode engineering preference.

### 3.3 Primary value becomes a first-class ParamSpec (fixes G1)

Add to `PARAM_SPECS` per category with mode + window resolved per context:

| Category | Primary spec (no design context) | With design context |
|---|---|---|
| resistor | R exact, window = original tolerance (default ±1 %); jumper special-case | unchanged |
| capacitor | C in [0.9×, 2×], proximity-scored; effective-C at bias for MLCC | C ≥ C_min from ripple/holdup |
| magnetic (L) | L in [0.8×, 1.25×], proximity-scored, tight = ±10 % | L ∈ design range at Ipk |
| chipBead | Z@100 MHz ≥, proximity | at frequency of interest |
| mosfet | no single primary — FOM distance (see 3.5) | stress-derived |
| diode | class identity + Vf/If distance | stress-derived |
| timeBase | f EXACT (1e-4) — already present | unchanged |

The verdict then gates on the primary spec like any other: value outside window ⇒ status
cap `partial` (in-family, flagged) or `no_substitute` (outside hard band). The 330 nH part
becomes impossible to emit as anything but no_substitute regardless of which stage
proposed it.

### 3.4 Two-axis grade (form-fit tier × parametric direction)

Keep the four external statuses (schema/UI stable) but derive them from an internal grade:

```
fit_tier:   DROP_IN (env fits + land-pattern class match) | SIZE_UP | DIFFERENT_FORM | UNKNOWN_FORM
param_dir:  EQUIV (all |x| small) | UPGRADE (all pass, some better) |
            NEAR_MISS (bounded deficits) | MAJOR_DELTA
evidence:   catalog-verified | datasheet-derived | vendor-declared | UNVERIFIED

exact       = DROP_IN × EQUIV, primary value within tight window, all evidence verified
recommended = DROP_IN × (EQUIV|UPGRADE)
partial     = SIZE_UP, or NEAR_MISS, or UNKNOWN_FORM/UNVERIFIED on any gating param
no_substitute = gate failure or nothing survives
```

`UNKNOWN` is never silently "pass": unknown dims or an unverifiable gating param caps the
grade at `partial` and is named in the why-line ("footprint unverified: no dimensions for
case 4020"). Match-detail must only cite checks that actually ran (fixes G9).

### 3.5 Category-specific upgrades

- **MOSFETs (fixes G4)**: rank by weighted distance on {Vds ≥, Rds(on) @ matched Vgs ≤,
  FOM = Rds×Qg ≤, Vgs(th) class (logic-level vs standard = EXACT class gate), Qrr for
  bridge/sync-rect contexts, package}. Never unsorted.
- **Diodes**: rectifier class = EXACT gate (Schottky/ultrafast/standard); Vf ≤ with the
  leakage trade-off flagged when Vf is much lower (thermal-runaway note for Schottky).
- **Capacitors**: ESR direction becomes `RANGE` when the row context says LDO-output
  (from CRE/BOM role detection), else LOWER_BETTER — the direction-breaker incumbents miss.
- **Magnetics**: Isat comparison normalized by definition where the DB records it
  (%-drop definition per IEC 62024-2); shielded→unshielded = gate; prefer L(I)-curve
  comparison at Ipk when CRE stress exists.
- **Crystals**: ESR ≤ with gain-margin note; CL EXACT (already); drive level ≥.

### 3.6 Immediate bug fixes (Phase 0 — independent of the scoring rework)

1. `_best_inkind_candidate`: read `comp["value"]`; **if the original's primary value cannot
   be parsed, refuse to rescue** (return None) — no-fallbacks rule. Note text must list
   only checks that ran.
2. `_rank_candidates`: unparseable original value ⇒ flag the row ("ranking not
   value-based") instead of silently returning unsorted; rescue and LLM stages see the flag.
3. Add primary-value specs to `PARAM_SPECS` (3.3) so `_stage_param_check` demotes on value.
4. Extend `_stage_param_check` demotion: FAIL on primary value ⇒ cap at partial with
   "deviates on value" as a *verdict reason*, not just prose; hard-band ⇒ no_substitute.
5. Kill every remaining vacuous-evidence note path (audit all f-string notes for claims
   about checks that can no-op).

---

## Part 4 — Package/case → dimensions everywhere

Priority order (form-fit is the industry gate; we currently can't evaluate it for most parts):

1. **Case-code dimension tables** (crossref-side, deterministic):
   - Full imperial EIA chip set + **metric codes with explicit disambiguation** (accept
     "0603M"/"1608 metric"; when bare and ambiguous, use category+value priors and flag
     `UNKNOWN_FORM` rather than guess wrong).
   - Molded tantalum A/B/C/D/E/V cases; aluminum-can `DxL` pattern parse ("16x25" → ⌀16×25);
     Würth/vendor magnetic codes ("4020" → 4.0×2.0×h?); SOT-23/SC-70/SOT-223/DPAK/D2PAK/
     TO-220/TO-247/SOIC/QFN body dims (IPC-7351 derived) with **alias normalization**
     (SOT-23=SC-59, TO-252=DPAK…).
   - Height stays unknown unless recorded — table gives L×W only; unknown height is an
     explicit caveat when the original is height-constrained.
2. **Librarian: extract dimensions from datasheets** (user requirement). Remove
   "mechanical/package/outline" from `SECTION_TERMINATORS`; add a dimension-extraction pass
   (regex patterns for L×W×H tables + LLM fallback on the outline-drawing page). Applies on
   ingest and on-demand: when crossref meets a part with no dims and no mappable case, it
   requests a librarian dimension fetch (same flow as electrical enrichment).
3. **Ingestion**: populate `mechanical.length/width/height` from distributor attributes
   (Digi-Key "Size / Dimension" + "Height") on the generic path instead of dropping them.
4. **Envelope-fit grading** (open lane #2): substitute must fit within original's
   L×W (2 % slack, orientation-agnostic — exists) **and height when known**; chip passives
   additionally require case-code equality for `DROP_IN` (downward size = weak joints, not
   a penalty — a fail, per land-pattern asymmetry).

---

## Part 5 — LLM rule updates (prompts)

- `cross-referencer.md`: state the four modes explicitly per category; "prefer the closest
  primary value, not merely in-window"; over-dimensioning guidance ("a 4× Isat part is a
  worse pick than a 1.5× part, all else equal"); require the model to output which criteria
  it *verified* vs *assumed* (feeds evidence labels).
- `otto.md`: remove "±20 % is often acceptable" blanket for inductance — replace with the
  same window+proximity rule; otto may argue *within* the deterministic gates, never across
  them (an otto overturn must still pass param_check to take effect — verify this is
  enforced, not conventioned).
- `ray.md`/`nicola.md`: give reviewers the numeric score breakdown per row so objections
  cite parameters, not vibes.

---

## Part 6 — Evaluation: tests that test the real thing

### 6.1 Deterministic trap harness (CI, no tokens)

Per category, fixture sets of `original + candidates` where each candidate is a named trap:

- wrong-value in-family (the 330 nH case — must never surface above no_substitute),
- slightly-low Isat (must be partial, not rejected, and must lose to a compliant part),
- massively oversized (must rank below close-fit),
- lower voltage / wrong dielectric / unshielded-for-shielded (gate fails),
- unknown-dims candidate vs known-dims (must be capped partial with named caveat),
- metric/imperial case confusion.

Assert the **assembled deterministic chain** (rank → guardrails → score → rescue →
param_check → match_detail): final status, ranking order, and that the why-line only cites
executed checks. This is the layer that made the screenshot bug possible and it costs zero
tokens to pin down forever.

### 6.2 Golden answer keys (CI, no tokens)

Use the 30 Proteus reference BOMs as an answer key at last: per row, an
`accepted_set` (known-good MPNs ± tight value window) and a `forbidden_set` (known-bad).
Metric = fraction of rows whose pick ∈ accepted and ∉ forbidden — replaces coverage-count
as the tracked number. Curated once, versioned in-repo.

### 6.3 Property tests on the scoring math (CI)

Monotonicity (closer value never scores worse), direction correctness per mode,
diminishing returns (marginal over-dimension penalty decreasing), gate dominance
(no compensation across a gate), symmetry of log-ratio.

### 6.4 LLM-in-loop smoke (in-suite, small, modest cost — per policy)

10–20 rows through the full pipeline (real LLM stages): assert pick ∈ accepted_set,
never ∈ forbidden_set. Catches prompt regressions the deterministic layer can't.

### 6.5 The FAE Adversary Loop (the headline eval — user-specified)

A closed improvement loop where an independent senior-FAE judge tries to shred the tool's
real output, and its findings drive the next development iteration:

```
 [1] Fixture library: N reference designs + BOMs (Proteus set + hand-built traps
     + real customer-style messy BOMs)
        │
 [2] Driver (harness) runs each through the REAL GUI — headless Playwright against
     the running webui: upload BOM → watch job → results page → print-to-PDF
     (webui has no results-PDF export today; print-to-PDF captures exactly what a
     customer sees, why-lines and "n/a"s included. Optional later: a real export
     endpoint, which the harness would then also exercise.)
        │
 [3] Independent judge: a SEPARATE Opus 4.8 agent (model: claude-opus-4-8 — not the
     model that ran the pipeline), zero access to our code or intermediate state.
     Persona prompt: senior power-electronics FAE whose job this CR tool is about to
     replace — maximally motivated to prove it isn't ready. Inputs: the PDF(s) + web
     access for datasheets. Mandate:
       • verify every substitution row like a customer-facing FAE would,
       • pull the actual datasheets for original + substitute when in doubt
         (never judge from memory — house rule),
       • hunt specifically for: value deviations, vacuous/fallback language
         ("n/a", "not enforced", criteria claimed but not evidenced),
         over-dimensioned picks, footprint/height misses, missing parameters,
         wrong direction calls (ESR windows, Qg trade-offs, Isat definitions),
         and anything a competitor FAE would use against us in front of the customer.
     Output: structured findings — {design, ref_des, severity, parameter, claim,
     evidence (datasheet cite), what-the-tool-said vs what-is-true}.
        │
 [4] Main agent (me) ingests findings: dedup vs known issues, reproduce each against
     the pipeline state, classify (scoring bug / data gap / prompt gap / judge wrong),
     and produce a scored run report + concrete improvement plan.
        │
 [5] YOU approve the plan → we implement → rerun the SAME fixture set → findings
     count by severity becomes the regression curve. Loop.
```

Mechanics:

- Runs on demand (a `/fae-eval` style harness script), not CI — it deliberately burns
  tokens on both the pipeline and the judge. Playwright headless always.
- Judge independence is enforced: different model (Opus 4.8), no repo access, sees only
  the artifact + public datasheets. Its incentive prompt is adversarial by design so it
  hunts the fallback language that a cooperative reviewer glosses over — exactly the class
  of failure in the screenshot ("meets criteria" that never ran).
- Judge findings are *claims*, not truth: step [4] reproduces each one before it enters
  the plan (the judge can be wrong; that's also recorded, and repeated judge errors feed
  back into the judge prompt).
- Persistent scorecards: `tests/evals/fae_runs/<date>/{findings.json, report.md, pdfs/}`
  — severity-weighted totals per run give a measurable "is the tool getting better" curve,
  the number the coverage metric never was.

### 6.6 Retire/demote the misleading metrics

`test_cr_vs_proteus_llm.py`'s coverage-count stops being the quality signal (kept only as
a throughput/regression canary). Quality = 6.2 answer-key score + 6.5 FAE severity curve.

---

## Part 7 — Rollout

| Phase | Content | Depends on |
|---|---|---|
| **0** | Bug fixes 3.6 (rescue key + refuse-on-unknown, ranked-unsorted flag, primary value in PARAM_SPECS, honest notes) + trap harness 6.1 covering them | — |
| **1** | Scoring engine v2 (utility curves, gates vs penalties, two-axis grade derivation) + property tests 6.3 | 0 |
| **2** | Dimensions: case tables + ingestion populate + librarian datasheet dimension extraction + envelope fit | parallel to 1 |
| **3** | Prompt updates (Part 5) + LLM smoke 6.4 + golden answer keys 6.2 | 1 |
| **4** | FAE Adversary Loop harness + first baseline run + first improvement plan from its findings | 2,3 (worth a baseline run even before fixes, to anchor the curve) |
| **5** | Operating-point deepening: L(I)@Ipk, Isat definition normalization, ESR windows by role, evidence labels in UI | 1-4 |

Each phase lands as ABT tickets on approval.
