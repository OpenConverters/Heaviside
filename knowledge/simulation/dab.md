# Dual Active Bridge (DAB) — ngspice Guide

Practical reference for simulating DAB DC-DC converters in ngspice.
Distilled from De Doncker et al. *A Three-Phase Soft-Switched
High-Power-Density DC/DC Converter* (1991, the seminal DAB paper),
Erickson & Maksimović ch. 6, and TI TIDA-010054 design guide.

## Topology at a glance

```
Vin ─┬─ S1 ─┬── La ─┬─ Lp ─┐
     │     │       │       ├─ Lm  (transformer primary)
     │     │       │       │
     │     │       └───────┘
     │     │       primary AC node
     ├─ S2 ─┘
     │
     └─ S3 ─┬── (mirror image of primary side)
            └─ S4 ─

                    transformer secondary
                       │
                       ├─ Q5 ─┬─ Q7 ─┐
                       │      │      │
                       │      │      └─ Vout ─ Cout ─ Rload ─ 0
                       └─ Q6 ─┴─ Q8 ─┘
                       (synchronous full bridge — NO diodes)
```

- Two H-bridges, one each side of an isolation transformer.
- Power flows controlled by **phase shift** between primary and secondary
  H-bridge gate drives. Phase shift φ in radians:
  - φ = 0 → no power transfer
  - φ = π/2 → maximum power
  - sign of φ determines direction (DAB is inherently bidirectional)
- A series inductor `La` (often the transformer leakage) sets the power
  level. P_out ≈ (Vin · Vout · φ · (π - |φ|)) / (2π² · fsw · La · n)
- **Output rectification is SYNCHRONOUS** — Q5–Q8 are MOSFETs, not diodes.
  Adding diodes is topologically wrong (TIDA-010054 review caught this).

## Sizing rules (start here)

```
n = Np/Ns           ; turns ratio. For step-down (Vin>Vout): n = Vin/Vout typical
La = Vin·Vout·φ_max·(π-φ_max) / (2π²·fsw·n·P_max)   ; for max power at φ_max
   ; pick φ_max = π/3 (60°) for soft switching margin
```

Soft-switching condition (ZVS) holds when current through La is large
enough at switching instant to charge/discharge MOSFET Coss within
deadtime. Below the ZVS boundary, hard switching → losses spike.

## Dot convention (transformer)

DAB transformer is symmetric (primary and secondary act as both source
and sink). The dots must be consistent:

```
Lprim prim_a  prim_b  Lp
Lsec  sec_a   sec_b   Ls
K1 Lprim Lsec 0.99
```

Dot convention rule: when current flows INTO the dotted end of the
primary, the secondary's dotted end is the *source* (positive emf).
For DAB, both H-bridges drive their respective transformer windings,
so the convention only affects the *direction* of phase shift.

If your sim runs with reverse power flow despite Vin > Vout, flip K
sign OR swap one winding's terminal order. **No diode reorientation
needed** because there are no rectifier diodes — the synchronous
switches Q5–Q8 are gate-driven independently.

## Critical ngspice gotchas for DAB

### 1. NO output diodes
A common (wrong) move is to add a diode bridge on the secondary. That
breaks the inherently bidirectional design and produces nonsense.
Use four MOSFET switches modeled as `S` voltage-controlled switches
plus their gate drives:

```
* Secondary H-bridge (synchronous)
S5 sec_a Vout  q5g 0 SMOD
S6 0    sec_a q6g 0 SMOD
S7 sec_b Vout  q7g 0 SMOD
S8 0    sec_b q8g 0 SMOD
```

### 2. Phase-shift gate drive

Primary and secondary H-bridges run at the same frequency. Secondary
gate signals are **delayed by φ/(2π) · TPER** relative to primary:

```
* Primary diagonal pair S1 (high-side) + S4 (low-side opposite leg)
Vq1g q1g 0 PULSE(0 10 0       1n 1n  TPER/2 - DEAD  TPER)
Vq4g q4g 0 PULSE(0 10 0       1n 1n  TPER/2 - DEAD  TPER)
* Other diagonal S2 + S3
Vq2g q2g 0 PULSE(0 10 TPER/2  1n 1n  TPER/2 - DEAD  TPER)
Vq3g q3g 0 PULSE(0 10 TPER/2  1n 1n  TPER/2 - DEAD  TPER)
* Secondary — same waveforms but delayed by phi_delay
Vq5g q5g 0 PULSE(0 10 phi_delay        1n 1n  TPER/2 - DEAD  TPER)
* (etc. for q6g, q7g, q8g)
```

`phi_delay = (phi_radians / (2*pi)) * TPER`. For phi=π/3 at fsw=100kHz:
`phi_delay = (π/3 / 2π) * 10µs = 1.667µs`.

### 3. Initial conditions are mandatory

Without `IC=` on the series inductor La and Cout, ngspice's DC
operating-point solver gets stuck because the bidirectional bridges
make the steady-state ambiguous. Always:

```
La  prim_a int   La_value IC=0
Cout out 0 100u IC={Vout_nominal}
.tran TSTEP TSTOP 0 TSTEP uic
```

### 4. Settling time is long

DAB output ripple settles over ~`100/fsw` cycles. Use TSTOP ≥ 1 ms
even for fsw=100kHz, and measure over the last 20%.

## Common SPICE failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Vout near 0 V, P_out ≈ 0 | Phase shift φ = 0; primary and secondary gate signals not staggered | Apply `phi_delay` offset to secondary PULSE sources |
| Vout = -440 V (negative target) | Phase shift sign wrong; reverse power flow | Flip phi_delay sign OR swap one bridge's diagonal pair gate signals |
| Vout gradually drifts up to absurd value | Synchronous switches modeled as diodes by mistake | Use `S` switch elements with gate drives, not `D` model |
| `singular matrix` at t=0 | DC operating point undefined for floating bridge | `uic` flag + `IC=` on every storage element |
| Vout = exactly Vin/n | Phi=180° (bridges anti-phase but no shift) | This is the unloaded condition; load Rload may be too large |

## Power measurement

```
.meas tran v_out_ss AVG V(out)            FROM=Tmeas TO=TSTOP
.meas tran p_out    AVG V(out)*I(Rload)   FROM=Tmeas TO=TSTOP
.meas tran p_in     AVG -V(vin)*I(Vin)    FROM=Tmeas TO=TSTOP
```

Same `-V(vin)*I(Vin)` sign rule as everywhere else.

## When DAB is the wrong tool

- For light-load operation, DAB efficiency drops sharply (loses ZVS).
  Use a phase-shifted full bridge or LLC instead.
- For unidirectional power flow, a phase-shifted full bridge is
  simpler — no need for synchronous secondary.
- DAB is bidirectional by design. If your reference is unidirectional,
  you may have mis-identified the topology.
