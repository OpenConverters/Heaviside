---
description: Design a Push-Pull converter from specs, calculate components, generate ngspice netlist
---

# Push-Pull Converter Design

## When to Use
- Isolated DC-DC conversion needed
- Low input voltage applications (battery, solar, 12V/24V/48V bus)
- Medium power (50-500W)
- Bidirectional core excitation (good transformer utilization)
- Center-tapped transformer acceptable

## Circuit Description
The Push-Pull converter uses two FETs that alternately drive two primary windings of a center-tapped transformer. Energy is transferred to the output when either FET is conducting. The secondary is also center-tapped with two rectifier diodes. An output inductor L1 and capacitor Co form the output filter.

Components: Q1, Q2 (primary FETs), T1 (center-tapped transformer, Np1=Np2), D1, D2 (secondary rectifiers), L1 (output inductor), Ci (input cap), Co (output cap).

Critical: Flux balance must be maintained -- any DC offset in the transformer causes core saturation. Current-mode control is strongly recommended.

## Design Procedure

### Step 1: Turns Ratio
```
n = ns/np = (Vout + Vf) / (Vin_min * D_max)
```
Where D_max is typically limited to 0.45 per switch (0.9 total) to provide dead time.

### Step 2: Duty Cycle
```
D = (Vout + Vf) / (Vin * ns/np)
```
Each FET conducts for time t1 per half-cycle. Maximum D per switch = 0.5 (no overlap allowed).

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 4: Calculate Output Inductance
```
L1 = (Vin_min * ns/np - Vf - Vout) * t1 / (r * Iout)
```
Reflected voltage across secondary minus Vout and Vf, applied during t1.

### Step 5: Magnetizing Inductance
```
Imag = Vin * t1 / Lp
```
Magnetizing current does not transfer power but adds to primary FET current.

### Step 6: Component Stresses

**Output Inductor L1:**
```
I_L1_avg = Iout
I_L1_peak = Iout * (1 + r/2)
I_L1_valley = Iout * (1 - r/2)
V_L1_max = Vin * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2, freewheeling)
```

**FET Q1, Q2:**
```
VQ_max = 2 * Vin
I_Q_avg = Iout * ns/np * D / 2
I_Q_peak = (Iout * (1 + r/2)) * ns/np + Imag
```

**Diode D1, D2 (secondary rectifiers):**
```
VD_max = 2 * Vin * ns/np
I_D_avg = Iout / 2
```

**Transformer T1:**
```
Ls (referred to secondary) = Lp / (np/ns)^2
VNp_max = Vin
VNs_max = Vin * ns/np
I_primary_peak = Iout * ns/np + Imag
```

**Input Capacitor Ci:**
```
I_Ci_rms = I_primary_rms (pulsed primary current)
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 7: Select Components
- **FETs**: V_DS rating >= 1.5 * 2 * Vin_max; low Rds_on
- **Rectifier diodes**: V_R >= 1.3 * 2 * Vin_max * ns/np; Schottky or fast recovery
- **Transformer**: center-tapped primary and secondary; matched windings critical for flux balance
- **Output inductor**: L value from Step 4; current rating > I_L1_peak
- **Input cap**: voltage rating >= 1.5 * Vin_max; high ripple current rating
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin * ns/np - Vf - Vout) * t1 / L1
Imag = Vin * t1 / Lp
Ls = Lp / (np/ns)^2
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

### FET Q1, Q2
```
I_Q_avg = (Imin + Imax)/2 * ns/np * t1 * fsw + Imag/2 * t1 * fsw
V_Q_min = 0V
V_Q_max = 2 * Vin
V_Q_t3 = Vin (DCM, reflected voltage)
```

### Diode D1, D2
```
I_D_avg = (Imin + Imax)/2 * t1 * fsw (each diode conducts during alternate t1)
V_D_min = -(2 * Vin * ns/np)
V_D_max = Vf
V_D_t3 = -Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_t1 = -(Iout * ns/np + Imag) + Iin_avg (pulsed)
I_Ci_t2 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Ngspice Netlist Template

```spice
* Push-Pull Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Push-Pull Converter

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
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* Center-tapped primary transformer model
* Primary winding 1: in -> ct_pri (center tap = Vin)
* Primary winding 2: ct_pri -> in (wound in opposite sense)
* Using coupled inductors for center-tapped transformer
Lp1 in sw1 {Lp} ic=0
Lp2 in sw2 {Lp} ic=0
Ls1 sec1 ct_sec {Lp*(ns/np)^2} ic=0
Ls2 sec2 ct_sec {Lp*(ns/np)^2} ic=0
K1 Lp1 Ls1 0.998
K2 Lp2 Ls2 0.998

* PWM gate drives (alternating, 180 degrees out of phase)
* Q1 drives first half-cycle, Q2 drives second half-cycle
Vpwm1 gate1 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})
Vpwm2 gate2 0 PULSE(0 10 {1/(2*fsw)} 1n 1n {duty/fsw} {1/fsw})

* Primary FETs (low-side switches)
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)
S1 sw1 0 gate1 0 SW1
S2 sw2 0 gate2 0 SW1

* Secondary rectifier diodes (center-tapped secondary)
.model DFAST D(Is=1e-5 Rs=0.03 N=1.05 BV=200)
D1 ct_sec sec1 DFAST
D2 ct_sec sec2 DFAST

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

echo "=== Push-Pull Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata pushpull_results.csv v(out) v(ct_sec) i(L1) i(Vin)
quit
.endc

.end
```

## Flux Balance Considerations

Push-pull converters are susceptible to transformer flux imbalance (DC offset in the core magnetization). This can lead to core saturation and destructive currents.

Mitigation strategies:
- **Current-mode control**: Cycle-by-cycle current limiting inherently balances the flux
- **Matched FETs**: Use FETs with matched Rds_on to ensure equal volt-seconds per half-cycle
- **Matched windings**: Primary winding halves must be as symmetric as possible
- **Series capacitor**: A small DC-blocking capacitor in series with the primary prevents DC flux buildup (adds cost and loss)
