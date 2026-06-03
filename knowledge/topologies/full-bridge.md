---
description: Design a Full-Bridge converter from specs, calculate components, generate ngspice netlist
---

# Full-Bridge Converter Design

## When to Use
- Isolated DC-DC conversion needed
- High power applications (500W and above)
- Full Vin utilization across transformer (best transformer utilization)
- FET voltage stress = Vin (same as half-bridge, half of push-pull)
- Center-tapped or full-bridge secondary rectification

## Circuit Description
The Full-Bridge converter uses four FETs arranged in an H-bridge configuration. Diagonal pairs (Q1+Q4 and Q2+Q3) switch alternately, applying the full input voltage across the transformer primary in alternating polarity. The secondary uses either a center-tapped configuration with two diodes or a full-bridge rectifier with four diodes, followed by an output LC filter.

Components: Q1, Q2, Q3, Q4 (H-bridge FETs), T1 (transformer), D1, D2 (center-tapped) or D1-D4 (full-bridge rectifier), L1 (output inductor), Ci (input cap), Co (output cap).

The full-bridge is the highest-power isolated topology before resonant converters are needed.

## Design Procedure

### Step 1: Turns Ratio
```
n = ns/np = (Vout + Vf) / (Vin_min * D_max)
```
Where D_max is typically limited to 0.45 per diagonal pair.

### Step 2: Duty Cycle
```
D = (Vout + Vf) / (Vin * ns/np)
```
Each diagonal pair conducts for time t1 per half-cycle. Maximum D per pair = 0.5.

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 4: Calculate Output Inductance
```
L1 = (Vin * ns/np - Vf - Vout) * t1 / (r * Iout)
```

### Step 5: Magnetizing Inductance
```
Imag = Vin * t1 / Lp
```

### Step 6: Component Stresses

**Output Inductor L1:**
```
I_L1_avg = Iout
I_L1_peak = Iout * (1 + r/2)
I_L1_valley = Iout * (1 - r/2)
V_L1_max = Vin * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2, freewheeling)
```

**FET Q1, Q2, Q3, Q4:**
```
VQ_max = Vin
I_Q_avg = Iout * ns/np * D / 2
I_Q_peak = (Iout * (1 + r/2)) * ns/np + Imag
```

**Diode D1, D2 (center-tapped secondary):**
```
VD_max = 2 * Vin * ns/np
I_D_avg = Iout / 2
```

**Diode D1-D4 (full-bridge secondary rectifier):**
```
VD_max = Vin * ns/np
I_D_avg = Iout / 2
```

**Transformer T1:**
```
VNp_min = -Vin
VNp_max = Vin
VNs_max = Vin * ns/np
I_primary_peak = Iout * ns/np + Imag
```

**Input Capacitor Ci:**
```
I_Ci_rms = primary current ripple (pulsed)
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 7: Select Components
- **FETs**: V_DS rating >= 1.5 * Vin_max; 4 FETs required; low Rds_on
- **Rectifier diodes**: center-tapped: V_R >= 1.3 * 2 * Vin_max * ns/np; full-bridge: V_R >= 1.3 * Vin_max * ns/np
- **Transformer**: single primary, center-tapped or single secondary
- **Output inductor**: L value from Step 4; current rating > I_L1_peak
- **Input cap**: voltage rating >= 1.5 * Vin_max; high ripple current rating
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin * ns/np - Vf - Vout) * t1 / L1
Imag = Vin * t1 / Lp
```

### CCM Timing
```
t1 = 1/(2*fsw) * (Vout + Vf) / (Vin * ns/np)
t2 = 1/(2*fsw) - t1
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin (ideal, lossless)
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf) / (fsw * (Vin*ns/np - Vout - Vf) * Vin * ns/np))
t2 = t1 * (Vin*ns/np - Vout - Vf) / (Vout + Vf)
t3 = 1/(2*fsw) - t1 - t2
Imin = 0A
Imax = (Vin * ns/np - Vf - Vout) * t1 / L1
```

### Output Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * 2 * fsw
V_L1_max = Vin * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2)
V_L1_t3 = 0V (DCM)
```

### FET Q1, Q2, Q3, Q4
```
I_Q_avg = ((Imin + Imax)/2 * ns/np + Imag/2) * t1 * fsw
V_Q_min = 0V
V_Q_max = Vin
```

### Diode D1, D2 (center-tapped)
```
I_D_avg = (Imin + Imax)/2 * t1 * fsw
V_D_min = -(2 * Vin * ns/np)
V_D_max = Vf
V_D_t3 = -Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_t1 = -(Iout * ns/np + Imag) + Iin_avg
I_Ci_t2 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Ngspice Netlist Template

```spice
* Full-Bridge Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Full-Bridge Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param np={np}
.param ns={ns}
.param L={L}
.param Lp={Lp}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param deadtime=100n
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* H-Bridge switching
* Leg A: Q1 (high-side), Q3 (low-side)
* Leg B: Q2 (high-side), Q4 (low-side)
* Diagonal pairs: Q1+Q4 conduct together, Q2+Q3 conduct together

* Gate drives for diagonal pair Q1+Q4 (first half-cycle)
Vpwm_Q1 gate_Q1 0 PULSE(0 10 {deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})
Vpwm_Q4 gate_Q4 0 PULSE(0 10 {deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})

* Gate drives for diagonal pair Q2+Q3 (second half-cycle, 180 deg shifted)
Vpwm_Q2 gate_Q2 0 PULSE(0 10 {1/(2*fsw)+deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})
Vpwm_Q3 gate_Q3 0 PULSE(0 10 {1/(2*fsw)+deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})

* FET switch model
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)

* Leg A
S_Q1 in pri_a gate_Q1 0 SW1
S_Q3 pri_a 0 gate_Q3 0 SW1

* Leg B
S_Q2 in pri_b gate_Q2 0 SW1
S_Q4 pri_b 0 gate_Q4 0 SW1

* Body diodes
.model DBODY D(Is=1e-10 Rs=0.05 N=1.2 BV=600)
D_body_Q1 pri_a in DBODY
D_body_Q3 0 pri_a DBODY
D_body_Q2 pri_b in DBODY
D_body_Q4 0 pri_b DBODY

* Transformer: primary between pri_a and pri_b
* Center-tapped secondary
Lp1 pri_a pri_b {Lp} ic=0
Ls1 sec1 ct_sec {Lp*(ns/np)^2} ic=0
Ls2 sec2 ct_sec {Lp*(ns/np)^2} ic=0
K1 Lp1 Ls1 0.998
K2 Lp1 Ls2 0.998

* Secondary rectifier diodes (center-tapped)
.model DFAST D(Is=1e-5 Rs=0.03 N=1.05 BV=200)
D_rect1 ct_sec sec1 DFAST
D_rect2 ct_sec sec2 DFAST

* Output LC filter
L1 ct_sec lx {L} ic=0
RL lx out 0.01
C_out out 0 {Cout} ic={Vout*0.9}

* Load
R_load out 0 {Rload}

* Simulation
.tran {tstep} {tstop} 0 {tstep} uic

.control
run

* Steady-state measurements
let tstart = {tstart}
let tstop = {tstop}
meas tran Vout_avg avg v(out) from=tstart to=tstop
meas tran Vout_ripple pp v(out) from=tstart to=tstop
meas tran IL_avg avg i(L1) from=tstart to=tstop
meas tran IL_max max i(L1) from=tstart to=tstop
meas tran IL_min min i(L1) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Full-Bridge Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata fullbridge_results.csv v(out) v(pri_a) v(pri_b) i(L1) i(Vin)
quit
.endc

.end
```

## Full-Bridge Secondary Rectifier Variant

Replace center-tapped secondary with full-bridge rectifier (4 diodes, single secondary winding):

```spice
* Single secondary winding (not center-tapped)
Ls1 sec_a sec_b {Lp*(ns/np)^2} ic=0
K1 Lp1 Ls1 0.998

* Full-bridge rectifier
D_rect1 sec_a rect_pos DFAST
D_rect2 sec_b rect_pos DFAST
D_rect3 rect_neg sec_a DFAST
D_rect4 rect_neg sec_b DFAST

* Output LC filter (from rect_pos, return to rect_neg)
L1 rect_pos lx {L} ic=0
RL lx out 0.01
C_out out rect_neg {Cout} ic={Vout*0.9}
R_load out rect_neg {Rload}
```

Advantages of full-bridge rectifier: no center tap needed, lower diode voltage stress (Vin*ns/np vs 2*Vin*ns/np). Disadvantage: 2 diode drops in conduction path.

## Design Notes

- The full-bridge topology provides the best transformer utilization of all hard-switched isolated topologies.
- At power levels above 1kW, consider the phase-shifted full-bridge (PSFB) variant for ZVS and higher efficiency.
- FET gate drive: high-side FETs (Q1, Q2) require bootstrap or isolated gate drivers.
- Dead time between diagonal pairs is critical to prevent shoot-through in each leg.
