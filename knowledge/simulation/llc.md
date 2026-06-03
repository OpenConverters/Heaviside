# LLC Resonant Converter — ngspice Guide

Practical reference for simulating half-bridge LLC converters in ngspice.
Distilled from Erickson & Maksimović *Fundamentals of Power Electronics* ch. 19–20,
Basso *Switch-Mode Power Supplies* 2nd ed. ch. 12, TI app notes SLUP263 / SLUA733,
and the MKF analytical model (`MKF/src/converter_models/Llc.cpp`).

## Topology at a glance

```
Vin ─┬─ S_hi ─┬─ Cr ─ Lr ─┬─ Lp ─┐
     │       │            │      ├─ Lm   (transformer primary)
     │       │            │      │
     │       │            │      └────── primary return
     │       │            └── transformer secondary (center-tap)
     └─ S_lo ─┘                ├─ Ls1 ─ D1 ─┐
                              ct           │
                               ├─ Ls2 ─ D2 ─┴── Cout ── Rload ── 0
                               │
                              ─0─
```

- Half-bridge switches `S_hi`, `S_lo` driven complementary at ~50% duty,
  small dead time (typ. 50–500 ns).
- Resonant tank: `Cr` series, `Lr` series, `Lm` shunt (transformer
  magnetizing inductance).
- Transformer ratio `n = Np/Ns_per_half` (each half-secondary shares the
  same turn count). At resonance fr=fsw, **Vout ≈ Vin/(2·n)** with rectifier
  drops ignored.
- Two secondary halves with center tap → two diodes (or sync-rect FETs)
  for full-wave rectification.

## Sizing rules (start here)

Given Vin, Vout, Iout, fsw, target an operating point at-resonance (fsw≈fr):

```
Ns/Np  = 2 · Vout / Vin
n_np_ns = 1 / (Ns/Np)           ; primary-to-secondary turns ratio
Rac    = (8 / π²) · n² · Rload   ; reflected resistance from secondary to primary
Q      = 0.3–0.5 (lower = sharper resonance, less margin)
Z0     = Q · Rac
Cr     = 1 / (2π · fr · Z0)
Lr     = Z0² · Cr
Lm     = (5–10) · Lr             ; gain limit; pick 6 by default
```

Larger `Lm/Lr` ratio = higher peak gain at low fsw but also harder to
hold-up the switching frequency at light load (frequency runs away).

## Dot convention rules (CRITICAL)

ngspice coupled-inductor syntax:

```
Lp  prim_dot  prim_ret  100u
Ls  sec_dot   sec_ret   10u
K1  Lp Ls 0.999
```

The **first listed node of each inductor is the dot**.

For an LLC with center-tap secondary:

```
* Primary
Lp  prim_dot  prim_ret  Lm_value   ; dot at prim_dot (HV end after Cr-Lr)

* Two secondary halves, both dotted at the center tap
Ls1 ct  sec_a  Ls_value
Ls2 ct  sec_b  Ls_value
K1 Lp Ls1 0.999
K2 Lp Ls2 0.999
K3 Ls1 Ls2 0.999

* Diodes anode at non-dotted end (sec_a, sec_b), cathode at out
D1 sec_a out DMOD
D2 sec_b out DMOD
Vct ct 0 DC 0
```

If you flip K's sign you must also swap the corresponding inductor's
node order — they are mathematically equivalent. **Do not do both** —
that double-flip leaves D1/D2 pointing the wrong way and the rectifier
either freewheels into Vin (Vout → Vin/2) or shorts Vout (numerical
blow-up).

## Common SPICE failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Vout = 0 V or stuck at small ripple | Diodes backward; dot convention inverted | Swap secondary node order (not K sign) and verify D anode at non-dot |
| Vout = Vin or huge | Center-tap floating, or both diodes always-on | Add `Vct ct 0 DC 0`; check `D1 sec_a out` and `D2 sec_b out` |
| Vout drifts up indefinitely (10⁵+ V) | Flux not balancing — K too close to 1.0 with idealized switches | Use `K=0.999` not `1.0`, add 10 kΩ damper from `prim_dot` to GND, 100 kΩ from each secondary node to its return |
| ngspice timestep collapse, "trouble converging at time = ..." | Tightly-coupled inductors with no parallel resistors | Add 10 kΩ on primary, 100 kΩ on each secondary |
| Vout oscillates wildly, never settles | Q too high (Q > 0.7) | Lower Q; or set `IC=Vout` on Cout to skip startup transient |
| `singular matrix` at t=0 | Floating subnet — center tap or ct-to-ground missing | Add `Vct ct 0 DC 0` and confirm Cout connects out → 0 |

## Convergence aids

```
.OPTIONS RELTOL=1e-3 ABSTOL=1e-6 VNTOL=1e-4 ITL1=500 ITL2=500 METHOD=GEAR
.tran TSTEP TSTOP 0 TSTEP uic        ; uic flag is essential
.ic V(out)=Vout_target                ; pre-charge output to skip startup
.ic V(ctank)=0                        ; series tank cap
```

`uic` flag tells ngspice to use the `IC=` initial conditions instead of
solving a DC operating point (which is undefined for resonant tanks).

## How long to simulate

- LLC settling time ~ `10·Q/fr` (10/Q resonant cycles). For fr=100 kHz,
  Q=0.4 → ~250 µs to settle.
- Simulate at least 50 switching cycles, measure over the last 20%.
- `.tran TSTEP TSTOP` with `TSTOP = max(50/fsw, 5 ms)` is a safe default.

## Power measurement (sign-correct)

```
.meas tran v_out_ss AVG V(out) FROM=Tmeas TO=TSTOP
.meas tran p_out    AVG V(out)*I(Rload) FROM=Tmeas TO=TSTOP
.meas tran p_in     AVG -V(vin)*I(Vin) FROM=Tmeas TO=TSTOP
```

Note the **leading minus on p_in** — ngspice's `I(Vin)` flows into the +
terminal of an independent source, so without the negation `p_in` comes
out negative and efficiency = -134%.

## Behavioral half-bridge gate drive

Skip the controller IC entirely. Drive the half-bridge directly:

```
Vhi_g hi_g 0 PULSE(0 10 0       1n 1n  TPER/2 - DEAD  TPER)
Vlo_g lo_g 0 PULSE(10 0 TPER/2  1n 1n  TPER/2 - DEAD  TPER)
S_hi vin hb hi_g 0 SMOD
S_lo hb 0 lo_g 0 SMOD
.model SMOD SW(Ron=10m Roff=1Meg Vt=5 Vh=0)
```

The `TPER/2` offset on `Vlo_g` enforces complementarity; the `DEAD`
substraction on the high-time creates the dead band so both switches
are never on simultaneously. For LLC, dead time of 2–5% of TPER is
typical.

## When NOT to use this

If the converter is in burst mode, has a DCM-LLC transition, or includes
a primary-side current-mode controller, the average model in
`averaged-pwm-models.md` will give a faster bode plot but won't catch
ZVS/ZCS issues. For replication of an existing design's operating point
at full load, the cycle-by-cycle netlist above is the right tool.
