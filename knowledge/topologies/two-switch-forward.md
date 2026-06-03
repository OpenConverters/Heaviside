---
description: Design a Two Switch Forward converter from specs, calculate components, generate ngspice netlist
---

# Two Switch Forward Converter Design

## When to Use
- Isolated DC-DC conversion needed (galvanic isolation)
- Output voltage lower than input voltage (step-down via turns ratio)
- FET voltage stress must be minimized (clamped to Vin -- major advantage)
- Medium to high power (100W-1kW typical)
- Duty cycle limited to < 0.5
- Higher reliability than single-switch (lower voltage FETs, no reset winding)
- Wide input voltage range applications (telecom, industrial)

## Circuit Description
The Two Switch Forward is an isolated Buck derivative with two FETs in a totem-pole configuration. Energy is transferred to the secondary when both FETs Q1 and Q2 are conducting simultaneously. The transformer does not store energy (no air gap needed); it resets during the off-time through the body diodes of both FETs, which clamp the switch voltage to Vin.

Primary side: Q1 (high-side FET, between Vin and transformer primary dot), Q2 (low-side FET, between transformer primary return and ground), D_Q1 (body diode of Q1 or external diode from transformer to Vin), D_Q2 (body diode of Q2 or external diode from ground to transformer).
Secondary side: D1 (rectifier diode), D2 (freewheeling diode), L1 (output inductor), Co (output cap).

The secondary-referred inductance is Ls = Lp / (np/ns)^2.

**Key advantage**: V_Q1_max = V_Q2_max = Vin (clamped by body diodes). No reset winding needed.
**Key limitation**: D < 0.5 (transformer must reset within off-time).

## Design Procedure

### Step 1: Turns Ratio
```
ns/np = (Vout + Vf) / (Vin * D_max)
```
Choose D_max around 0.40-0.45 at Vin_min (leave margin below 0.5).

### Step 2: Duty Cycle
```
D = (Vout + Vf) / (Vin * ns/np)
```
Check at Vin_min (max D) and Vin_max (min D). Ensure D_max < 0.5.

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 4: Calculate Output Inductance
```
L1 = (Vin * ns/np - Vf - Vout) * D / (r * Iout * fsw)
```
Worst-case ripple at Vin_max.

### Step 5: Verify CCM/DCM Boundary
CCM guaranteed when:
```
Iout > deltaI/2 = r * Iout / 2
```
Always true for r < 2.0. For light loads, converter enters DCM.

### Step 6: Demagnetization Time
```
td = t1 (demagnetization through body diodes reflects Vin back)
```
The magnetizing current flows through both body diodes, clamping the primary voltage to -Vin and resetting the core. Dead time after demagnetization:
```
tad = 1/fsw - t1 - td = 1/fsw - 2*t1
```
Must verify tad > 0, i.e., D < 0.5.

### Step 7: Magnetizing Current
```
Imag = Vin * t1 / Lp
```
Lp is the transformer primary magnetizing inductance.

### Step 8: Component Stresses

**Output Inductor L1:**
```
I_avg = Iout
I_peak = Iout * (1 + r/2)
I_valley = Iout * (1 - r/2)
V_L1_max = Vin * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2)
```

**FET Q1 (high-side) and Q2 (low-side):**
```
V_Q1_max = Vin (CLAMPED by body diodes -- major advantage!)
V_Q2_max = Vin (CLAMPED by body diodes -- major advantage!)
I_Q1_avg = I_Q2_avg = (Iout * ns/np + Imag/2) * D
I_Q1_rms = I_Q2_rms = (Iout * ns/np) * sqrt(D) * sqrt(1 + r^2/12)
```
Compare to single-switch: V = 2*Vin. Two-switch halves the voltage stress per FET.

**Rectifier Diode D1:**
```
V_D1_max = Vin_max * ns/np + Vout
I_D1_avg = Iout * D
```

**Freewheeling Diode D2:**
```
V_D2_max = Vin_max * ns/np
I_D2_avg = Iout * (1 - D)
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 9: Select Components
- **FET Q1, Q2**: V_DS >= 1.5 * Vin_max (much lower than single-switch!); standard voltage FETs
- **Demagnetization diodes**: body diodes of Q1/Q2 suffice; external fast diodes optional for efficiency
- **Rectifier D1**: V_R >= 1.3 * V_D1_max; Schottky preferred
- **Freewheeling D2**: V_R >= 1.3 * V_D2_max; Schottky preferred
- **Output inductor L1**: current rating > I_peak; low DCR
- **Transformer T1**: core with no air gap; adequate Lp; proper np:ns ratio (no reset winding needed)
- **Output cap Co**: low ESR for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin * ns/np - Vf - Vout) * t1 / L1
Ls = Lp / (np/ns)^2
Imag = Vin * t1 / Lp
D_max < 0.5
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vin * ns/np)
td = t1 (demagnetization time equals on-time)
tad = 1/fsw - 2*t1 (dead time, must be > 0)
t2 = 1/fsw - t1
D = t1 * fsw
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin * (1/efficiency)
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf) / (fsw * (Vin * ns/np - Vout - Vf) * (Vin * ns/np)))
t2 = t1 * (Vin * ns/np - Vout - Vf) / (Vout + Vf)
td = t1 (demagnetization time)
t3 = 1/fsw - t1 - t2 (inductor freewheeling ends)
Imin = 0A
Imax = (Vin * ns/np - Vf - Vout) * t1 / L1
```
Note: In DCM, verify t1 + td < 1/fsw (transformer must still fully reset).

### Output Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * fsw
V_L1_min = -(Vout + Vf)
V_L1_max = Vin * ns/np - Vf - Vout
V_L1_t3 = 0V (DCM)
```

### FET Q1 and Q2
```
I_Q1_avg = I_Q2_avg = (Imin + Imax)/2 * (ns/np) * t1 * fsw + Imag/2 * D
V_Q1_min = V_Q2_min = 0V
V_Q1_max = V_Q2_max = Vin (CLAMPED!)
V_Q_during_t1 = 0V (both conducting)
V_Q_during_td = Vin (body diodes conducting, clamped)
V_Q_during_tad = Vin (floating, clamped by body diodes)
```

### Rectifier Diode D1
```
I_D1_avg = (Imin + Imax)/2 * t1 * fsw
V_D1_min = -(Vin_max * ns/np + Vout)
V_D1_max = Vf
```

### Freewheeling Diode D2
```
I_D2_avg = (Imin + Imax)/2 * t2 * fsw
V_D2_min = -Vin * ns/np
V_D2_max = Vf
V_D2_t3 = -Vout (DCM)
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Ngspice Netlist Template

```spice
* Two Switch Forward Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz
* Turns ratio np:ns = {np}:{ns}

.title Two Switch Forward Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param np={np}
.param ns={ns}
.param Lp={Lp}
.param L1={L1}
.param Cout={Cout}
.param Cin={Cin}
.param Rload={Vout/Iout}
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* PWM gate drive (both FETs driven simultaneously)
Vpwm_q1 gate_q1 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})
Vpwm_q2 gate_q2 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Switch model
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)
.model DFAST D(Is=1e-14 Rs=0.05 N=1.0 BV=200 TT=20n)

* High-side FET Q1: Vin to transformer primary dot
S1 in pri_dot gate_q1 0 SW1

* Transformer: coupled inductors (no energy storage, k close to 1)
* Primary winding
Lp1 pri_dot pri_bot {Lp} ic=0
* Secondary winding
Ls1 sec_dot sec_bot {Lp*(ns/np)*(ns/np)} ic=0
* Coupling coefficient (close to 1 for forward transformer)
K1 Lp1 Ls1 0.998

* Low-side FET Q2: transformer primary return to ground
S2 pri_bot 0 gate_q2 0 SW1

* Demagnetization path through body diodes (clamp to Vin)
* D_Q1 body diode: transformer primary dot to Vin (high-side clamp)
D_Q1 pri_dot in DFAST
* D_Q2 body diode: ground to transformer primary return (low-side clamp)
D_Q2 0 pri_bot DFAST

* Secondary rectifier diode D1 (conducts during t1)
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 sec_dot rect_out DSCHOTTKY

* Freewheeling diode D2 (conducts during t2)
D2 0 rect_out DSCHOTTKY

* Secondary return to ground
Rsec sec_bot 0 0.001

* Output LC filter
L_out rect_out lx {L1} ic={Iout}
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
meas tran IL_avg avg i(L_out) from=tstart to=tstop
meas tran IL_max max i(L_out) from=tstart to=tstop
meas tran IL_min min i(L_out) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Two Switch Forward Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata two_switch_fwd_results.csv v(out) v(pri_dot) v(pri_bot) i(L_out) i(Vin)
quit
.endc

.end
```
