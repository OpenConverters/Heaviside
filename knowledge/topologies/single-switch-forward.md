---
description: Design a Single Switch Forward converter from specs, calculate components, generate ngspice netlist
---

# Single Switch Forward Converter Design

## When to Use
- Isolated DC-DC conversion needed (galvanic isolation)
- Simplest isolated forward topology
- Output voltage lower than input voltage (step-down via turns ratio)
- Duty cycle limited to < 0.5 (with 1:1 reset winding)
- Low to medium power (50W-500W typical)
- Cost-sensitive designs (single FET, simple drive)

## Circuit Description
The Single Switch Forward is the simplest isolated Buck derivative. Energy is transferred to the secondary when the main FET Q1 IS conducting. The transformer does not store energy (no air gap needed); it resets during the off-time via a dedicated reset winding and demagnetization diode D3. The reset winding forces the magnetizing current back to zero before the next switching cycle.

Primary side: Q1 (main FET), D3 (demagnetization diode on reset winding), transformer T1 with primary winding (np turns, Lp magnetizing inductance), secondary winding (ns turns), and reset winding (n_reset turns).
Secondary side: D1 (rectifier diode), D2 (freewheeling diode), L1 (output inductor), Co (output cap).

The secondary-referred inductance is Ls = Lp / (np/ns)^2.

**Key limitation**: D < np / (np + n_reset). For 1:1 reset winding (n_reset = np), D < 0.5.

## Design Procedure

### Step 1: Turns Ratio
```
ns/np = (Vout + Vf) / (Vin * D_max)
```
Choose D_max < 0.45 at Vin_min (leave margin below 0.5 for 1:1 reset).

### Step 2: Reset Winding Ratio
Typically n_reset = np (1:1 reset). This gives D_max = 0.5.
For higher duty cycle headroom, use n_reset > np, but FET voltage stress increases.
```
D_max = np / (np + n_reset)
```

### Step 3: Duty Cycle
```
D = (Vout + Vf) / (Vin * ns/np)
```
Check at Vin_min (max D) and Vin_max (min D). Ensure D_max < np/(np + n_reset).

### Step 4: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 5: Calculate Output Inductance
```
L1 = (Vin * ns/np - Vf - Vout) * D / (r * Iout * fsw)
```
Worst-case ripple at Vin_max.

### Step 6: Verify CCM/DCM Boundary
CCM guaranteed when:
```
Iout > deltaI/2 = r * Iout / 2
```
Always true for r < 2.0. For light loads, converter enters DCM.

### Step 7: Demagnetization Time
```
td = t1 * np / n_reset
```
For 1:1 reset (n_reset = np): td = t1.
Dead time available after demagnetization:
```
tad = 1/fsw - t1 - td
```
Must verify tad > 0, otherwise transformer does not fully reset (saturation risk).

### Step 8: Magnetizing Current
```
Imag = Vin * t1 / Lp
```
Lp is the transformer primary magnetizing inductance.

### Step 9: Component Stresses

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
V_Q1_max = Vin * (1 + np/n_reset) -- HIGH STRESS!
```
For 1:1 reset: V_Q1_max = 2 * Vin. This is a major disadvantage.
```
I_Q1_avg = (Iout * ns/np + Imag/2) * D
I_Q1_rms = (Iout * ns/np) * sqrt(D) * sqrt(1 + r^2/12)
```

**Demagnetization Diode D3 (on reset winding):**
```
V_D3_max = Vin * (1 + n_reset/np)
I_D3_peak = Imag * np/n_reset
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

### Step 10: Select Components
- **FET Q1**: V_DS >= 1.5 * V_Q1_max = 1.5 * Vin * (1 + np/n_reset); this often requires high-voltage FETs
- **Demagnetization diode D3**: fast recovery; V_R >= 1.3 * V_D3_max
- **Rectifier D1**: V_R >= 1.3 * V_D1_max; Schottky preferred
- **Freewheeling D2**: V_R >= 1.3 * V_D2_max; Schottky preferred
- **Output inductor L1**: current rating > I_peak; low DCR
- **Transformer T1**: core with no air gap; adequate Lp; proper np:ns:n_reset ratio
- **Output cap Co**: low ESR for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin * ns/np - Vf - Vout) * t1 / L1
Ls = Lp / (np/ns)^2
Imag = Vin * t1 / Lp
D_max = np / (np + n_reset)
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vin * ns/np)
td = t1 * np / n_reset
t2 = 1/fsw - t1
tad = 1/fsw - t1 - td (must be > 0)
D = t1 * fsw
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin * (1/efficiency)
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf) / (fsw * (Vin * ns/np - Vout - Vf) * (Vin * ns/np)))
t2 = t1 * (Vin * ns/np - Vout - Vf) / (Vout + Vf)
td = t1 * np / n_reset
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

### Main FET Q1
```
I_Q1_avg = (Imin + Imax)/2 * (ns/np) * t1 * fsw + Imag/2 * D
V_Q1_min = 0V
V_Q1_on = Vin (during t1, reflected to primary)
V_Q1_demag = Vin * (1 + np/n_reset) (during td, demagnetization)
V_Q1_max = Vin * (1 + np/n_reset)
V_Q1_tad = Vin (after demagnetization, before next cycle)
```

### Demagnetization Diode D3
```
I_D3_avg = Imag/2 * (np/n_reset) * td * fsw
V_D3_max = Vin * (1 + n_reset/np)
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
* Single Switch Forward Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz
* Turns ratio np:ns:n_reset = {np}:{ns}:{n_reset}

.title Single Switch Forward Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param np={np}
.param ns={ns}
.param n_reset={n_reset}
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

* PWM gate drive for main FET Q1
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Switch model
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)

* Main FET Q1: primary winding to ground
S1 pri_bot 0 gate 0 SW1

* Transformer: three coupled inductors (primary, secondary, reset)
* Primary winding: in -> pri_bot (through Q1 to ground)
Lp1 in pri_bot {Lp} ic=0
* Secondary winding
Ls1 sec_dot sec_bot {Lp*(ns/np)*(ns/np)} ic=0
* Reset winding (wound in opposite sense for demagnetization)
Lr1 reset_dot reset_bot {Lp*(n_reset/np)*(n_reset/np)} ic=0
* Coupling coefficient (close to 1 for forward transformer)
K1 Lp1 Ls1 Lr1 0.998

* Reset winding demagnetization path
* Reset winding dot connected to input via D3, return to ground
* D3 conducts when Q1 is off, resetting the core
.model DFAST D(Is=1e-14 Rs=0.05 N=1.0 BV=200 TT=20n)
D3 reset_bot 0 DFAST
* Reset winding dot connected to Vin
Rreset reset_dot in 0.001

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
meas tran VQ1_max max v(pri_bot) from=tstart to=tstop

echo "=== Single Switch Forward Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
print VQ1_max
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata single_switch_fwd_results.csv v(out) v(pri_bot) i(L_out) i(Vin)
quit
.endc

.end
```
