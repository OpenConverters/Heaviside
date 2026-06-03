---
description: Design a Two-Switch Flyback converter from specs, calculate components, generate ngspice netlist
---

# Two-Switch Flyback Converter Design

## When to Use
- Isolated DC-DC conversion needed
- Low to medium power (up to 150W)
- High input voltage (offline/PFC output) where single-FET flyback has excessive voltage stress
- No snubber needed (natural voltage clamping via demagnetization diodes)
- Wide input range applications
- Same functionality as standard flyback, but with halved FET voltage stress

## Circuit Description
The Two-Switch Flyback uses two FETs in a totem-pole (series) configuration across the primary, with two demagnetization (clamping) diodes. Q1 connects Vin to the transformer primary dot, Q2 connects the other end of the primary to ground. D_clamp1 connects the transformer primary dot to Vin, and D_clamp2 connects ground to the other end of the primary. When both FETs turn off, the leakage inductance energy is returned to the input through the clamping diodes, naturally limiting FET voltage stress.

Components: Q1 (high-side FET), Q2 (low-side FET), D_clamp1, D_clamp2 (demagnetization/clamping diodes), T1 (coupled inductor/flyback transformer), D1 (secondary rectifier), Co (output cap), Ci (input cap).

Energy is stored in the transformer magnetizing inductance during t1 (FETs ON) and transferred to the secondary during t2 (FETs OFF) -- same operating principle as standard flyback.

## Design Procedure

### Step 1: Turns Ratio
```
n = ns/np = Vout / (Vin_nom * D / (1 - D))
```
Choose n to balance FET voltage stress and secondary diode stress. Same as standard flyback.

### Step 2: Duty Cycle
```
D = (Vout + Vf) * np / ((Vout + Vf) * np + Vin * ns)
```
Same as standard flyback. Maximum D typically 0.5 for CCM operation.

### Step 3: Choose Current Ripple
For CCM flyback, the primary current ramps during t1:
```
Ipri_peak = Iin + deltaI/2
Iin = Iout * (Vout + Vf) / (Vin * efficiency)
deltaI = Vin * t1 / Lp
```

### Step 4: Calculate Magnetizing Inductance
```
Lp = Vin_min * D_max / (deltaI * fsw)
```
Reflected to secondary:
```
Ls = Lp * (ns/np)^2
```

### Step 5: Verify CCM/DCM Boundary
CCM requires:
```
Iout > deltaI_sec / 2
```
Where deltaI_sec = deltaI * np/ns.

### Step 6: Right Half Plane Zero (RHPZ)
Same RHPZ as standard flyback (present in CCM):
```
frhpz = (Vout + Vf)^2 * np^2 / (2 * pi * Lp * Pout * ns^2)
```
Control loop crossover must be well below frhpz.

### Step 7: Component Stresses

**FET Q1, Q2 (key advantage: clamped voltage):**
```
VQ_max = (Vin + (Vout + Vf) * np/ns) / 2
```
This is approximately Vin/2 + Vreflected/2. In a standard flyback, VQ_max = Vin + (Vout+Vf)*np/ns + Vspike. The two-switch flyback HALVES the voltage stress and eliminates the leakage spike entirely.

```
I_Q_avg = Iin * D (same current flows through both FETs in series)
I_Q_peak = Iin + deltaI/2
I_Q_rms = Iin * sqrt(D) * sqrt(1 + (deltaI/(2*Iin))^2 / 3)
```

**Demagnetization Diodes D_clamp1, D_clamp2:**
```
VD_clamp_max = Vin
I_D_clamp_peak = magnetizing current at turn-off (reflected)
```
These diodes return leakage energy to the input supply (no dissipative snubber needed).

**Secondary Diode D1:**
```
VD1_max = (Vout + Vf) + Vin * ns/np
I_D1_avg = Iout
I_D1_peak = Iout + deltaI_sec/2
```

**Transformer T1 (coupled inductor):**
```
I_primary_peak = Iin + deltaI/2
VNp_max = Vin (during t1)
VNp_min = -(Vout + Vf) * np/ns (during t2)
VNs_max = Vin * ns/np (during t1, secondary blocked)
VNs_min = -(Vout + Vf) (during t2, secondary conducting)
```

**Input Capacitor Ci:**
```
I_Ci_rms = Iin * sqrt(D) * sqrt(1 + (deltaI/(2*Iin))^2/3) (pulsed primary current)
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * sqrt(D/(1-D)) * sqrt(1 + r_sec^2/12)
Vripple = Iout * D / (Co * fsw) + I_D1_peak * ESR
```

### Step 8: Select Components
- **FETs**: V_DS rating >= 1.5 * VQ_max; both FETs identical; high-side needs isolated/bootstrap driver
- **Clamping diodes**: V_R >= 1.3 * Vin_max; fast recovery; ultrafast or SiC preferred
- **Secondary diode**: V_R >= 1.3 * VD1_max; Schottky preferred for low Vout
- **Transformer**: Lp from Step 4; current rating > I_primary_peak; controlled leakage OK (energy recovered)
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating > I_Ci_rms
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR for ripple spec

## Complete Equations

### General
```
Iripple_pri = Vin * t1 / Lp
Iripple_sec = Iripple_pri * np/ns
VQ_max = (Vin + (Vout + Vf) * np/ns) / 2
RHPZ: frhpz = (Vout + Vf)^2 * np^2 / (2 * pi * Lp * Pout * ns^2)
```

### CCM Timing
```
t1 = D / fsw = (1/fsw) * (Vout + Vf) * np / ((Vout + Vf) * np + Vin * ns)
t2 = 1/fsw - t1
Iin = Iout * (Vout + Vf) / (Vin * eff)
Imin_pri = Iin - Iripple_pri/2
Imax_pri = Iin + Iripple_pri/2
```

### DCM Timing
```
t1 = D / fsw (same as CCM, set by controller)
t2 = Vin * t1 * ns / ((Vout + Vf) * np)
t3 = 1/fsw - t1 - t2
Imin_pri = 0A
Imax_pri = Vin * t1 / Lp
Imax_sec = Imax_pri * np/ns
```

### FET Q1, Q2
```
I_Q_avg = (Imin_pri + Imax_pri)/2 * t1 * fsw
V_Q_min = 0V
V_Q_max = (Vin + (Vout + Vf) * np/ns) / 2
```

### Demagnetization Diodes D_clamp1, D_clamp2
```
I_Dclamp = leakage current at turn-off (brief pulse)
V_Dclamp_max = Vin
```
Conduct briefly at turn-off to return leakage energy to Vin.

### Secondary Diode D1
```
I_D1_avg = (Imin_sec + Imax_sec)/2 * t2 * fsw (CCM)
V_D1_min = -(Vout + Vf + Vin * ns/np)
V_D1_max = Vf
V_D1_t3 = -Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_t1 = -(Imin_pri to Imax_pri ramp) + Iin_avg
I_Ci_t2 = Iin_avg
```

### Output Capacitor Co
```
I_Co_t1 = -Iout (capacitor supplies load during t1)
I_Co_t2_min = Imin_sec - Iout
I_Co_t2_max = Imax_sec - Iout
```

## Ngspice Netlist Template

```spice
* Two-Switch Flyback Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Two-Switch Flyback Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param np={np}
.param ns={ns}
.param Lp={Lp}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param tstep={1/(fsw*200)}
.param tstop={80/fsw}
.param tstart={40/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* PWM gate drive (both FETs switch simultaneously)
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Two-switch configuration:
* Q1 (high-side): Vin -> dot of primary
* Q2 (low-side): undotted end of primary -> GND
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)
S_Q1 in pri_dot gate 0 SW1
S_Q2 pri_undot 0 gate 0 SW1

* Demagnetization (clamping) diodes
* D_clamp1: primary dot -> Vin (returns leakage energy)
* D_clamp2: GND -> primary undotted end (returns leakage energy)
.model DCLAMP D(Is=1e-10 Rs=0.05 N=1.2 BV=600)
D_clamp1 pri_dot in DCLAMP
D_clamp2 0 pri_undot DCLAMP

* Flyback transformer (coupled inductor)
* Primary: pri_dot to pri_undot (dot at pri_dot)
* Secondary: sec_dot to sec_undot (dot at sec_dot)
Lp1 pri_dot pri_undot {Lp} ic=0
Ls1 sec_dot sec_undot {Lp*(ns/np)^2} ic=0
K1 Lp1 Ls1 0.99

* Secondary rectifier diode
* In flyback: secondary conducts when primary is OFF
* Dot convention: current exits secondary dot when primary current decreases
.model DFAST D(Is=1e-5 Rs=0.03 N=1.05 BV=200)
D_sec sec_dot out DFAST

* Secondary return
R_gnd sec_undot 0 0.001

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
meas tran Ipri_max max i(Lp1) from=tstart to=tstop
meas tran Ipri_min min i(Lp1) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Two-Switch Flyback Converter Simulation Results ==="
print Vout_avg Vout_ripple
print Ipri_max Ipri_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

* Check FET voltage stress (should be ~Vin/2 + Vreflected/2)
meas tran VQ1_max max v(in,pri_dot) from=tstart to=tstop
meas tran VQ2_max max v(pri_undot) from=tstart to=tstop
echo "=== FET Voltage Stress (should be clamped) ==="
print VQ1_max VQ2_max

wrdata twosw_flyback_results.csv v(out) v(pri_dot) v(pri_undot) i(Lp1) i(Vin)
quit
.endc

.end
```

## Comparison: Standard Flyback vs Two-Switch Flyback

| Parameter | Standard Flyback | Two-Switch Flyback |
|-----------|-----------------|-------------------|
| FET count | 1 | 2 |
| VQ_max | Vin + Vreflected + Vspike | (Vin + Vreflected) / 2 |
| Snubber | Required (RCD or active) | Not needed |
| Leakage energy | Dissipated in snubber | Returned to input |
| Gate drive | Simple (ground-referenced) | Needs isolated/bootstrap for Q1 |
| Efficiency | Lower (snubber loss) | Higher (no snubber loss) |
| Max duty cycle | ~0.5 (CCM) | ~0.5 (CCM) |
| RHPZ | Yes (CCM) | Yes (CCM, same as standard) |

## Design Notes

- The clamped voltage stress makes the two-switch flyback ideal for high-input-voltage applications (e.g., 400V PFC output, 380V DC bus) where a standard flyback would require expensive high-voltage FETs.
- Both FETs must switch simultaneously. Q1 (high-side) requires an isolated gate driver or bootstrap circuit.
- The leakage inductance energy is non-dissipatively returned to the input, improving efficiency compared to RCD snubber in standard flyback.
- The maximum duty cycle is still limited to ~0.5 in CCM to avoid subharmonic instability (same as standard flyback with current-mode control).
- Transformer design is relaxed: leakage inductance is less critical since its energy is recovered, not dissipated.
