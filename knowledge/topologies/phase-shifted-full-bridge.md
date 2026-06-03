---
description: Design a Phase-Shifted Full-Bridge (PSFB) converter from specs, calculate components, generate ngspice netlist
---

# Phase-Shifted Full-Bridge Converter Design

## When to Use
- Isolated DC-DC conversion at high power (500W to multi-kW)
- High efficiency required (ZVS eliminates switching losses)
- Wide input voltage range
- Telecom, server, and industrial power supplies
- When hard-switched full-bridge efficiency is insufficient

## Circuit Description
The Phase-Shifted Full-Bridge (PSFB) converter uses the same H-bridge topology as the standard full-bridge, but controls power by phase-shifting the two legs rather than varying duty cycle. Each FET operates at nearly 50% duty cycle, and the phase angle between leg A and leg B determines the effective duty cycle seen by the transformer. During the transition intervals, the transformer leakage inductance (plus any external series inductance) resonates with FET output capacitances to achieve zero-voltage switching (ZVS).

Components: Q1, Q2, Q3, Q4 (H-bridge FETs), Lsh (series/leakage inductance for ZVS), T1 (transformer), D1, D2 (secondary rectifiers), L1 (output inductor), Ci (input cap), Co (output cap).

Key difference from standard full-bridge: all FETs switch at 50% duty cycle; power control is via phase shift between legs.

## Design Procedure

### Step 1: Effective Primary Voltage
Leakage inductance reduces effective voltage applied to transformer:
```
VNp = Vin * Lp / (Lp + Lsh)
```
Where Lsh = total series inductance (leakage + external).

### Step 2: Turns Ratio
```
n = ns/np = (Vout + Vf) / (VNp_min * D_eff_max)
```
Where D_eff_max accounts for duty cycle loss due to leakage.

### Step 3: Effective Duty Cycle
```
D_eff = (Vout + Vf) / (VNp * ns/np)
```
The actual phase shift determines D_eff.

### Step 4: Duty Cycle Loss
During transitions, the leakage inductance must reset before power transfers:
```
D_loss = 4 * Lsh * Iload_reflected * fsw / Vin
```
Where Iload_reflected = Iout * ns/np. Actual D must compensate: D_actual = D_eff + D_loss.

### Step 5: Choose Current Ripple Ratio
Target r = 0.4 for the output inductor.
```
r = deltaI / Iout
```

### Step 6: Calculate Output Inductance
```
L1 = (VNp * ns/np - Vf - Vout) * t1 / (r * Iout)
```

### Step 7: Magnetizing Inductance
```
Imag = Vin * t1 / (Lp + Lsh)
```
Magnetizing current must be large enough to achieve ZVS for the lagging leg.

### Step 8: ZVS Conditions
For leading leg (easier): reflected load current provides energy.
For lagging leg (harder): magnetizing + leakage current must charge/discharge Coss.
```
ZVS condition (lagging leg): 0.5 * (Lp + Lsh) * Imag^2 >= 2 * Coss * Vin^2
```
If ZVS is not achievable at light load, increase Lsh or decrease Lp (at the cost of duty cycle loss).

### Step 9: Component Stresses

**Output Inductor L1:**
```
I_L1_avg = Iout
I_L1_peak = Iout * (1 + r/2)
I_L1_valley = Iout * (1 - r/2)
V_L1_max = VNp * ns/np - Vf - Vout (during power transfer)
V_L1_min = -(Vout + Vf) (during freewheeling)
```

**FET Q1, Q2, Q3, Q4:**
```
VQ_max = Vin
I_Q_peak = (Iout * (1 + r/2)) * ns/np + Imag
```
All FETs: duty cycle approximately 50%. ZVS eliminates turn-on switching losses.

**Diode D1, D2 (center-tapped secondary):**
```
VD_max = 2 * VNp * ns/np
I_D_avg = Iout / 2
```

**Transformer T1:**
```
VNp_min = -Vin
VNp_max = Vin
I_primary_peak = Iout * ns/np + Imag
```

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 10: Select Components
- **FETs**: V_DS rating >= 1.5 * Vin_max; low Coss for easier ZVS; low Rds_on
- **Series inductor**: Lsh sized for ZVS at minimum load; consider external inductor
- **Rectifier diodes**: V_R >= 1.3 * 2 * VNp_max * ns/np; fast recovery essential (hard commutation on secondary)
- **Transformer**: minimize leakage if external Lsh used; or design controlled leakage
- **Output inductor**: L value from Step 6; current rating > I_L1_peak
- **Output cap**: voltage rating >= 1.5 * Vout; low ESR

## Complete Equations

### General
```
VNp = Vin * Lp / (Lp + Lsh)
D_eff = (Vout + Vf) / (VNp * ns/np)
Iripple = (VNp * ns/np - Vf - Vout) * t1 / L1
Imag = Vin * t1 / (Lp + Lsh)
D_loss = 4 * Lsh * Iout * ns/np * fsw / Vin
```

### CCM Timing
```
t1 = 1/(2*fsw) * (Vout + Vf) / (VNp * ns/np)
t2 = 1/(2*fsw) - t1
tph = D_eff / (2 * fsw) (phase shift time)
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin (ideal, lossless)
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout + Vf) / (fsw * (VNp*ns/np - Vout - Vf) * VNp * ns/np))
t2 = t1 * (VNp*ns/np - Vout - Vf) / (Vout + Vf)
t3 = 1/(2*fsw) - t1 - t2
Imin = 0A
Imax = (VNp * ns/np - Vf - Vout) * t1 / L1
```

### Output Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * 2 * fsw
V_L1_max = VNp * ns/np - Vf - Vout (during t1)
V_L1_min = -(Vout + Vf) (during t2)
V_L1_t3 = 0V (DCM)
```

### FET Q1, Q2, Q3, Q4
```
I_Q_avg = ((Imin + Imax)/2 * ns/np + Imag/2) * t1 * fsw
V_Q_min = 0V
V_Q_max = Vin
```
Note: Each FET switches at ~50% duty cycle. ZVS achieved when body diode conducts before gate turn-on.

### Diode D1, D2
```
I_D_avg = (Imin + Imax)/2 * t1 * fsw
V_D_min = -(2 * VNp * ns/np)
V_D_max = Vf
V_D_t3 = -Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_t1 = -(Iout * ns/np + Imag) + Iin_avg
I_Ci_t2 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Ngspice Netlist Template

```spice
* Phase-Shifted Full-Bridge Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Phase-Shifted Full-Bridge Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param phase_shift={D_eff}
.param np={np}
.param ns={ns}
.param L={L}
.param Lp={Lp}
.param Lsh={Lsh}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param deadtime=100n
.param tstep={1/(fsw*200)}
.param tstop={80/fsw}
.param tstart={40/fsw}

* Input supply
Vin in 0 DC {Vin}

* Input capacitor
C_in in 0 {Cin}

* Phase-shifted gate drives
* Leg A: Q1 (high-side), Q3 (low-side) - LEADING leg
* Both at 50% duty cycle, complementary with dead time
Vpwm_Q1 gate_Q1 0 PULSE(0 10 {deadtime} 1n 1n {1/(2*fsw)-deadtime} {1/fsw})
Vpwm_Q3 gate_Q3 0 PULSE(0 10 {1/(2*fsw)+deadtime} 1n 1n {1/(2*fsw)-deadtime} {1/fsw})

* Leg B: Q2 (high-side), Q4 (low-side) - LAGGING leg
* Phase-shifted by tph relative to Leg A
.param tph={phase_shift/(2*fsw)}
Vpwm_Q2 gate_Q2 0 PULSE(0 10 {tph+deadtime} 1n 1n {1/(2*fsw)-deadtime} {1/fsw})
Vpwm_Q4 gate_Q4 0 PULSE(0 10 {tph+1/(2*fsw)+deadtime} 1n 1n {1/(2*fsw)-deadtime} {1/fsw})

* FET switch model
.model SW1 SW(Ron=0.05 Roff=1Meg Vt=2.5 Vh=0.5)

* Leg A (leading)
S_Q1 in pri_a gate_Q1 0 SW1
S_Q3 pri_a 0 gate_Q3 0 SW1

* Leg B (lagging)
S_Q2 in pri_b gate_Q2 0 SW1
S_Q4 pri_b 0 gate_Q4 0 SW1

* Body diodes (essential for ZVS -- body diode conducts before gate turn-on)
.model DBODY D(Is=1e-10 Rs=0.05 N=1.2 BV=600)
D_body_Q1 pri_a in DBODY
D_body_Q3 0 pri_a DBODY
D_body_Q2 pri_b in DBODY
D_body_Q4 0 pri_b DBODY

* FET output capacitances (for ZVS resonance)
C_oss_Q1 in pri_a 200p
C_oss_Q3 pri_a 0 200p
C_oss_Q2 in pri_b 200p
C_oss_Q4 pri_b 0 200p

* Series (leakage) inductance for ZVS energy storage
L_sh pri_a pri_a2 {Lsh} ic=0

* Transformer: primary between pri_a2 and pri_b
* Center-tapped secondary
Lp1 pri_a2 pri_b {Lp} ic=0
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

echo "=== Phase-Shifted Full-Bridge Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

* Check ZVS (look for body diode conduction before gate turn-on)
echo "=== Check ZVS by examining switch node waveforms ==="

wrdata psfb_results.csv v(out) v(pri_a) v(pri_b) v(ct_sec) i(L1) i(Vin)
quit
.endc

.end
```

## ZVS Design Guidelines

### Leading Leg ZVS (easier)
The reflected load current provides the energy to charge/discharge the Coss of Q1 and Q3. ZVS is typically achieved across the full load range.

### Lagging Leg ZVS (harder)
Only the magnetizing current and energy stored in Lsh are available. At light load, ZVS may be lost.
```
ZVS energy requirement: 0.5 * (Lp + Lsh) * I_ZVS^2 >= 2 * Coss * Vin^2
```
Where I_ZVS = current at the switching instant of the lagging leg.

Strategies to extend ZVS range:
- **Increase Lsh**: Adds external inductor in series with primary (increases duty cycle loss)
- **Decrease Lp**: Increases magnetizing current (increases conduction loss)
- **Add auxiliary circuit**: Resonant clamp or active clamp to extend ZVS range

### Duty Cycle Loss
The series inductance causes a finite transition time where the primary voltage is not fully applied:
```
t_transition = Lsh * I_load_reflected / Vin
D_loss = 2 * t_transition * fsw
```
This reduces the effective duty cycle and must be compensated by the turns ratio design.

## Design Notes

- PSFB achieves the highest efficiency of hard-switched isolated topologies due to ZVS.
- Secondary-side rectifier diodes still undergo hard commutation (consider synchronous rectification or current doubler rectifier).
- The dead time must be carefully set: long enough for ZVS resonance, short enough to avoid losing energy.
- Current-mode control with peak current sensing on the primary is recommended for both regulation and overcurrent protection.

---

## Andreycak PSFB Design Guide (from SLUA107)

Source: Andreycak, "Phase Shifted Full Bridge, Zero Voltage Transition Design Considerations" (TI SLUA107A, Rev. Aug 2011).

### Right Leg vs. Left Leg Transitions

The PSFB has two fundamentally different transitions:

**Right leg transition (easier -- "leading leg"):**
- Occurs when the power transfer interval ends (e.g., QD turns off while QA is still on)
- Primary current at this instant is approximately the full load current IP(t0)
- The load current charges CD from 0 to VIN and discharges CC from VIN to 0
- Transition is fast because the full reflected load current drives the resonance
- During transition, as primary voltage drops below reflected secondary voltage, the output inductor begins supplementing decaying primary power

**Left leg transition (harder -- "lagging leg"):**
- Occurs when the freewheeling interval ends (e.g., QA turns off while QC is on)
- Primary current has decayed from losses during the freewheeling interval
- Only the magnetizing current and residual primary current are available
- Transition takes longer than the right leg because available current is lower
- This is where ZVS is most likely to be lost at light load

### Resonant Tank Design

The resonant tank frequency must be at least 4x higher than the maximum transition time:
```
omega_R = pi / (2 * t_max_transition)
```

**Resonant capacitance:**
```
CR = (8/3) * Coss + Cxfmr
```
Where:
- Coss is multiplied by 4/3 to account for voltage-dependent capacitance
- Factor of 2 because two FET capacitances are driven in parallel
- Cxfmr = transformer winding capacitance (NOT negligible at high frequency)

**Resonant inductance:**
```
LR = 1 / (omega_R^2 * CR)
= (2 * t_max^2) / (pi^2 * ((8/3)*Coss + Cxfmr))
```

This is the exact value needed for the resonant transition. Note: LR = leakage inductance + any external series inductance.

**Stored energy requirement for ZVS:**
```
0.5 * LR * I_PRI(min)^2 >= 0.5 * CR * VIN(max)^2
```

**Minimum primary current for ZVS:**
```
I_PRI(min) = VIN(max) * sqrt(CR / LR)
```

### Practical Considerations

**Series inductance (LR) too large:**
- Limits primary current slew rate: dI/dt = VIN / LR
- May prevent reaching full load current within the conversion cycle
- At very high frequencies (>500 kHz), transition times erode usable duty cycle significantly

**Sources of minimum primary current for ZVS:**
1. Minimum load current (simplest -- set minimum load spec)
2. Design transformer magnetizing inductance to provide sufficient magnetizing current
3. Reflected output inductor magnetizing current
4. External inductor shunting the transformer primary

**Transformer magnetizing inductance alone is usually insufficient** in off-line high-frequency converters because the transformer is core-loss limited (many primary turns, high Lmag).

### Operational Waveform Summary

The PSFB cycle has four intervals per half-cycle:
1. **Power transfer**: Diagonal switches on (e.g., QB + QC). Full VIN across primary. Current rises from negative to positive.
2. **Right leg transition**: One switch turns off. Coss resonance swings the leg. Fast transition.
3. **Freewheeling (clamped)**: Two switches in same leg are on (e.g., QA + QC). Primary shorted. Current maintained. No power transfer.
4. **Left leg transition**: Other switch turns off. Slower resonance. ZVS achieved if sufficient energy.

**Key benefit of phase-shifted control**: The commutating switches (that transition during the harder left-leg interval) can be designated as the high-side switches, simplifying gate drive since one high-side switch is always already ON when the other needs to turn on.

### Performance Summary

- ZVS eliminates switching losses and discharge of Coss
- EMI/RFI significantly lower due to soft switching (controlled dv/dt)
- Fixed frequency operation achievable over identified range of VIN and Iout
- Peak efficiency obtained with moderate load ranges; 10:1 load range is practical
- Best suited for mid-to-high power off-line applications
- Above ~500 kHz, transition times erode usable duty cycle -- diminishing returns
- Secondary rectifiers still undergo hard commutation (synchronous rectification or current doubler recommended)
