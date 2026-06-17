# Cross-Reference pipeline

Re-source a converter's BOM to a target manufacturer, ranking substitutes
against **real simulated stress** and explaining every decision per parameter.

## Inputs → pipeline

```
 ENTRY                                    SOURCE OF THE BOM
 POST /jobs/crossref/from-bom   ─┐
 POST /jobs/crossref            ─┼─►  run_crossref_pipeline(bom)   BOM is given
 POST /jobs/crossref/from-pdf   ─┐│
 POST /jobs/crossref/from-url   ─┴┴►  run_crossref_with_cre(...)   extract it (RE front)
```

A **bare BOM** lands directly in the CR core. A **PDF/URL** first goes through the
**RE (Reverse-Engineering) front**, which extracts and *simulates* the reference
so the cross-reference can rank against actual per-component V/I stress.

```
RE FRONT (PDF / URL only)                 CR CORE (every path)
 url_fetch (httpx → stealth Chromium)      Prefetch TAS candidates
 Extract reference document                Librarian (source missing parts)
 Spec extract  ── specs + full BOM ◄─┐     Pre-classify
 Reverse-engineer (reuses spec BOM)  │R+N  Cross-reference (LLM)
 Verify MPNs                         │     Guardrails (V / I / package physics)
 Extract RDS(on) (IC datasheet)      │     Score
 Extract datasheet claims            │     Otto (challenge no_substitute)
 Testbench (MKF SPICE simulation) ───┘     In-kind rescue (deterministic floor)
 RE→CR stress bridge ──────────────►       Review: Ray + Nicola ──► correct ──┐
                                           Learn  ◄─────────────────────(re-review)┘
```

`spec_extract` and `reverse_engineer` are **merged**: spec_extract emits the full
BOM and reverse_engineer reuses it — one large-context LLM call instead of two
(≈ half the calls/tokens/time per design), falling back to its own call only if
the BOM comes back empty.

## Ingestion robustness

- **LLM column-mapper** runs on *every* upload (best-effort): it names which
  column is each canonical field — MPN, ref designator, manufacturer, value,
  category — catching non-standard headers (`MFG_PN`, `LOCATION` = ref-des) the
  deterministic alias table doesn't know. It only *names columns*; values always
  come verbatim from real cells. If the LLM is unavailable, deterministic
  aliasing alone parses.
- **Normalization** fills a missing `ref_des` from `location`/`designator`/`item#`
  (or a synthetic unique id — a missing designator can never collapse rows), and
  infers `component_type` from the description when there is no category column.

## Why each status

- **exact** — same MPN, already from the target manufacturer.
- **recommended** — meets or exceeds every constraint.
- **partial** — meets the critical constraints with a flagged minor gap
  (capacitance > 2×, package one size up, non-E96 resistor rounded, …).
- **no_substitute** — nothing qualifying in the target catalogue.
- **keep_original** — already the target manufacturer, or not fitted.

Each component also carries a **deterministic `match_detail`**: per-parameter
(value / voltage / package) original → substitute with a verdict
(`exact` / `exceeds` / `lower` / `differs` / `same`) plus a one-line *why*,
computed from the data — not the LLM's prose. Surfaced in the Jobs result view
(expand a row) and in the PDF report.

## Review-and-retry

The high-risk LLM stages (spec_extract, reverse_engineer, cross-referencer)
are gated by `heaviside.stages.reviewed_stage.review_and_retry`: **produce → Ray
+ Nicola judge → if rejected, re-run with their objections fed back**, bounded,
surfacing unresolved objections (never a silent pass). The deterministic
**in-kind rescue** is a floor under the stochastic LLM stages: it promotes any
prefetched candidate that *provably* meets the in-kind criteria, fetching from
TAS on demand if prefetch missed the row.

## Output

`CrossRefOutcome` → per-component status + `match_detail` + guardrail fires +
coverage. The same dict renders the Jobs result table and the PDF report
(`GET /jobs/{id}/report.pdf`).
