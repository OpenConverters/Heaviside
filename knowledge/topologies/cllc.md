---
description: Design a CLLC resonant converter for bidirectional isolated DC-DC, calculate resonant tank and transformer, generate ngspice netlist
---

# CLLC Resonant Converter Design

## When to Use
- Bidirectional isolated DC-DC conversion (power flows in both directions)
- Symmetric operation required in forward and reverse modes
- Battery energy storage systems (BESS), V2G (vehicle-to-grid), DC microgrids
- On-board charger (OBC) with bidirectional capability
- Solid-state transformer stages, DC-DC stage of cascaded converters
- High efficiency needed in both directions (ZVS primary, ZCS secondary achievable in both)
- Medium-to-high power: 1 kW to 20+ kW typical

## Circuit Description

The CLLC resonant converter is a symmetric bidirectional extension of the LLC converter. It uses two full-bridge (or half-bridge) switching legs connected through a resonant tank that includes resonant elements on BOTH sides of the transformer.

Components:
- Primary full-bridge: Q1-Q4 (MOSFETs or GaN HEMTs)
- Secondary full-bridge: Q5-Q8 (MOSFETs, acting as synchronous rectifiers OR active switches in reverse mode)
- Primary resonant inductor: Lr1
- Primary resonant capacitor: Cr1
- Transformer with magnetizing inductance Lm, turns ratio n = Np/Ns
- Secondary resonant inductor: Lr2
- Secondary resonant capacitor: Cr2
- Input and output DC capacitors: Cin, Cout

### Relationship to LLC and CLL

The CLLC is formed by mirroring the LLC resonant tank:
- Forward mode (primary to secondary): operates as an LLC converter. Lr1 is the series resonant inductor, Cr1 is the series resonant capacitor, Lm provides the parallel inductance.
- Reverse mode (secondary to primary): operates as a CLL (or equivalently, an LLC from the secondary perspective). Lr2 and Cr2 form the secondary-side resonant network.

The key insight from Kazimierczuk Ch18 (CLL analysis): the CLL and LLC topologies share the same mathematical gain structure. By designing Lr2 and Cr2 to mirror the resonant behavior of Lr1 and Cr1, symmetric gain curves can be achieved in both directions.

### Tank Symmetry Condition

For symmetric bidirectional operation, the resonant tank must satisfy:

```
Lr1 * Cr1 = Lr2 * Cr2       (same resonant frequency both directions)
Lr1/Lm = n^2 * Lr2/Lm       (same inductance ratio both directions)
```

This yields the design constraint:
```
Lr2 = Lr1 / n^2
Cr2 = Cr1 * n^2
```

When these conditions are met, the gain curves are identical in both directions (referred to the same side).

### Two Resonant Frequencies (per direction)

Forward mode:
```
fr_fwd = 1 / (2*pi*sqrt(Lr1 * Cr1))          (series resonant frequency)
fp_fwd = 1 / (2*pi*sqrt((Lr1 + Lm) * Cr1))   (lower resonant frequency)
```

Reverse mode:
```
fr_rev = 1 / (2*pi*sqrt(Lr2 * Cr2))           (series resonant frequency)
fp_rev = 1 / (2*pi*sqrt((Lr2 + Lm/n^2) * Cr2))  (lower resonant frequency)
```

With the symmetry condition: fr_fwd = fr_rev and fp_fwd = fp_rev.

## Design Procedure

### Step 1: Define System Specifications

```
V1 = primary-side DC voltage (e.g., 400V DC bus)
V2 = secondary-side DC voltage (e.g., 48V battery)
P_max = maximum power in both directions
Eff = target efficiency (typically 0.95-0.97)
```

### Step 2: Determine Transformer Turns Ratio

For symmetric gain at unity (M = 1 at resonance):

```
n = Np/Ns = V1 / V2     (full-bridge to full-bridge)
n = V1 / (2*V2)          (full-bridge to half-bridge)
```

### Step 3: Choose Inductance Ratio and Quality Factor

```
m = Lm/Lr1               (magnetizing-to-resonant inductance ratio, typically 3-8)
Q = sqrt(Lr1/Cr1) / Rac  (quality factor at full load)
Rac = 8*n^2*RL / pi^2    (AC equivalent load resistance)
```

Design tradeoffs (same as LLC):
- Lower m: wider gain range, easier to handle input voltage variation, but higher magnetizing current and conduction loss
- Higher m: narrower gain range, lower circulating current, better peak efficiency
- Lower Q: wider gain range, flatter gain curves
- Higher Q: sharper selectivity, higher peak efficiency near resonance

### Step 4: Calculate Resonant Components (Primary Side)

Choose resonant frequency fr (typically 80-200 kHz):

```
Cr1 = 1 / (2*pi*fr * Q * Rac)
Lr1 = 1 / ((2*pi*fr)^2 * Cr1)
Lm = m * Lr1
```

### Step 5: Calculate Resonant Components (Secondary Side)

Apply symmetry condition:

```
Lr2 = Lr1 / n^2
Cr2 = Cr1 * n^2
```

### Step 6: Verify Gain Range

Using the LLC/CLL gain equation (valid for both directions):

```
|M| = 1 / sqrt((1 + 1/m - 1/(m*F^2))^2 + Q^2*(F - 1/F)^2)
```

Where F = fs/fr. Verify:
- M_max achievable at F < 1 (for minimum input voltage)
- M_min achievable at F > 1 (for maximum input voltage)
- ZVS maintained over entire operating range in both directions

### Step 7: Design Transformer

The transformer must provide:
- Turns ratio n
- Magnetizing inductance Lm (set by air gap)
- Low leakage inductance (or controlled leakage to partially implement Lr1)

```
Bmax = V1 / (4 * fs_min * Np * Ae)   (peak flux density)
```

Choose core and Np such that Bmax < 0.3T (ferrite) with margin.

### Step 8: ZVS and Dead Time

Both sides require ZVS. The magnetizing current provides the ZVS energy:

```
I_Lm_peak = V2 * n / (4 * Lm * fs)    (forward mode)
I_Lm_peak = V1 / (4 * Lm * fs)        (reverse mode, referred to primary)

ZVS condition: (1/2)*Lm*I_Lm_peak^2 >= 2*Coss*Vbus^2

Dead time: t_dead >= pi * sqrt(Lm * 2*Coss)
           t_dead < T/2 - 1/(2*fr)
```

For the secondary side in reverse mode, the same equations apply with Lm/n^2 replacing Lm and Coss of the secondary MOSFETs.

## Key Equations

### Gain Function (Forward Mode, FHA)

```
M_fwd = V2*n / V1 = 1 / sqrt((1 + 1/m - 1/(m*F^2))^2 + Q_fwd^2*(F - 1/F)^2)
```

### Gain Function (Reverse Mode, FHA)

```
M_rev = V1/(V2*n) = 1 / sqrt((1 + 1/m' - 1/(m'*F^2))^2 + Q_rev^2*(F - 1/F)^2)
```

Where m' = (Lm/n^2)/Lr2 = m (by symmetry condition), and Q_rev = sqrt(Lr2/Cr2)/Rac_rev.

With symmetric tank design, M_fwd and M_rev have identical gain curves.

### At Resonant Frequency (fs = fr)

```
M(fr) = m / (m-1)    (independent of load, both directions)
```

For V1 = n*V2 (nominal voltage ratio matching turns ratio), M = 1 requires m/(m-1) = 1, which gives m -> infinity. In practice, design for M(fr) slightly above 1 to handle losses.

### Resonant Tank Design Summary

```
fr = 1 / (2*pi*sqrt(Lr1*Cr1)) = 1 / (2*pi*sqrt(Lr2*Cr2))
fp = 1 / (2*pi*sqrt((Lr1+Lm)*Cr1))
Z0 = sqrt(Lr1/Cr1)            (characteristic impedance, primary side)
Q = Z0 / Rac                   (quality factor)
m = Lm / Lr1                   (inductance ratio)
Rac = 8*n^2*RL / pi^2          (effective AC load resistance)
```

## Component Stresses

### Primary MOSFETs (full-bridge, forward mode)

```
V_Q_max = V1                          (each MOSFET blocks full V1)
I_Q_rms = I_Lr1_rms / sqrt(2)         (each switch carries half)
I_Q_turnoff = I_Lm_peak               (turn-off current = magnetizing current)
```

### Secondary MOSFETs (full-bridge, forward mode as synchronous rectifier)

```
V_Q_max = V2                          (each MOSFET blocks full V2)
I_Q_rms ~ (pi/(2*sqrt(2))) * Io / 2   (sinusoidal half-wave, per switch)
I_Q_avg = Io / 2                       (per switch)
```

### Primary Resonant Capacitor Cr1

```
V_Cr1_dc = 0  (full-bridge, no DC bias unlike half-bridge)
V_Cr1_peak = I_Lr1_peak / (2*pi*fs*Cr1)
I_Cr1_rms = I_Lr1_rms                  (series path)
```

Use film capacitors or C0G/NP0 ceramics. Must handle full AC RMS current.

### Secondary Resonant Capacitor Cr2

```
V_Cr2_peak = I_Lr2_peak / (2*pi*fs*Cr2)
I_Cr2_rms = I_Lr2_rms
```

### Resonant Inductors Lr1, Lr2

```
I_Lr1_rms = sqrt(I_load^2 + I_mag^2)  (primary side)
I_Lr2_rms = n * I_Lr1_rms             (secondary side, approximately)
```

Lr1 can be partially or fully realized as transformer leakage inductance. If separate, use gapped ferrite core. Lr2 similarly on the secondary side.

## Bidirectional Gain Symmetry

### Why Symmetry Matters

In a standard LLC (Lr and Cr on primary only), the reverse-mode gain characteristics differ significantly from forward mode. The secondary side sees only the magnetizing inductance and no series resonant capacitor, leading to asymmetric ZVS conditions, different gain slopes, and potentially hard switching in reverse.

The CLLC solves this by adding a resonant tank on the secondary side (Lr2, Cr2), creating a symmetric structure. When the symmetry condition Lr1*Cr1 = Lr2*Cr2 is satisfied:

1. **Same resonant frequency** in both directions
2. **Same gain curves** (referred to one side) in both directions
3. **ZVS achievable** in both directions under the same conditions
4. **ZCS on rectifier diodes** (or body diodes of SR MOSFETs) in both directions when operating below fr

### Asymmetric Design (Advanced)

For applications where V1 and V2 have different voltage ranges, or power rating differs between directions, an asymmetric CLLC can be designed:

```
Lr1*Cr1 =/= Lr2*Cr2    (different resonant frequencies)
```

This gives different gain curves in each direction, allowing optimization for different operating points. The tradeoff is increased design complexity and potentially different optimal switching frequencies for each direction.

### Mode Transition

When reversing power flow:
- The active bridge transitions from inverter mode to synchronous rectifier mode (and vice versa)
- Dead time control may need adjustment (different Coss values on each side)
- The frequency control loop must be designed for stability in both directions
- A smooth mode transition requires: detect power flow direction, switch control strategy, avoid shoot-through during transition

## Practical Design Considerations

### Integrated Magnetics

For the CLLC, integrated magnetics can combine:
- Transformer (turns ratio n, magnetizing inductance Lm)
- Primary leakage inductance (partially implements Lr1)
- Secondary leakage inductance (partially implements Lr2)

If leakage is insufficient, external inductors are added. The leakage can be controlled via:
- Winding separation on the bobbin
- Use of interleaving (less leakage) vs sectional winding (more leakage)
- PCB transformer with controlled layer spacing

### GaN and SiC Considerations

For high-frequency CLLC designs (>500 kHz):
- GaN HEMTs: lower Coss enables faster ZVS transitions, smaller dead time, higher efficiency
- SiC MOSFETs: preferred for high-voltage (>600V) applications
- PCB-integrated magnetics become practical at MHz frequencies
- Cr1 and Cr2 can use MLCC ceramics (C0G/NP0) at high frequencies

### Control Strategy

Common approaches:
1. **Frequency control**: vary fs to regulate output. Simple but variable frequency complicates EMI.
2. **Phase-shift control**: full-bridge on each side with phase shift between legs. Constant frequency.
3. **Hybrid control**: frequency control for wide-range regulation, phase shift for fine tuning.
4. **Direction detection**: monitor power flow direction (current polarity or voltage error sign) to switch between forward and reverse control loops.

### Efficiency Optimization

- At resonance (fs = fr): lowest circulating current, highest efficiency. Design for M = 1 at nominal operating point.
- Below resonance: ZCS on secondary diodes eliminates reverse recovery, beneficial for Si MOSFETs.
- Above resonance: lower circulating current but hard commutation of secondary diodes.
- Dead time optimization: smaller dead time reduces body diode conduction loss but requires sufficient ZVS margin.

## Ngspice Netlist Template

```spice
* CLLC Resonant Bidirectional Converter
* V1={V1}V, V2={V2}V, P={P}W, fr={fr}Hz

.title CLLC Resonant Bidirectional Converter

* Parameters
.param V1={V1}
.param V2={V2}
.param P={P}
.param Rload={V2*V2/P}
.param fs={fs}
.param Lr1={Lr1}
.param Cr1={Cr1}
.param Lm={Lm}
.param Lr2={Lr2}
.param Cr2={Cr2}
.param n={n}
.param tdead={tdead}
.param tstep={1/(fs*200)}
.param tstop={80/fs}
.param tstart={40/fs}

* Primary DC supply
Vin v1p v1n DC {V1}

* Primary full-bridge gate drives
* Leg A: Q1 (high), Q3 (low) -- complementary
* Leg B: Q2 (high), Q4 (low) -- phase-shifted 180 degrees
Vg1 g1 0 PULSE(0 15 0 1n 1n {0.5/fs - tdead} {1/fs})
Vg3 g3 0 PULSE(0 15 {0.5/fs} 1n 1n {0.5/fs - tdead} {1/fs})
Vg2 g2 0 PULSE(0 15 {0.5/fs} 1n 1n {0.5/fs - tdead} {1/fs})
Vg4 g4 0 PULSE(0 15 0 1n 1n {0.5/fs - tdead} {1/fs})

* Primary full-bridge switches (ideal)
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=7.5 Vh=0.5)
S1 v1p pa g1 0 SW1
S3 pa v1n g3 0 SW1
S2 v1p pb g2 0 SW1
S4 pb v1n g4 0 SW1

* Primary body diodes
.model DBODY D(Is=1e-12 Rs=0.01 N=1.5 BV=800)
D1 pa v1p DBODY
D3 v1n pa DBODY
D2 pb v1p DBODY
D4 v1n pb DBODY

* Primary resonant tank
Cr1 pa cr1_node {Cr1} ic=0
Lr1 cr1_node prim_a {Lr1} ic=0

* Transformer (coupled inductors + magnetizing inductance)
Lm prim_a prim_b {Lm} ic=0

.param Lp_xfmr=10m
.param Ls_xfmr={Lp_xfmr/(n*n)}
Lp prim_a prim_b {Lp_xfmr}
Ls sec_a sec_b {Ls_xfmr}
K1 Lp Ls 1

* Primary return
Rprim_ret prim_b pb 0.001

* Secondary resonant tank
Lr2 sec_a lr2_node {Lr2} ic=0
Cr2 lr2_node sec_cr2 {Cr2} ic=0
Rsec_cr2 sec_cr2 sa 0.001

* Secondary return
Rsec_ret sec_b sb 0.001

* Secondary full-bridge (as synchronous rectifier in forward mode)
* In forward mode, driven synchronously with secondary voltage
Vg5 g5 0 PULSE(0 15 0 1n 1n {0.5/fs - tdead} {1/fs})
Vg7 g7 0 PULSE(0 15 {0.5/fs} 1n 1n {0.5/fs - tdead} {1/fs})
Vg6 g6 0 PULSE(0 15 {0.5/fs} 1n 1n {0.5/fs - tdead} {1/fs})
Vg8 g8 0 PULSE(0 15 0 1n 1n {0.5/fs - tdead} {1/fs})

.model SW2 SW(Ron=0.01 Roff=1Meg Vt=7.5 Vh=0.5)
S5 v2p sa g5 0 SW2
S7 sa v2n g7 0 SW2
S6 v2p sb g6 0 SW2
S8 sb v2n g8 0 SW2

* Secondary body diodes
.model DBODY2 D(Is=1e-12 Rs=0.005 N=1.5 BV=100)
D5 sa v2p DBODY2
D7 v2n sa DBODY2
D6 sb v2p DBODY2
D8 v2n sb DBODY2

* Output filter and load
Co v2p v2n {Co} ic={V2}
Rload v2p v2n {Rload}

* Primary input capacitor
Cin v1p v1n 10u ic={V1}

.param Co={1/(2*pi*fs*Rload*0.01)}

* Primary ground reference
Rg1 v1n 0 1Meg

* Simulation
.tran {tstep} {tstop} 0 {tstep} uic

.control
run

let tstart = {tstart}
let tstop = {tstop}
meas tran V2_avg avg v(v2p,v2n) from=tstart to=tstop
meas tran V2_ripple pp v(v2p,v2n) from=tstart to=tstop
meas tran ILr1_rms rms i(Lr1) from=tstart to=tstop
meas tran ILr1_max max i(Lr1) from=tstart to=tstop
meas tran ILm_peak max i(Lm) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== CLLC Resonant Converter Simulation Results ==="
print V2_avg V2_ripple
print ILr1_rms ILr1_max ILm_peak
let Pin = -Iin_avg * {V1}
let Pout = V2_avg * V2_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata cllc_results.csv v(v2p,v2n) v(pa,pb) i(Lr1) i(Lm) i(Lr2)
quit
.endc

.end
```

**Note on CLLC simulation**: The secondary-side gate drives shown above assume forward-mode operation with the secondary switches driven as synchronous rectifiers. For reverse-mode simulation, swap the roles: drive the secondary full-bridge as the inverter and the primary as the rectifier. For proper bidirectional control simulation, the SR gate timing should be derived from the secondary voltage/current waveforms rather than fixed pulse sources.

## Design Example

Specifications: V1 = 400V (DC bus), V2 = 48V (battery), P = 3.3 kW bidirectional, fr = 150 kHz

| Parameter | Value | Notes |
|-----------|-------|-------|
| n = Np/Ns | 8.33 | 400/48, round to integer Np/Ns ratio |
| m = Lm/Lr1 | 6 | Moderate gain range |
| Q | 0.35 | At full load |
| Rac | 8*n^2*RL/(pi^2) = 8*(8.33)^2*(48^2/3300)/pi^2 | = 37.7 ohm |
| Cr1 | 1/(2*pi*150e3*0.35*37.7) = 81 nF | Use 82 nF film or C0G |
| Lr1 | 1/((2*pi*150e3)^2*82e-9) = 13.7 uH | Discrete or leakage |
| Lm | 6*13.7 = 82 uH | Set by transformer gap |
| Lr2 | 13.7/8.33^2 = 0.197 uH | Secondary leakage |
| Cr2 | 82e-9*8.33^2 = 5.7 uF | Ceramic bank |
| fs range | ~120-200 kHz | Below and above fr |
| Efficiency target | >96% both directions | |

## CLL Resonant Converter Theory (from Kazimierczuk Ch18)

Reference: Kazimierczuk & Czarkowski, "Resonant Power Converters" 2nd ed., Wiley-IEEE 2011, Chapter 18.

The CLL resonant converter is the foundation upon which the CLLC bidirectional topology is built. In Kazimierczuk's framework, the CLL is identical in structure to what the industry calls the LLC converter: a series capacitor C, a series inductor L1 (= Lr), and a parallel inductor L2 (= Lm) with the load across L2.

### CLL Circuit Topology

The CLL resonant converter consists of:
- Class D half-bridge or full-bridge inverter (S1, S2 with body diodes)
- Resonant circuit: C (series) - L1 (series) - L2 (parallel with load)
- Voltage-driven rectifier (half-wave, center-tapped, or bridge)

The inductor L1 can be replaced by a transformer to achieve isolation, in which case the leakage inductance serves as L1 and the magnetizing inductance serves as L2.

### Resonant Frequencies and Key Parameters

```
f0 = 1 / (2*pi*sqrt(C * (L1+L2)))        Lower corner frequency
fr = 1 / (2*pi*sqrt(C * L1))              Upper series resonant frequency
A = L1 / L2                               Inductance ratio (= Lr/Lm = 1/m)
L = L1 + L2                               Total inductance
QL = Ri / (omega0 * L)                    Loaded quality factor at f0
Z0 = sqrt(L/C)                            Characteristic impedance
```

Rectifier input resistance (depends on rectifier type):
```
Ri = pi^2*RL / 2          (half-wave)
Ri = pi^2*n^2*RL / 2      (center-tapped)
Ri = pi^2*n^2*RL / 8      (bridge)
```
(These include eta_R correction factors for lossy diodes.)

### DC Voltage Transfer Function (Lossless)

**Half-bridge CLL converter** (all three rectifier types):

| Rectifier | Mv (lossless) |
|---|---|
| Half-wave | 2 / (n*pi^2 * sqrt((1+A)^2*(1-(f/f0)^2)^2 + (1/QL*(f/f0 - A*f0/((1+A)*f)))^2)) |
| Center-tapped | 4 / (n*pi^2 * sqrt(...same...)) |
| Bridge | 4 / (n*pi^2 * sqrt(...same...)) |

**Full-bridge CLL converter**: Mv is 2x the half-bridge value for each rectifier type.

### Load-Independent Operating Point

The Mv becomes independent of load (QL) at the normalized frequency:
```
f/f0 = sqrt(1 + 1/A) = sqrt(1 + L2/L1)
```

In absolute terms, this equals fr = 1/(2*pi*sqrt(C*L1)), the series resonant frequency.

At this frequency, the tank presents an **inductive** impedance to the switches. This is the primary advantage over the SPRC (LCC), where the load-independent point occurs in the capacitive region.

### Efficiency Model

**Inverter efficiency**:
```
eta_I = 1 / (1 + pi^2*r / (Ri * ((1+A)^2*(1-(f/f0)^2)^2 + (1/QL*(f/f0 - A*f0/((1+A)*f)))^2)))
```
where r = rDS + rL1 + rL2 + rC (total parasitic resistance).

**Rectifier efficiency** (center-tapped example):
```
eta_R = 1 / (1 + VF/Vo + (RF + rLF)/RL + act*pi^2/(4*RL))
```

**Optimal QL for maximum efficiency**: QL = (f/f0) / 2.

### Component Stress Equations

```
Peak switch current:   ISM = Im = (2*Vi*Mvr) / (pi*Ri) * sqrt(1 + [QL*(f/f0)*(1+A)]^2)
Peak switch voltage:   VSM = Vi  (half-bridge), Vi (full-bridge, per switch)

Peak L1 voltage:       VL1m = omega * L1 * Im
Peak L2 voltage:       VL2m = 2*Vi / (pi^2 * sqrt((1+A)^2*(1-(f/f0)^2)^2 + (1/QL*(f/f0 - A*f0/((1+A)*f)))^2))
Peak C voltage:        VCm = Im / (omega * C)

Diode peak current:    IDM = pi*Io  (half-wave), Io (center-tapped/bridge)
Diode peak voltage:    VDM = n*Vo*pi (half-wave), 2*(Vo+VF) (center-tapped), Vo+VF (bridge)
```

### Operating Mode Boundaries

1. **f > fr (above series resonance)**: inductive load, ZVS guaranteed, sinusoidal current, preferred operation
2. **f0 < f < fr (between resonances)**: can be inductive or capacitive depending on load. Boundary is load-dependent.
3. **f < f0 (below corner frequency)**: capacitive load, ZCS operation, AVOID for MOSFETs

The boundary between capacitive and inductive loads at frequencies between f0 and fr depends on the loaded quality factor QL. The exact boundary requires numerical solution of the tank impedance phase equation.

### Safety Conditions

- **Short circuit (RL = 0)**: L2 is shorted, circuit becomes series C-L1. At f = fr, impedance = r only. **DANGEROUS** -- must have overcurrent protection.
- **Open circuit (RL = infinity)**: full L1+L2 resonates with C. At f = f0, excessive current through tank. **DANGEROUS** -- control must prevent operation near f0.
- **Safe operating range**: always maintain fs significantly above peak gain frequency, and implement both frequency clamping and overcurrent shutdown.

### Design Example (from Kazimierczuk Example 18.1)

Specifications: Vi = 250V, Vo = 40V, Io = 0-2A, f0 = 100 kHz, eta = 90%

| Parameter | Value |
|---|---|
| A = L1/L2 | 1 |
| QL at full load | 0.2 |
| f/f0 | 1.471 |
| f (switching) | 147.1 kHz |
| C | 3.1 nF |
| L1 | 403.5 uH |
| L2 | 403.5 uH |
| Z0 | 507 ohm |
| ISM | 1.64 A |
| VL1m | 612 V |
| VL2m | 143.3 V |
| VCm | 572 V |
| IDM | pi * Io = 6.28 A (half-wave) |
| VDM | n * Vo = 125.7 V |

### Summary of CLL Properties (from Kazimierczuk)

- Mv independent of load at fr = 1/(2*pi*sqrt(C*L1)), where load is inductive (ZVS guaranteed)
- Efficiency decreases with increasing RL at light loads
- Boundary between capacitive/inductive is load-dependent
- NOT safe at short circuit near fr, NOT safe at open circuit near f0
- The transformer leakage inductance is absorbed into L1; magnetizing inductance = L2
- CLL and LLC are the same topology with different nomenclature conventions
