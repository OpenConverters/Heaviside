---
description: Design a Zeta converter from specs, calculate components, generate ngspice netlist
---

# Zeta Converter Design

## When to Use
- Input voltage can be above or below output voltage (step up/down)
- Non-inverting output required (unlike Cuk)
- Similar to SEPIC but with switch on input side
- Energy transferred when switch IS conducting (unlike SEPIC)
- Non-isolated application
- Uses two inductors and a coupling capacitor

## Circuit Description
The Zeta converter steps up or steps down while maintaining positive, non-inverting output. It uses two inductors (L1 on input, L2 on output) and a coupling capacitor (C1). The topology is the dual of SEPIC: while SEPIC has the diode on the output and the FET feeding the coupling cap, Zeta has the FET on the input side and the diode feeding the output inductor.

Components: Q1 (high-side FET), D1 (output diode), L1 (input inductor), L2 (output inductor), C1 (coupling capacitor), Ci (input cap), Co (output cap).

The coupling capacitor C1 DC voltage equals -Vout in steady state.

## Design Procedure

### Step 1: Duty Cycle
```
D = Vout / (Vin + Vout)
```
Same as SEPIC. For Vin < Vout, D > 0.5 (boost mode). For Vin > Vout, D < 0.5 (buck mode).

Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Calculate Inductances
```
L1 = Vin * D / (r1 * Iin_avg * fsw)
L2 = Vin * D / (r2 * Iout * fsw)
```
Where r1, r2 = ripple ratios (target 0.4 each).

Or from the timing equations:
```
IL1_ripple = Vin * t1 / L1
IL2_ripple = Vin * t1 / L2
```

### Step 3: Coupling Capacitor C1
```
VC1 = -Vout (DC steady state, referenced from FET side to L2 side)
```
Choose C1 for acceptable ripple. Same considerations as SEPIC.

### Step 4: Component Stresses

**Inductor L1 (input):**
```
IL1_avg = Iin_avg = (Vout + Vf) * Iout / Vin
IL1_ripple = Vin * t1 / L1
IL1_peak = IL1_avg + IL1_ripple/2
```

**Inductor L2 (output):**
```
IL2_avg = Iout
IL2_ripple = Vin * t1 / L2
IL2_peak = Iout + IL2_ripple/2
```

**FET Q1:**
```
VQ1_max = Vin + Vout + Vf
IQ1 = IL1 + IC1 during on-time
```

**Diode D1:**
```
VD1_min = -(Vin + Vout) (reverse blocking voltage)
VD1_forward = Vf
```

**Coupling Capacitor C1:**
```
VC1 = -Vout (DC, referenced appropriately)
Voltage rating >= 1.5 * (Vin_max + Vout)
```

### Step 5: Select Components
- **FET**: V_DS rating >= 1.5 * (Vin + Vout + Vf); low Rds_on
- **Diode**: V_R rating >= 1.3 * (Vin + Vout); Schottky preferred
- **L1**: current rating > IL1_peak; low DCR
- **L2**: current rating > IL2_peak; low DCR
- **C1**: voltage rating >= 1.5 * Vout; low ESR film or ceramic
- **Input cap**: voltage rating >= 1.5 * Vin_max; high ripple current rating
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR for ripple

## Complete Equations (from TI Power Topologies Handbook)

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vout + Vin + Vf)
t2 = 1/fsw - t1
```
Same t1/t2 equations as SEPIC.

### General
```
Iin_avg = (Vout + Vf) * Iout / Vin
IL1_ripple = Vin * t1 / L1
IL2_ripple = Vin * t1 / L2
```

### Inductor L1
```
I_L1_avg = Iin_avg
V_L1_max = Vin (during t1)
V_L1_min = -(Vout + Vf) (during t2)
```

### Inductor L2
```
I_L2_avg = Iout
V_L2_max = Vin (during t1, through C1)
V_L2_min = -(Vout + Vf) (during t2)
```

### FET Q1
```
I_Q1_avg = (IL1 + IL2) * D (combined current during on-time)
V_Q1_min = 0V
V_Q1_max = Vin + Vout + Vf
```

### Diode D1
```
I_D1_avg = Iout (delivers all output current)
V_D1_min = -(Vin + Vout)
V_D1_max = Vf
```

### Coupling Capacitor C1
```
VC1 = -Vout (DC, node referenced from switch to L2)
```

### Input Capacitor Ci
```
I_Ci_rms: pulsating (similar to boost input, but through FET)
```

### Output Capacitor Co
```
I_Co_min = IL2_min - Iout
I_Co_max = IL2_max - Iout
Vripple = IL2_ripple / (8 * Co * fsw) + IL2_ripple * ESR
```

### DCM Equations
Same structure as SEPIC DCM. Third interval t3 where both inductor currents are zero:
```
t3 = 1/fsw - t1 - t2
V_L1_t3 = 0V
V_L2_t3 = 0V
```

## Ngspice Netlist Template

```spice
* Zeta Converter
* Vin={Vin}V, Vout={Vout}V (positive, non-inverting), Iout={Iout}A, fsw={fsw}Hz

.title Zeta Converter

* Parameters
.param Vin={Vin}
.param Vout={Vout}
.param fsw={fsw}
.param duty={D}
.param L1={L1}
.param L2={L2}
.param C1={C1}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param tstep={1/(fsw*200)}
.param tstop={80/fsw}
.param tstart={40/fsw}

* Input supply
Vin in 0 DC {Vin}

* PWM gate drive
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Input inductor L1 (from input to switch node)
L1 in lx1 {L1} ic=0
RL1 lx1 sw {0.01}

* FET Q1 (high-side, between switch node and ground)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 sw gate 0 0 NMOS W=100u L=1u

* Coupling capacitor C1 (from switch node to diode/L2 junction)
C1 sw cap_node {C1} ic={Vout}

* Diode D1 (from ground to cap_node, conducts during off-time)
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 0 cap_node DSCHOTTKY

* Output inductor L2 (from cap_node to output)
L2 cap_node lx2 {L2} ic=0
RL2 lx2 out 0.01

* Input capacitor
C_in in 0 {Cin}

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
meas tran IL1_avg avg i(L1) from=tstart to=tstop
meas tran IL1_max max i(L1) from=tstart to=tstop
meas tran IL1_min min i(L1) from=tstart to=tstop
meas tran IL2_avg avg i(L2) from=tstart to=tstop
meas tran IL2_max max i(L2) from=tstart to=tstop
meas tran IL2_min min i(L2) from=tstart to=tstop
meas tran VC1_avg avg v(sw,cap_node) from=tstart to=tstop
meas tran VC1_ripple pp v(sw,cap_node) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Zeta Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL1_avg IL1_max IL1_min
print IL2_avg IL2_max IL2_min
print VC1_avg VC1_ripple
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata zeta_results.csv v(out) v(sw) v(cap_node) i(L1) i(L2) i(Vin)
quit
.endc

.end
```
