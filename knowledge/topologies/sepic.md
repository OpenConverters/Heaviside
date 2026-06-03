---
description: Design a SEPIC (Single-Ended Primary-Inductor Converter) from specs, calculate components, generate ngspice netlist
---

# SEPIC Converter Design

## When to Use
- Output voltage can be higher or lower than input voltage (step-up/step-down)
- Non-inverting output required (positive output from positive input)
- Non-isolated application
- Like a Buck-Boost but without output polarity inversion
- Any power level

## Circuit Description
The SEPIC transfers energy when the FET is NOT conducting. It uses two inductors (L1, L2) and a coupling capacitor (C1) to achieve non-inverting buck-boost operation. L1 sees the input current; L2 delivers current to the output through D1. The coupling capacitor C1 transfers energy between the two inductor stages.

Components: Q1 (low-side FET), D1 (output diode), L1 (input inductor), L2 (output inductor), C1 (coupling capacitor), Ci (input cap), Co (output cap).

The two inductors can be wound on the same core (coupled inductors). With coupled inductors, use 2x the inductance value in calculations. Coupling also eliminates the resonance between L and C1 that can disturb the control loop.

Note: SEPIC has a right-half-plane zero (RHPZ) and is a 4th-order system, making control more complex than a Buck.

## Design Procedure

### Step 1: Duty Cycle
```
D = (Vout + Vf) / (Vin + Vout + Vf)
```
Where Vf = diode forward voltage.

Note: D is calculated at nominal Vin. Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Choose Current Ripple Ratio
Target r = 0.4 (optimal tradeoff per Maniktala).
```
r = deltaI_L1 / Iin_avg
```
Where deltaI_L1 = peak-to-peak inductor L1 current ripple.

### Step 3: Calculate Inductances
```
L1 = Vin * D / (r * Iin_avg * fsw)
L2 = Vin * D / (r * Iout * fsw)
```
Often L1 = L2 for simplicity. With coupled inductors on the same core, use 2x inductance value.

### Step 4: Verify CCM/DCM Boundary
CCM guaranteed when inductor current does not reach zero during t2.

### Step 5: Right-Half-Plane Zero
```
frhpz = Vout * (1 - D)^2 / (2 * pi * D^2 * L1 * Iout)
```
Crossover frequency must be set well below frhpz (typically fc < frhpz/3).

### Step 6: Component Stresses

**Inductor L1 (input):**
```
I_L1_avg = Iin_avg = (Vout + Vf) * Iout / Vin
I_L1_peak = I_L1_avg + IL1_ripple/2
I_L1_valley = I_L1_avg - IL1_ripple/2
V_L1_max = Vin (during t1)
V_L1_min = -(Vout + Vf) (during t2)
```

**Inductor L2 (output):**
```
I_L2_avg = Iout
I_L2_peak = Iout + IL2_ripple/2
I_L2_valley = Iout - IL2_ripple/2
V_L2_max = Vin (during t1, via C1)
V_L2_min = -(Vout + Vf) (during t2)
```

**FET Q1:**
```
V_Q1_max = Vin + Vout + Vf
I_Q1 = IL1 + IL2 combined (both inductor currents flow through Q1 during t1)
I_Q1_avg = (I_L1_avg + I_L2_avg) * D
```

**Diode D1:**
```
V_D1_min = -(Vin + Vout)
I_D1 = IL1 + IL2 combined (both inductor currents flow through D1 during t2)
I_D1_avg = (I_L1_avg + I_L2_avg) * (1 - D)
```

**Coupling Capacitor C1:**
```
V_C1_dc = Vin
V_C1_ripple = Iin_avg * (1 - D) / (C1 * fsw)
```

**Input Capacitor Ci:**
```
I_Ci_rms = IL1_ripple / (2 * sqrt(3))
```

**Output Capacitor Co:**
```
I_Co_t1 = -Iout
I_Co_max_t2 = I_L1_peak + I_L2_peak - Iout
```

### Step 7: Select Components
- **FET**: V_DS rating >= 1.5 * V_Q1_max; I_D rating > I_Q1_peak; low Rds_on
- **Diode**: V_R rating >= 1.3 * |V_D1_min|; Schottky preferred for low Vf
- **L1**: L value from Step 3; current rating > I_L1_peak; low DCR
- **L2**: L value from Step 3; current rating > I_L2_peak; low DCR
- **Coupling cap C1**: voltage rating >= 1.5 * Vin_max; low ESR film or ceramic; ripple current rated
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating > I_Ci_rms
- **Output cap**: voltage rating >= 1.5 * Vout; ESR low enough for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
IL1_ripple = Vin * t1 / L1
IL2_ripple = Vin * t1 / L2
Iin_avg = (Vout + Vf) * Iout / Vin
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vout + Vin + Vf)
t2 = 1/fsw - t1
IL1_min = Iin_avg - IL1_ripple/2 * (t1 + t2) * fsw
IL1_max = IL1_min + IL1_ripple
IL2_min = Iout - IL2_ripple/2
IL2_max = IL2_min + IL2_ripple
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * L2 * (Vout + Vf) * fsw * (L1 + L2)) / (Vin * fsw * (L1 + L2))
t2 = sqrt(2 * Iout * L1 * L2 * (Vout + Vf) * fsw * (L1 + L2)) / ((Vout + Vf) * fsw * (L1 + L2))
t3 = 1/fsw - t1 - t2
IL1_min = 0A
IL2_min = 0A
IL1_max = Vin * t1 / L1
IL2_max = Vin * t1 / L2
```

### Inductor L1
```
I_L1_avg = Iin_avg
V_L1_min = -(Vout + Vf)
V_L1_max = Vin
V_L1_t3 = 0V (DCM)
```

### Inductor L2
```
I_L2_avg = Iout
V_L2_min = -(Vout + Vf)
V_L2_max = Vin
V_L2_t3 = 0V (DCM)
```

### FET Q1
```
I_Q1_avg = (IL1_min + IL1_max + IL2_min + IL2_max)/2 * t1 * fsw
V_Q1_min = 0V
V_Q1_max = Vin + Vout + Vf
V_Q1_t3 = Vin (DCM)
```

### Diode D1
```
I_D1_avg = (IL1_min + IL1_max + IL2_min + IL2_max)/2 * t2 * fsw
V_D1_min = -(Vin + Vout)
V_D1_max = Vf
V_D1_t3 = -Vout (DCM)
```

### Coupling Capacitor C1
```
V_C1_dc = Vin
V_C1_ripple = Iin_avg * (1 - D) / (C1 * fsw)
```

### Input Capacitor Ci
```
I_Ci_min_t1 = -IL1_max + Iin_avg
I_Ci_max_t1 = -IL1_min + Iin_avg
I_Ci_t2_t3 = Iin_avg
```

### Output Capacitor Co
```
I_Co_t1 = -Iout
I_Co_max_t2 = IL1_max + IL2_max - Iout
```

## Ngspice Netlist Template

```spice
* SEPIC Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title SEPIC Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param L1={L1}
.param L2={L2}
.param C1={C1}
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

* Low-side switch (MOSFET)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 sw_node gate 0 0 NMOS W=100u L=1u
* Alternative: voltage-controlled switch
* .model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
* S1 sw_node 0 gate 0 SW1

* Input inductor L1
L1 in l1x {L1} ic=0
RL1 l1x sw_node 0.01

* Coupling capacitor C1 (between L1/Q1 junction and L2)
C1 sw_node l2x {C1} ic={Vin}

* Output inductor L2
L2 l2x l2out {L2} ic=0
RL2 l2out diode_a 0.01

* Output diode (anode at L2 output, cathode at Vout)
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 diode_a out DSCHOTTKY

* Output capacitor
C_out out 0 {Cout} ic={Vout*0.9}

* Input capacitor
C_in in 0 {Cin}

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
meas tran VC1_avg avg v(sw_node,l2x) from=tstart to=tstop
meas tran VC1_ripple pp v(sw_node,l2x) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== SEPIC Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL1_avg IL1_max IL1_min
print IL2_avg IL2_max IL2_min
print VC1_avg VC1_ripple
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata sepic_results.csv v(out) v(sw_node) i(L1) i(L2) v(sw_node,l2x) i(Vin)
quit
.endc

.end
```

## Coupled Inductor Variant

When L1 and L2 are wound on the same core, replace the two inductor definitions with a coupled pair:

```spice
* Coupled inductors on same core
* Use 2x inductance value for coupled design
L1 in l1x {L1} ic=0
L2 l2x l2out {L2} ic=0
K1 L1 L2 0.95

* With coupled inductors, C1 resonance with L does not affect the loop
* This simplifies control design
```

## Design Notes (Maniktala)
- SEPIC is like a Buck-Boost but non-inverting (positive output from positive input)
- Two inductors can be wound on same core (coupled), simplifying magnetics
- With coupled inductors, resonance between L and C1 does not affect the control loop
- Ideal duty cycle: D = Vout / (Vin + Vout)
- More complex control than Buck due to RHPZ and 4th-order plant transfer function
- Coupling capacitor C1 must handle full input current ripple; use low-ESR film or ceramic
- C1 voltage is approximately equal to Vin in steady state
