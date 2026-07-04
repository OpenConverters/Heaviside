# FAE Adversary Loop — cross-reference quality eval

An adversarial, closed improvement loop for the cross-reference tool. An
independent senior-FAE judge (running on Opus 4.8, with the *opposite* incentive
to the pipeline) tries to shred the tool's real output; its findings drive the
next development iteration. This is the quality signal the old coverage-count
benchmark never was — that metric rewarded *quantity* of substitutions, which is
exactly the behaviour that let a 330 nH part ship for a 1.5 µH original.

See `docs/crossref_v2_proposal.md` Part 6.5 for the rationale.

## The loop

```
 1. FIXTURES        tests/evals/fae/fixtures/*.csv  (+ manifest.json answer keys)
        │
 2. HARNESS         scripts/fae_eval/run_designs.py
        │           runs each BOM through the REAL server (same endpoints the GUI
        │           calls), captures result.json + report.pdf, and auto-grades
        │           with the deterministic value-integrity checker.
        ▼
    runs/<id>/<design>/{result.json, report.pdf, violations.json}
        │
 3. JUDGE           an INDEPENDENT Opus 4.8 agent per design (judge_prompt.md):
        │           reads report.pdf, pulls datasheets from the web, and returns
        │           structured findings. No repo/DB/code access — judges the
        │           output like a customer would. Adversarial by design.
        ▼
    findings (JSON per design)
        │
 4. SYNTHESIS       the main agent reproduces each finding against the pipeline,
        │           classifies it (scoring bug / data gap / prompt gap / judge
        │           wrong), and writes an improvement plan + severity scorecard.
        ▼
 5. APPROVE → IMPLEMENT → rerun the SAME fixtures.  Severity-weighted findings
    per run = the regression curve. Loop.
```

## Running it

**Harness (deterministic, but burns real LLM tokens in the pipeline):**

```bash
# start a server on a fresh port with the current code (NOT --reload-less prod)
python3 -m uvicorn heaviside.api:app --port 8788 &

# run all fixtures (or --only trap_inductor_1p5uH)
python3 scripts/fae_eval/run_designs.py --base-url http://127.0.0.1:8788
```

Output lands in `tests/evals/fae/runs/<id>/`. `violations.json` is the
zero-cost machine grade — any entry is a certain finding before the judge runs.

**Judge (independent Opus 4.8, adversarial — the token-heavy step):**

The judge is deliberately NOT wired into the harness script, because it must be
*independent* of the pipeline and its model. Two ways to run it:

- **In Claude Code (recommended here):** spawn a subagent per design with
  `model: opus` and web access (Explore / general-purpose), feeding it
  `judge_prompt.md` + the design's `report.pdf` (Read supports PDFs). It returns
  the findings JSON. This is a genuinely separate context/incentive from the
  agent that ran the pipeline.
- **Via the repo's agent runtime:** `call_agent_json("fae-judge", <report text>)`
  — requires Anthropic credentials in the process env and the `fae-judge` prompt
  installed under `heaviside/agents/prompts/` (it is named to avoid the
  review-role model gate on ray/nicola/reviewer). The judge prompt pins
  `model: claude-opus-4-8`.

**Then:** the main agent collects findings, reproduces each, and drafts the
improvement plan for approval. Runs accumulate in `runs/` as the regression
curve.

## Files

| File | Role |
|------|------|
| `fixtures/*.csv` | GUI-uploadable BOMs (real reference designs + value/class traps) |
| `fixtures/manifest.json` | target manufacturer + per-ref invariant answer keys |
| `judge_prompt.md` | the adversarial senior-FAE Opus 4.8 persona + findings schema |
| `../../../scripts/fae_eval/run_designs.py` | the harness (submit → poll → PDF → grade) |
| `../../../heaviside/pipeline/crossref_invariants.py` | deterministic value-integrity checker |
| `runs/<id>/` | per-run artifacts + scorecards (the regression curve) |
