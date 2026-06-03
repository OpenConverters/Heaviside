---
description: Design a Cuk converter from specs, calculate components, generate ngspice netlist
---

# Cuk Converter Design

## When to Use
- Output voltage is NEGATIVE (inverted polarity relative to input)
- Capacitive energy transfer via coupling capacitor C1
- Continuous input AND output currents needed (low EMI)
- Non-isolated application
- Can step up or step down voltage magnitude

## Circuit Description
The Cuk converter produces a negative output voltage using two inductors (L1 on input, L2 on output) and a coupling capacitor (C1) for energy transfer. Unlike Buck-Boost, both input and output currents are continuous (non-pulsating), which is a significant advantage for EMI filtering.

Components: Q1 (FET, low-side to ground), D1 (diode), L1 (input inductor), L2 (output inductor), C1 (coupling capacitor), Ci (input cap), Co (output cap).

The coupling capacitor C1 voltage in steady state equals Vin - Vout (note Vout is negative, so VC1 = Vin + |Vout|).

## Design Procedure

### Step 1: Duty Cycle
```
D = |Vout| / (Vin + |Vout|)
```
Note: Vout is negative. At ideal efficiency, D = |Vout|/(Vin + |Vout|).

Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Right-Half-Plane Zero (RHPZ)
The Cuk converter has a RHPZ that limits control bandwidth:
```
f_rhpz = 1/(2*pi) * sqrt((1-D) / (L1 * C1))
```
Crossover frequency must be well below f_rhpz (typically fc < f_rhpz/3).

### Step 3: Calculate Inductances
```
L1 = Vin * D / (r1 * Iin_avg * fsw)
L2 = |Vout| * (1 - D) / (r2 * Iout * fsw)
```
Where r1, r2 = ripple ratios (target 0.4 each).

### Step 4: Coupling Capacitor C1
```
VC1 = Vin - Vout = Vin + |Vout| (DC steady state)
VC1_ripple = Iin_avg * (1 - D) / (C1 * fsw)
```
Choose C1 for acceptable VC1_ripple (typically < 5% of VC1).

### Step 5: Component Stresses

**Inductor L1 (input):**
```
IL1_avg = Iin_avg = (|Vout| - Vf) * Iout / Vin
IL1_ripple = Vin * t1 / L1
IL1_peak = IL1_avg + IL1_ripple/2
```

**Inductor L2 (output):**
```
IL2_avg = Iout (current flows in negative direction)
IL2_ripple = Vin * t1 / L2
IL2_peak = Iout + IL2_ripple/2
```

**FET Q1:**
```
VQ1_max = Vin - Vout + Vf = Vin + |Vout| + Vf
IQ1 = IL1 + IL2 combined during on-time
IQ1_peak = IL1_peak + IL2_peak
```

**Diode D1:**
```
VD1_max = Vf (forward drop during conduction)
VD1_reverse = Vin + |Vout| (blocking voltage)
VD1_t3 = Vout (negative, DCM)
```

**Coupling Capacitor C1:**
```
VC1 = Vin + |Vout| (DC)
IC1_rms is significant - use film or high-ripple ceramic
Voltage rating >= 1.5 * (Vin_max + |Vout|)
```

### Step 6: Select Components
- **FET**: V_DS rating >= 1.5 * (Vin + |Vout| + Vf); low Rds_on
- **Diode**: V_R rating >= 1.3 * (Vin + |Vout|); Schottky preferred
- **L1**: current rating > IL1_peak; low DCR
- **L2**: current rating > IL2_peak; low DCR
- **C1**: voltage rating >= 1.5 * (Vin + |Vout|); low ESR film or ceramic
- **Input cap**: voltage rating >= 1.5 * Vin_max
- **Output cap**: voltage rating >= 1.5 * |Vout|; low ESR for ripple

## Complete Equations (from TI Power Topologies Handbook)

### CCM Timing
```
t1 = (1/fsw) * (-Vout + Vf) / (-Vout + Vin + Vf)
t2 = 1/fsw - t1
Imin_L1 = IL1_avg - IL1_ripple/2
Imax_L1 = IL1_avg + IL1_ripple/2
Imin_L2 = Iout - IL2_ripple/2
Imax_L2 = Iout + IL2_ripple/2
```

### General
```
Iin_avg = (Vout - Vf) * Iout / Vin
```
Note: Vout is negative, so Iin_avg is positive (power flows in).

```
IL1_ripple = Vin * t1 / L1
IL2_ripple = Vin * t1 / L2
VC1_ripple = Iin_avg * (1 - D) / (C1 * fsw)
```

### Inductor L1
```
I_L1_avg = Iin_avg
V_L1_max = Vin (during t1)
V_L1_min = Vin - VC1 = Vout (during t2, negative)
```

### Inductor L2
```
I_L2_avg = Iout (negative direction)
V_L2_max = VC1 + Vout = Vin (during t1)
V_L2_min = Vout (during t2)
```

### FET Q1
```
I_Q1_avg = (IL1 + IL2) * D
V_Q1_min = 0V
V_Q1_max = Vin - Vout + Vf = Vin + |Vout| + Vf
```

### Diode D1
```
I_D1_avg = (IL1 + IL2) * (1 - D)
V_D1_min = -(Vin + |Vout|)
V_D1_max = Vf
V_D1_t3 = Vout (DCM, negative)
```

### Coupling Capacitor C1
```
VC1 = Vin - Vout = Vin + |Vout| (DC)
VC1_ripple = Iin_avg * (1 - D) / (C1 * fsw)
```

### Input Capacitor Ci
```
I_Ci_rms is low (continuous input current from L1)
```

### Output Capacitor Co
```
I_Co_min = Imin_L2 - Iout
I_Co_max = Imax_L2 - Iout
```

## Ngspice Netlist Template

```spice
* Cuk Converter
* Vin={Vin}V, Vout={Vout}V (NEGATIVE), Iout={Iout}A, fsw={fsw}Hz

.title Cuk Converter

* Parameters
.param Vin={Vin}
.param Vout_mag={|Vout|}
.param fsw={fsw}
.param duty={D}
.param L1={L1}
.param L2={L2}
.param C1={C1}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={|Vout|/Iout}
.param tstep={1/(fsw*200)}
.param tstop={80/fsw}
.param tstart={40/fsw}

* Input supply
Vin in 0 DC {Vin}

* PWM gate drive
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Input inductor L1
L1 in sw_a {L1} ic=0
RL1 sw_a sw 0.01

* FET Q1 (low-side, between switch node and ground)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 sw gate 0 0 NMOS W=100u L=1u

* Coupling capacitor C1 (connects switch node to diode/L2 node)
C1 sw cap_node {C1} ic={Vin+Vout_mag}

* Diode D1 (from output-side node to ground)
* Note: Cathode at cap_node, anode at ground for correct Cuk polarity
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 0 cap_node DSCHOTTKY

* Output inductor L2 (from cap_node to output)
* Output is NEGATIVE with respect to ground
L2 cap_node lx2 {L2} ic=0
RL2 lx2 out 0.01

* Input capacitor
C_in in 0 {Cin}

* Output capacitor (output is negative)
C_out out 0 {Cout} ic={-Vout_mag*0.9}

* Load (connected to negative output)
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
meas tran IL1_avg avg i(L1) from=tstart to=tstop
meas tran IL1_max max i(L1) from=tstart to=tstop
meas tran IL1_min min i(L1) from=tstart to=tstop
meas tran IL2_avg avg i(L2) from=tstart to=tstop
meas tran IL2_max max i(L2) from=tstart to=tstop
meas tran IL2_min min i(L2) from=tstart to=tstop
meas tran VC1_avg avg v(sw,cap_node) from=tstart to=tstop
meas tran VC1_ripple pp v(sw,cap_node) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Cuk Converter Simulation Results ==="
echo "NOTE: Vout is NEGATIVE (inverted output)"
print Vout_avg Vout_ripple
print IL1_avg IL1_max IL1_min
print IL2_avg IL2_max IL2_min
print VC1_avg VC1_ripple
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata cuk_results.csv v(out) v(sw) v(cap_node) i(L1) i(L2) i(Vin)
quit
.endc

.end
```
