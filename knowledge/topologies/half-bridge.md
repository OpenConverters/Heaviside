---
description: Design a Half-Bridge converter from specs, calculate components, generate ngspice netlist
---

# Half-Bridge Converter Design

## When to Use
- Isolated DC-DC conversion needed
- Medium power applications (100-500W)
- Higher input voltage than push-pull (no 2*Vin stress on FETs)
- Cost-effective alternative to full-bridge (only 2 FETs)
- Center-tapped secondary acceptable

## Circuit Description
The Half-Bridge converter uses two FETs in a totem-pole configuration with two input capacitors forming a voltage divider. The transformer sees Vin/2 across its primary. The two FETs switch alternately, driving the primary with a bipolar voltage waveform. The secondary is center-tapped with two rectifier diodes, followed by an output LC filter.

Components: Q1 (high-side FET), Q2 (low-side FET), C1, C2 (input capacitor divider, each sees Vin/2), T1 (transformer), D1, D2 (secondary rectifiers), L1 (output inductor), Co (output cap).

Each FET sees only Vin (not 2*Vin like push-pull), making it suitable for higher input voltages.

## Design Procedure

### Step 1: Turns Ratio
```
n = ns/np = (Vout + Vf) / (Vin_min/2 * D_max)
```
Where D_max is typically limited to 0.45 per switch. Note effective primary voltage is Vin/2.

### Step 2: Duty Cycle
```
D = (Vout + Vf) / (Vin/2 * ns/np)
```
Equivalently:
```
D = (Vout + Vf) * np / (Vin * ns / 2)
```
Maximum D per switch = 0.5 (no overlap allowed).

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 4: Calculate Output Inductance
```
L1 = (Vin/2 * ns/np - Vf - Vout) * t1 / (r * Iout)
```

### Step 5: Magnetizing Inductance
```
Imag = Vin * t1 / (2 * Lp)
```
Magnetizing current flows through primary; Vin/2 effective voltage across Lp.

### Step 6: Component Stresses

**Output Inductor L1:**
```
I_L1_avg = Iout
I_L1_peak = Iout * (1 + r/2)
I_L1_valley = Iout * (1 - r/2)
V_L1_max = Vin/2 * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2, freewheeling)
```

**FET Q1, Q2:**
```
VQ_max = Vin
I_Q_avg = Iout * ns/np * D / 2
I_Q_peak = (Iout * (1 + r/2)) * ns/np + Imag
```

**Diode D1, D2 (secondary rectifiers):**
```
VD_max = 2 * Vin/2 * ns/np = Vin * ns/np
I_D_avg = Iout / 2
```

**Transformer T1:**
```
VNp_max = Vin/2
VNs_max = Vin/2 * ns/np
I_primary_peak = Iout * ns/np + Imag
```

**Input Capacitors C1, C2 (voltage divider):**
```
V_C1 = V_C2 = Vin/2
I_C_rms = I_primary_rms (each cap carries full primary AC current)
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 7: Select Components
- **FETs**: V_DS rating >= 1.5 * Vin_max; I_D rating > I_Q_peak; low Rds_on
- **Rectifier diodes**: V_R >= 1.3 * Vin_max * ns/np; Schottky or fast recovery
- **Input capacitors**: 2 capacitors, each rated >= Vin_max; must handle high ripple current
- **Transformer**: single primary, center-tapped secondary
- **Output inductor**: L value from Step 4; current rating > I_L1_peak
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin/2 * ns/np - Vf - Vout) * t1 / L1
Imag = Vin * t1 / (2 * Lp)
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vin * ns/np)
t2 = 1/(2*fsw) - t1
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin (ideal, lossless)
```
Note: t1 = (1/fsw) * (Vout+Vf) / (Vin*ns/np) because effective voltage is Vin/2 but the factor of 2 cancels with the half-period.

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf) / (fsw * (Vin/2*ns/np - Vout - Vf) * Vin/2 * ns/np))
t2 = t1 * (Vin/2*ns/np - Vout - Vf) / (Vout + Vf)
t3 = 1/(2*fsw) - t1 - t2
Imin = 0A
Imax = (Vin/2 * ns/np - Vf - Vout) * t1 / L1
```

### Output Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * 2 * fsw
V_L1_max = Vin/2 * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2)
V_L1_t3 = 0V (DCM)
```

### FET Q1, Q2
```
I_Q_avg = ((Imin + Imax)/2 * ns/np + Imag/2) * t1 * fsw
V_Q_min = 0V
V_Q_max = Vin
```

### Diode D1, D2
```
I_D_avg = (Imin + Imax)/2 * t1 * fsw (each diode conducts alternate half-cycles)
V_D_min = -(Vin * ns/np)
V_D_max = Vf
V_D_t3 = -Vout (DCM)
```

### Input Capacitors C1, C2
```
V_C1 = V_C2 = Vin/2
I_C_t1 = -(Iout * ns/np + Imag) + Iin_avg (during active switch)
I_C_t2 = Iin_avg (during dead time)
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Ngspice Netlist Template

```spice
* Half-Bridge Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Half-Bridge Converter

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

* Input capacitor divider (split rail)
C1 in mid {Cin} ic={Vin/2}
C2 mid 0 {Cin} ic={Vin/2}
* Balancing resistors (optional, high value)
R_bal1 in mid 100k
R_bal2 mid 0 100k

* Half-bridge FET drives (alternating with dead time)
* Q1 (high-side): connects 'in' to 'sw_pri'
* Q2 (low-side): connects 'sw_pri' to '0'
Vpwm1 gate1 0 PULSE(0 10 {deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})
Vpwm2 gate2 0 PULSE(0 10 {1/(2*fsw)+deadtime} 1n 1n {duty/fsw-deadtime} {1/fsw})

.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)
S1 in sw_pri gate1 0 SW1
S2 sw_pri 0 gate2 0 SW1

* Body diodes
.model DBODY D(Is=1e-10 Rs=0.05 N=1.2 BV=600)
D_body1 sw_pri in DBODY
D_body2 0 sw_pri DBODY

* Transformer: primary between sw_pri and mid (sees Vin/2)
* Center-tapped secondary
Lp1 sw_pri mid {Lp} ic=0
Ls1 sec1 ct_sec {Lp*(ns/np)^2} ic=0
Ls2 sec2 ct_sec {Lp*(ns/np)^2} ic=0
K1 Lp1 Ls1 0.998
K2 Lp1 Ls2 0.998

* Secondary rectifier diodes
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

echo "=== Half-Bridge Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata halfbridge_results.csv v(out) v(sw_pri) v(ct_sec) i(L1) i(Vin)
quit
.endc

.end
```

## Design Notes

- The capacitor divider (C1, C2) must be large enough to maintain Vin/2 at the midpoint under load. Typical rule: C1 = C2 >= 10 * Cout.
- Unlike push-pull, the half-bridge has inherent DC blocking via the capacitor divider, making it less susceptible to transformer flux imbalance.
- For higher power (>500W), consider the full-bridge topology which uses the full Vin across the transformer.
- The half-bridge is cost-effective: only 2 FETs (vs. 4 for full-bridge) and FET voltage stress is only Vin (vs. 2*Vin for push-pull).
