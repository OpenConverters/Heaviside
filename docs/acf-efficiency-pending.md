# ACF efficiency_sanity FAIL — pending work

**Status (2026-05-26): efficiency 0.10 → 0.65 after deck/topology fixes; still below the 0.70 realism threshold. Needs one more push.**

## Background

`active_clamp_forward` was crashing efficiency_sanity at η = 0.10 (open-loop fallback after closed-loop failed to converge). Five MKF commits this session lifted it to η = 0.65:

1. `21abe9e8 fix(acf): non-overlapping S1/S_clamp PWM + scientific format`
   – Dead-time between main and clamp switches (was overlapping → dead short).
2. `25b37da2 fix(acf): SW1 RON from cfg, UIC, corrected V_clamp formula`
   – `RON=0.01` from `spice_config()`, `.tran UIC`, V_cap formula uses
     `Vin·tOn/(period − tOn − 2·dt)`.
3. **Canonical topology rewrite** (in `ActiveClampForward.cpp::generate_ngspice_circuit`):
   – `S_clamp vin_dc clamp_node clamp_ctrl 0 SW1` (high-side aux from Vin).
   – `Cclamp clamp_node pri_in 10µF IC=Vin·(1−2dt/T)/(1−D−2dt/T)`
     (cap **across** primary, not to GND).
   – `Rclamp clamp_node pri_in 1MEG` (bleeder across cap).
   – Previous topology (`Cclamp clamp_cap 0 IC=Vin·D/(1−D)`) referenced
     the cap to GND, so during clamp-ON `sw_node` was pulled to +V_cap,
     same polarity as during S1-ON → transformer flux never reset →
     magnetising current drifted → ~80 W dissipated in deck parasitics.

## Why it's still FAIL

Direct sim on the deck (`simulate_steady_state(deck)`, default duty D=0.45):
```
vout = 21.67 V    pin = 211 W    pout = 196 W    η = 0.928
```

The topology is fundamentally efficient. The problem is the **operating point**:

- MKF deck-builder uses `get_maximum_duty_cycle()` which falls back to **0.45** for this fixture.
- Analytically-correct duty for `Vout=12V` at `Vin=48V`, `n=2` is `D = (Vout+Vd)·n/Vin = 0.529`.
- At D=0.45 the deck delivers `Vout = Vin·D/n − Vd ≈ 10 V` (under spec'd 12V), so `Pout = 41 W` instead of `60 W`.
- The Heaviside closed-loop driver (`heaviside/sim/runner.py::simulate_closed_loop`) iterates duty to drive vout → 12V target, but **doesn't update Cclamp's IC=** as duty changes. The cap drifts during transient → ngspice trips "Timestep too small ... trouble with dideal-instance dfwd0".
- So open-loop fallback runs at D=0.45 → vout=10V → η=0.65.

## Next step

Two viable fixes (try in order):

**A) Make the MKF deck use the analytical duty for its starting point** (instead of `get_maximum_duty_cycle()` ceiling). Edit `ActiveClampForward::generate_ngspice_circuit`:

```cpp
double mainOutputVoltage_ = opPoint.get_output_voltages()[0];
double n_ = turnsRatios[0];
double dutyCycleAnalytical =
    (mainOutputVoltage_ + diodeVoltageDrop) * n_ / inputVoltage;
double dutyCycle = std::min(dutyCycleAnalytical, get_maximum_duty_cycle());
```

This was attempted in this session but the edit got reverted (intentionally per a system note). Talk to whoever reverted before re-applying.

**B) Teach the closed-loop driver to re-stamp `Cclamp`'s `IC=`** when it rewrites the duty. Look at `heaviside/sim/runner.py::simulate_closed_loop` around the deck-rewrite step. Pattern:

```python
# After rewriting PWM duty, also update any IC= that's a function of D.
# For ACF: Cclamp IC = Vin·(1−2dt/T)/(1−D−2dt/T).
```

This is the more general fix but requires per-topology knowledge of which IC values are duty-dependent.

## Reproducing the FAIL

```bash
cd /home/alf/OpenConverters/Heaviside
CORPUS_TOPOLOGIES=active_clamp_forward .venv/bin/python scripts/corpus_run.py
```

```bash
# Direct sim on the deck (proves topology works at default duty):
.venv/bin/python -c "
import json, PyOpenMagnetics.PyOpenMagnetics as p
from heaviside.sim.runner import simulate_steady_state
spec = json.load(open('/tmp/acf.spec.json'))
nl = p.generate_ngspice_circuit('active_clamp_forward', spec, [2.0], 1e-3, 0, 0, 'switch', {})['netlist']
r = simulate_steady_state(nl)
print(f'vout={r.vout:.2f} pin={r.pin:.1f} pout={r.pout:.1f} eff={r.efficiency:.3f}')
"
```

## Don't touch (already verified working)

- The canonical topology rewrite is correct (proven by η=0.93 direct sim).
- SW1 RON, UIC, dead-time PWM, scientific-format time literals all needed
  to be there — removing any of them regresses the deck.
- The forward-family `snubR` is back at 1 kΩ (the 10 kΩ change in this
  session was misdirected; RC snubber dissipation is C·V²·f, R-independent).
