---
description: Design an Active Clamp Forward converter from specs, calculate components, generate ngspice netlist
---

# Active Clamp Forward Converter Design

## When to Use
- Isolated DC-DC conversion needed (galvanic isolation)
- Output voltage lower than input voltage (step-down via turns ratio)
- Duty cycle may exceed 0.5 (advantage over other Forward variants)
- Medium to high power (100W-1kW typical)
- Higher efficiency than single-switch Forward (magnetizing energy recycled)
- ZVS achievable for reduced switching losses

## Circuit Description
The Active Clamp Forward is an isolated Buck derivative. Energy is transferred to the secondary when the main FET Q1 IS conducting. The transformer does not store energy (no air gap needed); it resets during the off-time via an active clamp circuit (FET Q2 + clamp capacitor Cclamp). The clamp recycles the magnetizing energy back to the input instead of dissipating it.

Primary side: Q1 (main FET), Q2 (clamp FET), Cclamp (clamp capacitor), transformer T1 (Lp magnetizing inductance, np:ns turns ratio).
Secondary side: D1 (rectifier diode), D2 (freewheeling diode), L1 (output inductor), Co (output cap).

The secondary-referred inductance is Ls = Lp / (np/ns)^2.

## Design Procedure

### Step 1: Turns Ratio
```
ns/np = (Vout + Vf) / (Vin * D_max)
```
Choose D_max around 0.45-0.65 at Vin_min. Active clamp allows D > 0.5.

### Step 2: Duty Cycle
```
D = (Vout + Vf) / (Vin * ns/np)
```
Check at Vin_min (max D) and Vin_max (min D).

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 4: Calculate Output Inductance
```
L1 = (Vin * ns/np - Vf - Vout) * D / (r * Iout * fsw)
```
Worst-case (highest ripple) at Vin_max (lowest D but highest voltage across inductor during t1).

### Step 5: Verify CCM/DCM Boundary
CCM guaranteed when:
```
Iout > deltaI/2 = r * Iout / 2
```
Always true for r < 2.0. For light loads, converter enters DCM.

### Step 6: Magnetizing Current
```
Imag = Vin * t1 / Lp
```
Lp is the transformer primary magnetizing inductance. Imag is typically 10-20% of the reflected load current to ensure proper operation.

### Step 7: Clamp Voltage and Capacitor
```
Vclamp = D / (1 - D) * Vin
```
The clamp capacitor Cclamp should be large enough to keep the voltage ripple small (< 5% of Vclamp):
```
Cclamp > Imag / (0.05 * Vclamp * fsw)
```

### Step 8: Component Stresses

**Output Inductor L1:**
```
I_avg = Iout
I_peak = Iout * (1 + r/2)
I_valley = Iout * (1 - r/2)
V_L1_max = Vin * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2)
```

**Main FET Q1:**
```
V_Q1_max = Vin / (1 - D) = Vin + Vclamp (CLAMPED)
I_Q1_avg = (Iout * ns/np + Imag/2) * D
I_Q1_rms = (Iout * ns/np) * sqrt(D) * sqrt(1 + r^2/12)
```

**Clamp FET Q2:**
```
V_Q2_max = Vin / (1 - D) (same as Q1, CLAMPED)
I_Q2_avg = Imag/2 * (1 - D)
```

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
- **Main FET Q1**: V_DS >= 1.5 * V_Q1_max; low Rds_on
- **Clamp FET Q2**: same voltage rating as Q1; can be smaller current rating
- **Clamp cap Cclamp**: voltage rating >= 1.5 * Vclamp; low ESR film cap preferred
- **Rectifier D1**: V_R >= 1.3 * V_D1_max; Schottky preferred
- **Freewheeling D2**: V_R >= 1.3 * V_D2_max; Schottky preferred
- **Output inductor L1**: current rating > I_peak; low DCR
- **Transformer T1**: core with no air gap; adequate Lp for magnetizing current; proper np:ns ratio
- **Output cap Co**: low ESR for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin * ns/np - Vf - Vout) * t1 / L1
Ls = Lp / (np/ns)^2
Imag = Vin * t1 / Lp
Vclamp = D / (1 - D) * Vin
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vin * ns/np)
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
t3 = 1/fsw - t1 - t2
Imin = 0A
Imax = (Vin * ns/np - Vf - Vout) * t1 / L1
```

### Output Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * fsw
V_L1_min = -(Vout + Vf)
V_L1_max = Vin * ns/np - Vf - Vout
V_L1_t3 = 0V (DCM)
```

### Main FET Q1
```
I_Q1_avg = (Imin + Imax)/2 * (ns/np) * t1 * fsw + Imag/2 * D
V_Q1_min = 0V
V_Q1_max = Vin + Vclamp = Vin / (1 - D)
```

### Clamp FET Q2
```
I_Q2_avg = Imag/2 * (1 - D)
V_Q2_max = Vin / (1 - D)
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
* Active Clamp Forward Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz
* Turns ratio np:ns = {np}:{ns}

.title Active Clamp Forward Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param np={np}
.param ns={ns}
.param Lp={Lp}
.param L1={L1}
.param Cclamp={Cclamp}
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

* PWM gate drive for main FET Q1
Vpwm_q1 gate_q1 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Complementary PWM for clamp FET Q2 (with dead time)
.param deadtime=50n
Vpwm_q2 gate_q2 0 PULSE(10 0 {-deadtime} 1n 1n {duty/fsw + 2*deadtime} {1/fsw})

* Switch models
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)

* Main FET Q1: input to transformer primary
S1 in pri_top gate_q1 0 SW1

* Transformer: coupled inductors (no energy storage, k close to 1)
* Primary winding
Lp1 pri_top pri_bot {Lp} ic=0
* Secondary winding
Ls1 sec_dot sec_bot {Lp*(ns/np)*(ns/np)} ic=0
* Coupling coefficient (close to 1 for forward transformer)
K1 Lp1 Ls1 0.998

* Clamp circuit: Q2 + Cclamp across primary
* Clamp FET Q2 connects clamp node to primary top
S2 clamp pri_top gate_q2 0 SW1
D_body_q2 pri_top clamp DFAST
* Clamp capacitor
C_clamp clamp pri_bot {Cclamp}

* Primary return to ground
Rpri pri_bot 0 0.001

* Secondary rectifier diode D1 (conducts during t1)
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
.model DFAST D(Is=1e-14 Rs=0.05 N=1.0 BV=200 TT=20n)
D1 sec_dot rect_out DSCHOTTKY

* Freewheeling diode D2 (conducts during t2)
D2 0 rect_out DSCHOTTKY

* Secondary return
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
meas tran Vclamp_avg avg v(clamp) from=tstart to=tstop

echo "=== Active Clamp Forward Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
print Vclamp_avg
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata active_clamp_fwd_results.csv v(out) v(pri_top) v(clamp) i(L_out) i(Vin)
quit
.endc

.end
```
