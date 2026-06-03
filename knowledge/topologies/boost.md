---
description: Design a Boost (step-up) converter from specs, calculate components, generate ngspice netlist
---

# Boost Converter Design

## When to Use
- Output voltage higher than input voltage (Vout > Vin)
- Non-isolated application
- High efficiency needed (typically 90-97%)
- Any power level

## Circuit Description
The Boost converter steps up an input voltage to a higher output voltage. Energy is transferred to the output when the FET is NOT conducting. The inductor current flows continuously from the input through either the FET to ground (during t1) or through the diode to the output (during t2). Input current is continuous (advantage for EMI), but output current is pulsed (needs good output capacitor).

Components: Q1 (low-side FET), D1 (boost diode), L1 (inductor), Ci (input cap), Co (output cap).

For synchronous Boost: set Vf = 0V in all equations and replace D1 with a high-side FET Q2.

## Design Procedure

### Step 1: Duty Cycle
```
D = 1 - Vin/Vout (ideal)
D = (Vout + Vf - Vin) / (Vout + Vf)
```
Where Vf = diode forward voltage (0 for synchronous).

Note: D is calculated at nominal Vin. Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Choose Current Ripple Ratio
Target r = 0.4 (optimal tradeoff per Maniktala).
```
r = deltaI / Iin
```
Where deltaI = peak-to-peak inductor current ripple, Iin = average input (inductor) current.

### Step 3: Calculate Inductance
```
L = Vin_min * D_max / (r * Iin * fsw)
```
Or equivalently:
```
L = Vin_min * (Vout - Vin_min) / (r * Iin * Vout * fsw)
```
Worst-case (highest ripple) is at Vin_MIN for Boost (highest duty cycle).

### Step 4: Verify CCM/DCM Boundary
CCM guaranteed when:
```
Iout > deltaI/2 * (1 - D)
```
For light loads, converter enters DCM.

### Step 5: Right Half Plane Zero (RHPZ)
The Boost converter has a RHPZ that limits control bandwidth:
```
frhpz = Vout * (1 - D)^2 / (2 * pi * L1 * Iout)
```
Crossover frequency of the control loop must be well below frhpz (typically fc < frhpz/3).

### Step 6: Component Stresses

**Inductor:**
```
I_avg = Iin = Iout * Vout / (Vin * efficiency)
I_peak = Iin + deltaI/2
I_valley = Iin - deltaI/2
V_L_max = Vin (during t1)
V_L_min = Vin - Vout - Vf (during t2, negative)
```

**FET Q1:**
```
V_Q1_max = Vout + Vf
I_Q1_avg = Iin * D
I_Q1_rms = Iin * sqrt(D) * sqrt(1 + r^2/12)
```

**Diode D1:**
```
V_D1_max = Vout (reverse voltage)
I_D1_avg = Iout
```

**Input Capacitor Ci:**
```
I_Ci_rms = Iin * r / (2 * sqrt(3))
```
(Input current is continuous, so Ci stress is low -- advantage of Boost)

**Output Capacitor Co:**
```
I_Co_rms = Iout * sqrt(D / (1 - D)) * sqrt(1 + r^2/12)
```
(Output current is pulsed -- Co stress is high, needs low ESR)
```
Vripple = Iout * D / (Co * fsw) + Iout * ESR / (1 - D)
```

### Step 7: Select Components
- **FET**: V_DS rating >= 1.5 * V_Q1_max; I_D rating > I_peak; low Rds_on
- **Diode**: V_R rating >= 1.3 * Vout; Schottky preferred for low Vf; fast recovery essential
- **Inductor**: L value from Step 3; current rating > I_peak; low DCR
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating > I_Ci_rms
- **Output cap**: voltage rating >= 1.5 * Vout; ESR low enough for ripple spec; ripple current rating > I_Co_rms

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = Vin * t1 / L1
RHPZ: frhpz = Vout * (1 - D)^2 / (2 * pi * L1 * Iout)
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf - Vin) / (Vout + Vf)
t2 = 1/fsw - t1
Iin = Iout * (Vout + Vf) / Vin
Imin = Iin - Iripple/2
Imax = Iin + Iripple/2
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf - Vin) / (fsw * Vin^2))
t2 = t1 * (Vout + Vf) / (Vout + Vf - Vin) - t1
t3 = 1/fsw - t1 - t2
Imin = 0A
Imax = Vin * t1 / L1
```

### Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * fsw
V_L1_min = Vin - Vout - Vf
V_L1_max = Vin
```

### FET Q1
```
I_Q1_avg = (Imin + Imax)/2 * t1 * fsw
V_Q1_max = Vout + Vf
V_Q1_t3 = Vin (DCM)
```

### Diode D1
```
I_D1_avg = (Imin + Imax)/2 * t2 * fsw
V_D1_min = -Vout
V_D1_max = Vf
V_D1_t3 = Vin - Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_min = -Imax + Iin
I_Ci_max = -Imin + Iin
```

### Output Capacitor Co
```
I_Co_t1 = -Iout
I_Co_min_t2 = Imin - Iout
I_Co_max_t2 = Imax - Iout
```

## Output Ripple Voltage (from Basso Ch1)

### Capacitive contribution:
For boost, the output cap charges only during t2 (diode conduction). During t1, output cap alone supplies the load.
```
deltaV_cap = Iout * D / (C * fsw)
```
This is LARGER than buck because the full load current comes from the cap during t1.

### ESR contribution:
```
deltaV_ESR = (Imax - (-Iout)) * R_ESR ≈ (Imax + Iout) * R_ESR
```
At the diode turn-on edge, there's a step change in capacitor current.

### Design implication:
Boost output caps see much more stress than buck. The capacitor current is pulsed (not triangular like buck). Use low-ESR capacitors and/or paralleled caps.

## RMS Current Formulas (from Basso Appendix 1D)

### CCM:
**Inductor RMS:** Same as buck: I_L_rms = sqrt(I_avg^2 + deltaI^2/12) where I_avg = Iout/(1-D)

**Switch RMS:** I_sw_rms = I_L_avg * sqrt(D) * sqrt(1 + r^2/12) where I_L_avg = Iout/(1-D)

**Diode RMS:** I_D_rms = I_L_avg * sqrt(1-D) * sqrt(1 + r^2/12)

**Output Capacitor RMS:**
```
I_Cout_rms = sqrt(Iout^2 * D/(1-D) + deltaI^2*(1-D)/12)
```
Much larger than buck due to pulsed current!

**Input Capacitor RMS:**
```
I_Cin_rms = deltaI / (2*sqrt(3))
```
Small because input current is continuous (triangular ripple only).

### DCM:
Same structure as buck DCM with D1, D2, Ip substitutions.

## Small-Signal Transfer Functions (from Basso Appendix 2A)

### Key difference from Buck: RIGHT HALF PLANE ZERO (RHPZ)
```
f_RHPZ = R*(1-D)^2 / (2*pi*L)
```
This limits achievable bandwidth! Crossover MUST be well below f_RHPZ (typically fc < fRHPZ/3).

### Voltage-Mode CCM:
```
H(s) = Vout/(Vpeak*(1-D)) * (1 + s*R_ESR*C) * (1 - s*L/(R*(1-D)^2)) / (1 + s/omega_0/Q + s^2/omega_0^2)
```
- LC double pole (like buck) PLUS RHPZ
- Very difficult to compensate at high D (low Vin)

### Current-Mode CCM:
```
H(s) ≈ single dominant pole + RHPZ + ESR zero + subharmonic pole
```
- Easier than voltage mode but RHPZ still limits bandwidth
- Slope compensation required if D > 0.5

### Compensation:
- **VM CCM**: Type 3 required, but limited by RHPZ
- **CM CCM**: Type 2, limited by RHPZ
- **DCM**: Type 1 or 2 (no RHPZ in useful range, first-order system)

## Practical Design Notes (from Basso)
- Boost is an indirect energy transfer converter: energy stored in L during t1, released during t2
- Response to load steps is inherently slower than buck due to store-release cycle
- The RHPZ models this delay: trying to increase duty cycle temporarily REDUCES output
- For wide Vin range, consider two-phase interleaved boost to reduce ripple
- Input current is continuous (good for EMI), output current is pulsed (bad for output ripple)

## Ngspice Netlist Template

```spice
* Boost Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Boost Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param L={L}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* Inductor: Vin -> L1 -> switch node
L1 in lx {L} ic=0
RL lx sw 0.01

* PWM gate drive
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Low-side switch: switch node to GND (ideal MOSFET model)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 sw gate 0 0 NMOS W=100u L=1u
* Alternative: voltage-controlled switch
* .model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
* S1 sw 0 gate 0 SW1

* Boost diode: switch node to output
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 sw out DSCHOTTKY

* Output capacitor
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

echo "=== Boost Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata boost_results.csv v(out) v(sw) i(L1) i(Vin)
quit
.endc

.end
```

## Synchronous Boost Variant

Replace diode D1 with a high-side FET Q2 driven complementary to Q1 (with dead time):

```spice
* Complementary PWM with dead time
Vpwm_lo gate_lo 0 PULSE(0 10 0 1n 1n {duty/fsw - deadtime} {1/fsw})
Vpwm_hi gate_hi 0 PULSE(10 0 {deadtime} 1n 1n {duty/fsw + deadtime} {1/fsw})

* Low-side and high-side FETs
.model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
S1 sw 0 gate_lo 0 SW1
S2 out sw gate_hi 0 SW1
D_body sw out DSCHOTTKY
```
