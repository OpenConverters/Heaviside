# MKF Issues Blocking Heaviside Corpus — Handoff

**Date:** 2026-05-26
**From:** Heaviside agent
**Corpus state:** 18/21 PASS. The 3 remaining failures are all rooted in MKF.

---

## 1. Flyback: CoreAdviser never gaps the transformer

**Symptom:** `inductor_isat_margin` FAIL — isat=1.2A vs ipeak=1.8A (ratio 0.66, need ≥ 1.2).

**Root cause:** MKF's CoreAdviser/MagneticAdviser always picks **ungapped ferrite toroids** for flyback transformers. Flyback transformers *must* have an air gap to store energy — without one, the core saturates at a fraction of an ampere. Every spec variant tested produces ungapped cores:

| Spec tweak | Core | Material | Gap? | L achieved |
|---|---|---|---|---|
| baseline (48V→12V, 2A, 200kHz) | T 38.1/19.05/12.7 | N30 | no | 132 µH |
| Iout=1A | T 38.1/19.05/12.7 | N30 | no | 262 µH |
| Iout=0.5A | (larger toroid) | — | no | 479 µH |
| fsw=100kHz | T 63/38/25 | T35 | no | 527 µH |
| lower ripple (0.2) | T 30/17.9/16.0 | 76 | no | 494 µH |

B_sat at 100°C for N30 = 0.229 T.  With N=3, A_e=121mm², L=132µH:
analytical isat = 0.229 × 3 × 121e-6 / 132e-6 = 0.63 A — far below any useful operating current.

**What needs to change in MKF:**
1. `CoreAdviserGapping.cpp` (or wherever gapping strategy lives): flyback topology must produce **gapped** cores. The gap length sets the stored energy capability: `E = ½ L I²`, and gap controls the trade-off between L and isat.
2. `CoreAdviserPipeline.cpp`: the filter pipeline should reject ungapped candidates for flyback (and possibly isolated_buck_boost, which also stores energy in the transformer).
3. The scoring should prefer cores where `isat ≥ 1.2 × ipeak_worst` — this is the Maniktala criterion that the Heaviside realism gate enforces.

**Reproducing:**
```bash
cd /home/alf/OpenConverters/Heaviside
CORPUS_TOPOLOGIES=flyback .venv/bin/python scripts/corpus_run.py
```

**Test fixture:** `tests/regression/decomposer/test_flyback.py` — 48V→12V, 2A, 200kHz, n=2.

---

## 2. Isolated Buck: MagneticAdviser timeout / SEGV

**Symptom:** CRASH — timeout after 600s (or SEGV before the empty-harmonic guard was added in `c626aa66`).

**Root cause (from prior session investigation):** At the default Cout IC = spec'd Vout_sec, the secondary diode never forward-biases enough to start conducting. The secondary loop carries no real current → the FFT amplitudes are ~1000× too small (numerical noise) → CoilMesher's `generate_mesh_inducing_coil` gets empty harmonics.

The `c626aa66` guard turns the SEGV into an exception, and the `efbbbd6b` MagneticAdviser bail prevents infinite looping. But the adviser still burns 600s iterating through core candidates that all fail the same way.

**What needs to change in MKF:**
1. The isolated_buck SPICE deck needs to seed Cout at a **lower** voltage so the rectifier diode starts conducting during transient. Currently `IC=Vout_spec` means the cap is pre-charged to exactly the output voltage — no voltage difference to forward-bias the diode.
2. Alternatively, loosen the DIDEAL model for this topology (higher IS or lower VT) so the diode conducts at smaller forward bias.
3. The MagneticAdviser should fail faster when ALL candidates throw the same exception — the current bail-after-N-consecutive may not be aggressive enough (still allows 600s of wall time).

**Reproducing:**
```bash
CORPUS_TOPOLOGIES=isolated_buck .venv/bin/python scripts/corpus_run.py
```

**Test fixture:** `tests/regression/decomposer/test_isolated_buck.py` — has 2 outputs.

---

## 3. ACF: analytical duty + canonical clamp topology

**Status:** PASS in Heaviside (workaround applied in Python), but MKF's deck is still suboptimal.

The `ActiveClampForward.cpp::generate_ngspice_circuit` emits the **old** clamp topology:
```
S_clamp clamp_cap sw_node ...    ← should be: S_clamp vin_dc clamp_node ...
Cclamp  clamp_cap 0 ...          ← should be: Cclamp  clamp_node pri_in ...
Rclamp  clamp_cap 0 1MEG         ← should be: Rclamp  clamp_node pri_in 1MEG
```

And the IC formula should be `Vin × (T − 2dt) / (T − tOn − 2dt)` (cap across primary), not `Vin × tOn / (T − tOn − 2dt)` (cap to GND).

Heaviside's `sim/runner.py::_transform_acf_topology` rewrites the netlist at simulation time, but ideally MKF would emit the canonical topology directly so other consumers (WebFrontend, ngspice direct runs) also get the correct circuit.

The analytical duty fix in `process_operating_points_for_input_voltage` (line 61: `t1 = period * (Vout+Vd) / (Vin/n)`) is already in the source — keep it.

**What needs to change in MKF:**
Apply the canonical clamp topology in `generate_ngspice_circuit` (~lines 649-663):
```cpp
circuit << "S_clamp vin_dc clamp_node clamp_ctrl 0 SW1\n";
double clampVoltage = inputVoltage * (period - 2 * deadTime) / (period - tOn - 2 * deadTime);
circuit << "Cclamp clamp_node pri_in " << ... << " IC=" << clampVoltage << "\n";
circuit << "Rclamp clamp_node pri_in 1MEG\n\n";
```

Also ensure the DIDEAL model uses `SpiceSimulationConfig` values (`diodeIS`, `diodeRS`) rather than hardcoded `IS=1e-14 RS=1e-6`, which causes "timestep too small" with the canonical topology.

---

## Build notes

- **Build dir:** `/home/alf/OpenMagnetics/PyMKF/build/cp311-cp311-linux_x86_64/` (Python 3.11)
- **Heaviside venv:** Python 3.12 — the 3.11 .so has ABI mismatch. The existing `cpython-312` .so in the venv was hand-copied from a prior build.
- **CMake reconfigure is broken** (stale uv-temp Python path). Use `ninja -j2 PyOpenMagnetics` directly.
- **After editing MKF source:** copy changed `.cpp` files into `_deps/mkf-src/src/...` in the build tree (the build fetched its own copy via FetchContent).
- **Memory:** `-j2` only. `-j4` or higher OOM-kills cc1plus.
- To install: `cp PyOpenMagnetics.cpython-3XX-*.so` into `.venv/lib/python3.12/site-packages/PyOpenMagnetics/`.
