---
description: Design an Inverting Buck-Boost converter from specs, calculate components, generate ngspice netlist
---

# Inverting Buck-Boost Converter Design

## When to Use
- Output voltage is NEGATIVE (inverted polarity relative to input)
- Output magnitude can be higher or lower than input voltage
- Non-isolated application (functionally equivalent to a flyback without isolation)
- When galvanic isolation is not required but voltage inversion is

## Circuit Description
The Inverting Buck-Boost converts a positive input voltage to a negative output voltage. Energy is stored in the inductor when the FET is conducting (t1) and transferred to the output when the FET is NOT conducting (t2). The output is inverted in polarity relative to the input.

Components: Q1 (switch FET), D1 (freewheeling diode), L1 (inductor), Ci (input cap), Co (output cap).

**Key characteristics (from Maniktala):**
- Switch stress is Vin + |Vout| (higher than Buck or Boost alone)
- Both input and output currents are pulsed (worst EMI of the basic topologies)
- SEPIC is preferred when a positive output is needed
- Has a Right-Half-Plane Zero (RHPZ) that limits control bandwidth

For synchronous Inverting Buck-Boost: set Vf = 0V in all equations.

## Design Procedure

### Step 1: Duty Cycle
```
D = |Vout| / (Vin + |Vout|)
```
Where Vout is negative. Ideal case (Vf = 0).

Note: D is calculated at nominal Vin. Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Choose Current Ripple Ratio
Target r = 0.4 (optimal tradeoff per Maniktala).
```
r = deltaI / I_L_avg
```
Where deltaI = peak-to-peak inductor current ripple.

### Step 3: Calculate Inductance
```
L = Vin_max * D_min / (r * I_L_avg * fsw)
```
Or equivalently:
```
L = Vin * D / (deltaI * fsw)
```
Worst-case (highest ripple) is at Vin_max for the Inverting Buck-Boost.

### Step 4: Verify CCM/DCM Boundary
CCM guaranteed when:
```
I_L_avg > deltaI/2
```
For light loads, converter enters DCM.

### Step 5: Component Stresses

**Inductor:**
```
I_avg = -Iout * (Vin + Vf - Vout) / Vin
I_peak = I_avg + Iripple/2
I_valley = I_avg - Iripple/2
V_L_max = Vin (during t1)
V_L_min = Vout - Vf (during t2, negative)
```

**FET Q1:**
```
V_Q1_max = Vin + Vf - Vout = Vin + Vf + |Vout|
I_Q1_avg = (Imin + Imax)/2 * t1 * fsw
```

**Diode D1:**
```
V_D1_min = Vout - Vin (negative, reverse bias)
V_D1_t3 = Vout (negative, DCM idle interval)
I_D1_avg = (Imin + Imax)/2 * t2 * fsw
```

**Input Capacitor Ci:**
```
I_Ci_rms: pulsed current (high stress, similar to Boost input)
```

**Output Capacitor Co:**
```
I_Co_rms: pulsed current (high stress, similar to Buck output)
Vripple = deltaI / (8 * Co * fsw) + deltaI * ESR
```

### Step 6: Select Components
- **FET**: V_DS rating >= 1.5 * V_Q1_max = 1.5 * (Vin + |Vout|); low Rds_on
- **Diode**: V_R rating >= 1.3 * (Vin + |Vout|); Schottky preferred for low Vf
- **Inductor**: L value from Step 3; current rating > I_peak; low DCR
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating sufficient
- **Output cap**: voltage rating >= 1.5 * |Vout|; ESR low enough for ripple spec

### Step 7: RHPZ Frequency
The Right-Half-Plane Zero limits the achievable crossover frequency of the control loop:
```
frhpz = Vout * (1 - D)^2 / (2 * pi * D * L1 * Iout)
```
Note: Vout and Iout are negative values. The crossover frequency must be set well below frhpz (typically fc < frhpz / 3).

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = Vin * t1 / L1
```

### CCM Timing
```
t1 = (1/fsw) * (-Vout + Vf) / (-Vout + Vf + Vin)
t2 = 1/fsw - t1
Imin = -Iout * (Vin + Vf - Vout) / Vin - Iripple/2
Imax = -Iout * (Vin + Vf - Vout) / Vin + Iripple/2
Iin = Vout * Iout / Vin + (Vf/Vin) * (Imin + Imax)/2 * t2 * fsw
```
Note: Vout and Iout are NEGATIVE values in these equations.

### DCM Timing
```
t1 = sqrt(-2 * Iout * L1 * (-Vout + Vf) / (fsw * Vin^2))
t2 = t1 * (-Vout + Vin + Vf) / (-Vout + Vf) - t1
t3 = 1/fsw - t1 - t2
Imin = 0A
Imax = Vin * t1 / L1
```

### Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * fsw
V_L1_min = Vout - Vf (negative)
V_L1_max = Vin
V_L1_t3 = 0V (DCM)
```

### FET Q1
```
I_Q1_avg = (Imin + Imax)/2 * t1 * fsw
V_Q1_min = 0V
V_Q1_max = Vin + Vf - Vout = Vin + Vf + |Vout|
V_Q1_t3 = Vin (DCM)
```

### Diode D1
```
I_D1_avg = (Imin + Imax)/2 * t2 * fsw
V_D1_min = Vout - Vin (negative)
V_D1_max = Vf
V_D1_t3 = Vout (negative, DCM)
```

### Input Capacitor Ci
```
I_Ci_min_t1 = -Imax + Iin_avg
I_Ci_max_t1 = -Imin + Iin_avg
I_Ci_t2_t3 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Imin - (-Iout) = Imin + Iout
I_Co_max = Imax - (-Iout) = Imax + Iout
```

## Ngspice Netlist Template

```spice
* Inverting Buck-Boost Converter
* Vin={Vin}V, Vout={Vout}V (negative), Iout={Iout}A (negative), fsw={fsw}Hz

.title Inverting Buck-Boost Converter

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

* PWM gate drive
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Switch FET Q1 (between input and inductor)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 in gate sw 0 NMOS W=100u L=1u
* Alternative: voltage-controlled switch
* .model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
* S1 in sw gate 0 SW1

* Freewheeling diode D1 (cathode to switch node, anode to output)
* Output is NEGATIVE, so diode conducts from out_neg to sw
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 out sw DSCHOTTKY

* Inductor (switch node to ground)
L1 sw lx {L} ic=0
RL lx 0 0.01

* Input capacitor
C_in in 0 {Cin}

* Output capacitor (negative output rail to ground)
C_out out 0 {Cout} ic={Vout*0.9}

* Load (Rload is negative/positive depending on convention; use absolute value)
* Output node "out" is NEGATIVE relative to ground
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

echo "=== Inverting Buck-Boost Converter Simulation Results ==="
echo "NOTE: Vout is NEGATIVE (inverted polarity)"
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata buck_boost_results.csv v(out) v(sw) i(L1) i(Vin)
quit
.endc

.end
```
