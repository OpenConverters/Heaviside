# Heaviside — Handoff for the next agent

**Date:** 2026-05-26
**Branch:** `main` (all repos)
**Autonomy:** per `AGENTS.md`, commit pre-granted, push requires explicit ask. Use SSH key `hephaestus_om` for `OpenMagnetics/*` pushes — the default `wn00224418_wdp` lacks write access.

Read [`AGENTS.md`](AGENTS.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md) first. Then this file for the live state.

---

## Corpus state (last known)

**13 / 21 PASS** (estimated, after the last set of commits but pre-full-rerun). Run `.venv/bin/python scripts/corpus_run.py` to confirm. To run a subset: `CORPUS_TOPOLOGIES=foo,bar .venv/bin/python scripts/corpus_run.py`.

| Verdict | Count | Topologies |
|---|---|---|
| **PASS** | 13 (est) | buck, boost, cuk, sepic, zeta, four_switch_buck_boost, single_switch_forward, **two_switch_forward**, asymmetric_half_bridge, phase_shifted_full_bridge, dual_active_bridge, vienna, **push_pull** |
| **FAIL** | 3 (est) | active_clamp_forward (efficiency_sanity 0.65; see docs/acf-efficiency-pending.md), flyback (inductor_isat_margin), isolated_buck_boost (efficiency_sanity) |
| **INCOMPLETE** | 1 | cllc (every check UNAVAILABLE — analyst agent not wired) |
| **CRASH** | 4 (est) | clllc, isolated_buck, llc, weinberg |

---

## What's installed vs. what's committed

The PyOpenMagnetics extension currently in `.venv` was built from these MKF/PyMKF heads and **hand-copied** as a `.so` (no wheel install). If you need a clean install, rebuild with `cmake --build` from `/home/alf/OpenMagnetics/PyMKF/build/cp312-cp312-linux_x86_64/` and `cp` the resulting `PyOpenMagnetics.cpython-*.so` into `.venv/lib/python3.12/site-packages/PyOpenMagnetics/`.

**MKF commits this session** (on `main`, *unpushed*):
- `b6d0344f` fix(push_pull): default duty 0.5 → 0.48
- `25b37da2` fix(acf): SW1 RON from cfg, UIC, corrected V_clamp formula
- `21abe9e8` fix(acf): non-overlapping S1/S_clamp PWM + scientific format
- `efbbbd6b` fix(adviser): bail on N consecutive identical filter throws
- `c626aa66` fix(coil-mesher): guard empty-harmonic OOB
- `b58d4f89` fix(coil): skip devirtualization when wind_by_*_turns returns false
- `3c851744` fix(coil): two OOB reads in Coil::equalize_margins
- Plus several `feat(spice-defaults)` and `feat(spice-config)` registry refactors
- ACF deck restructure: canonical topology with Cclamp across primary, S_clamp high-side aux from Vin

**PyMKF commits this session** (on `main`, *unpushed*):
- `3f2001c` fix(dispatch): `dual_active_bridge` alias in generate_ngspice_circuit
- `11881bd` fix(dispatch): DAB alias + Vienna empty-extras

**Heaviside commits this session** (on `main`, *unpushed*):
- `1f1a3fb` fix(extract+test_push_pull): centre-tapped primary aliases + 3-entry turnsRatios
- `6b77359` docs: ACF efficiency_sanity pending note
- `6b5fed4` fix(sim): forward-family iin probe uses i(vin) not i(vpri_sense)
- `8d95f93` fix(corpus/cache/realism): iso-buck no-trim + cache rename race + efficiency surfacing
- `9803fa2` fix(enrichers): harvest each magnetic's L from its own MAS
- `44a9101` fix(psfb): harvest L_out0 inductance from its MAS, delegate isat to MKF
- `1ea8be3` fix(extract): centerTapped secondaries + corpus filter env var

---

## Build state (READ THIS BEFORE TOUCHING MKF)

CMake reconfigure is broken in this build dir (the original uv-temp Python interpreter path is gone). **Don't run `cmake .`** — it'll fail on FetchContent. To incrementally rebuild PyMKF:

```bash
cd /home/alf/OpenMagnetics/PyMKF/build/cp312-cp312-linux_x86_64
rm -f .ninja_lock
ninja -j2 PyOpenMagnetics 2>&1 | tail -5   # use -j2 to avoid OOM; -j4 OOMed last attempt
cp PyOpenMagnetics.cpython-312-x86_64-linux-gnu.so \
   /home/alf/OpenConverters/Heaviside/.venv/lib/python3.12/site-packages/PyOpenMagnetics/
```

**Use Bash with `timeout: 600000` (10 min) for the ninja invocation** — the harness backgrounds short Bash calls and the build gets killed. Foreground with explicit timeout works.

**LibraryContext.cpp**: a NEW MKF file added during this session that the CMake GLOB doesn't pick up without a reconfigure. We worked around by manually compiling it once and patching `build.ninja` to include `LibraryContext.cpp.o` in the link line. **If you do a full clean rebuild, you'll need to either fix the GLOB (use `CONFIGURE_DEPENDS`) or repeat the manual patch.** The current `build.ninja` already has the patch.

**Another agent has been editing MKF too** — specifically `Topology.cpp` (LLC/CLLLC/SRC/IBB/AHB/PSFB/PSHB/Vienna/CMC/DMC config registry refactors) and `MagneticAdviser.cpp` / `CoreAdviser.cpp` (LibraryContext integration). Don't revert their changes — system-reminder messages flagged these as intentional.

---

## Items still pending (priority order)

### 1. **Push the local commits** (10 commits across 3 repos)

Nothing has been pushed yet this session. Confirm with the human before pushing — see CLAUDE.md "actions visible to others".

```bash
cd /home/alf/OpenMagnetics/MKF && git -c core.sshCommand="/usr/bin/ssh -i ~/.ssh/hephaestus_om" push origin main
cd /home/alf/OpenMagnetics/PyMKF && git -c core.sshCommand="/usr/bin/ssh -i ~/.ssh/hephaestus_om" push origin main
cd /home/alf/OpenConverters/Heaviside && git push origin main
```

### 2. **ACF efficiency_sanity** (η = 0.65, needs ≥ 0.70)

Full pending note: [`docs/acf-efficiency-pending.md`](docs/acf-efficiency-pending.md).

TL;DR — the canonical ACF topology rewrite (Cclamp across primary, high-side aux from Vin) works at η = 0.93 when sim is run directly on the deck at the default duty (D=0.45). The remaining gap is that with D=0.45, vout = 10 V instead of the 12 V spec target → pout = 41 W (not 60 W) → η = 41/63 = 0.65. The Heaviside closed-loop driver tries to fix this by iterating duty but doesn't re-stamp Cclamp's `IC=` → ngspice timestep-too-small.

Two fixes, both viable:

**(A) MKF deck-builder uses analytical duty.** Edit `ActiveClampForward.cpp::generate_ngspice_circuit`:
```cpp
double dutyCycleAnalytical = (mainOutputVoltage + diodeVoltageDrop) * n / inputVoltage;
double dutyCycle = std::min(dutyCycleAnalytical, get_maximum_duty_cycle());
```
Was attempted; got reverted (another agent's edit). **Talk to whoever reverted before re-applying** — they may have a reason.

**(B) Closed-loop driver re-stamps Cclamp IC.** `heaviside/sim/runner.py::simulate_closed_loop` rewrites the PULSE duty but skips IC=. For ACF specifically, the steady-state Cclamp voltage is `Vin·(1−2dt/T)/(1−D−2dt/T)`. Per-topology IC patching is generalizable but invasive.

### 3. **flyback inductor_isat_margin FAIL** (honest)

CoreAdviser picks an undersized core (achieved L ≈ 131 µH when spec asks for 1 mH). The harvest is correct; the gate honestly fails. Fix is in MKF's CoreAdviser sizing — it should refuse to short-list cores whose achievable L falls more than e.g. 2× below the spec target.

### 4. **isolated_buck_boost stencil out of date**

```
StencilError: isolated_buck_boost: unexpected element 'Rpri_esr' (resistor) in deck — stencil out of date with MKF spice generator
```

`heaviside/decomposer/stencils.py` — either add `Rpri_esr` to `_TESTBENCH_PREFIXES` (it's a parasitic ESR) or update the `_ISOBB_REAL_KINDS` set.

### 5. **llc / clllc CRASHes**

After the SpiceSimulationConfig was added for LLC/CLLLC and the winding alias was extended for `Secondary 0 Half 1`, they should be close to passing. Last failure for LLC was `no winding named 'sec1' (have: ['Primary', 'Secondary 0 Half 1', 'Secondary 0 Half 2'])` — the alias now matches `Secondary 0 Half 1` but **the LLC enricher specifically asks for 'sec1' (the second secondary half), not 'sec0'**. Need to also accept `Secondary 0 Half 2` when the caller wants `sec1` (since both halves are the same physical winding pair). Easy `_winding_turns_by_name` extension.

### 6. **isolated_buck CRASH / SEGV-then-timeout**

The MKF CoreMesher's `generate_mesh_inducing_coil` empty-harmonic guard (commit `c626aa66`) turns the SEGV into an exception, and the MagneticAdviser bail (`efbbbd6b`) prevents the candidate-loop from burning 10 min. But the underlying issue — iso_buck's secondary winding FFT amplitudes are ~1000× too small (numerical noise) — is unfixed. **Root cause is in ngspice simulation, not Python**: at default Cout IC = spec'd Vout_sec, the diode never forward-biases enough to start conducting, the secondary loop carries no real current, and the FFT just shows noise. Fix candidates: (a) seed Cout to a lower voltage so the diode starts conducting, (b) loosen the diode model, (c) restructure the iso_buck SPICE deck. Needs interactive SPICE work.

### 7. **weinberg fixture invalid for V1**

```
realism enrichment failed: weinberg enrichment: D_min = 0.4800 ≤ 0.5 at Vin_max = 60.0 V — Weinberg V1 requires per-switch D > 0.5 (overlap mode) for boost-style step-up
```

The fixture's `Vout/Vin/n` combination doesn't satisfy the V1 topology constraint. Either pick spec values where D > 0.5 at Vin_max, or extend the enricher to handle V2/V3 variants where D ≤ 0.5 is allowed.

### 8. **CLLC INCOMPLETE**

All checks UNAVAILABLE → no analyst/librarian stage running. Wait for the analyst agent rollout (tracked in task #40, "della Pollock orchestrator").

---

## Known traps

- **Do NOT use `cmake .` to reconfigure** — fails on stale uv-temp Python path. Use `ninja` directly against the existing `build.ninja`.
- **Do NOT run ninja with `-j5` or higher** — OOM-killed cc1plus instances mid-build, leaves the build in an inconsistent state. Use `-j2`.
- **Bash tool harness kills backgrounded ninja** — use foreground Bash with `timeout: 600000` for the build invocation.
- **The forward-family `snubR` is 1 kΩ, not 10 kΩ** — an earlier 10 kΩ change in this session was misdirected (RC snubber dissipation is `C·V²·f`, R-independent) and was reverted in `cb850c21`. The 10 kΩ value is correct only for the boost/Vienna families.
- **`get_extra_components_inputs` is called with the modified spec** (where `desiredInductance` and `desiredMagnetizingInductance` get clobbered to `L_authoritative` by `cli.py:209`). Topologies that distinguish the two (forward family with output choke separate from transformer) need to harvest L from each magnetic's own MAS, not from spec. Already done for buck/boost/flyback/PSFB/forward_family/push_pull/AHB/weinberg/LLC/CLLLC/DAB/iso_buck/iso_buck_boost in commit `9803fa2`.
- **Stripped wheel symbols**: `uv build` strips `.so` symbols. For gdb work, swap in the unstripped `.so` from `_mkf_local/build/`. The unstripped `.so` is currently in the venv.
- **iso_buck regression test fixture has 2 outputs** — push-pull/forward fixtures only have 1. `scripts/corpus_run.py` has a `_NO_TRIM_TOPOLOGIES` allowlist that skips the output-trim step for `isolated_buck` and `isolated_buck_boost` (see commit `8d95f93`).

---

## Quick-reference: how to reproduce things

```bash
# Full corpus
cd /home/alf/OpenConverters/Heaviside
.venv/bin/python scripts/corpus_run.py

# One topology
CORPUS_TOPOLOGIES=push_pull .venv/bin/python scripts/corpus_run.py

# Get sim numbers + verdict for a topology, with debug hook
.venv/bin/python << 'PYEOF'
import sys, json; sys.path.insert(0, '.'); sys.path.insert(0, 'scripts')
from corpus_run import _enrich_for_realism, _extract_spec
from pathlib import Path
from heaviside.pipeline import realism as R
orig = R.evaluate_tas
def hooked(tas, *, topology, spec=None):
    sim = tas.get("simulation_results")
    if isinstance(sim, dict):
        for k, v in sim.items():
            if isinstance(v, dict):
                print(f"sim: eff={v.get('efficiency')} pin={v.get('pin')} pout={v.get('pout')} vout={v.get('vout')} closed={v.get('is_closed_loop')}")
    return orig(tas, topology=topology, spec=spec)
R.evaluate_tas = hooked
import heaviside.pipeline as P; P.evaluate_tas = hooked
spec = _enrich_for_realism(_extract_spec(Path('tests/regression/decomposer/test_<TOPO>.py'), topology='<TOPO>'))
json.dump(spec, open('/tmp/<TOPO>.spec.json','w'))
from heaviside import cli as cli_mod
sys.argv = ['heaviside','design','<TOPO>','--spec','/tmp/<TOPO>.spec.json','--realism','--out','/tmp/<TOPO>.tas.json']
try: cli_mod.app()
except SystemExit: pass
PYEOF

# Inspect a topology's generated SPICE deck
.venv/bin/python -c "
import PyOpenMagnetics.PyOpenMagnetics as p, json
spec = json.load(open('/tmp/<TOPO>.spec.json'))
print(p.generate_ngspice_circuit('<TOPO>', spec, [TURNS], <Lm>, 0, 0, 'switch', {})['netlist'])
"

# Run the bare ngspice deck (bypassing closed-loop iteration)
.venv/bin/python -c "
import json, PyOpenMagnetics.PyOpenMagnetics as p
from heaviside.sim.runner import simulate_steady_state
spec = json.load(open('/tmp/<TOPO>.spec.json'))
nl = p.generate_ngspice_circuit('<TOPO>', spec, [TURNS], <Lm>, 0, 0, 'switch', {})['netlist']
r = simulate_steady_state(nl)
print(f'vout={r.vout:.2f} pin={r.pin:.1f} pout={r.pout:.1f} eff={r.efficiency:.3f}')
"
```

---

## Don't touch

- `heaviside/pipeline/extract.py::_enrich_phase_shifted_full_bridge` — recently rewritten to use `_harvest_inductance` from each magnetic's MAS and `_compute_isat_authoritative` (PyOM call). PSFB went CRASH → PASS via this; don't revert.
- `MKF/src/converter_models/ActiveClampForward.cpp` clamp section — canonical topology rewrite (Cclamp between `clamp_node` and `pri_in`, S_clamp between `vin_dc` and `clamp_node`). Verified working at η=0.93 in direct sim.
- `MKF/src/constructive_models/Coil.cpp::equalize_margins` — two OOB-read fixes (sizing guard + wrap-test). Unblocked PSFB segfault.
- `MKF/src/support/CoilMesher.cpp::generate_mesh_inducing_coil` — empty-harmonic guard. Unblocked iso_buck SEGV.
- `MKF/src/advisers/MagneticAdviser.cpp` candidate loop — bails after N consecutive identical filter throws. Without this, iso_buck wall-clocks past 600s timeout.
- `MKF/src/converter_models/Topology.cpp` SpiceSimulationConfig registry — extensive recent refactor by another agent (LLC/CLLLC/SRC/IBB/AHB/PSFB/PSHB/Vienna/CMC/DMC). Don't revert.
