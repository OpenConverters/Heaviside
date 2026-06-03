---
description: Design a Buck (step-down) converter from specs, calculate components, generate ngspice netlist
---

# Buck Converter Design

## When to Use
- Output voltage lower than input voltage (Vout < Vin)
- Non-isolated application
- High efficiency needed (typically 90-97%)
- Any power level

## Circuit Description
The Buck converter steps down an input voltage to a lower output voltage. Energy is transferred to the output when the FET is conducting. The inductor current flows continuously through either the FET (during t1) or the freewheeling diode (during t2).

Components: Q1 (high-side FET), D1 (freewheeling diode) or Q2 (sync rectifier), L1 (inductor), Ci (input cap), Co (output cap).

For synchronous Buck: set Vf = 0V in all equations.

## Design Procedure

### Step 1: Duty Cycle
```
D = (Vout + Vf) / (Vin + Vf)
```
Where Vf = diode forward voltage (0 for synchronous).

Note: D is calculated at nominal Vin. Always check at Vin_min (max D) and Vin_max (min D).

### Step 2: Choose Current Ripple Ratio
Target r = 0.4 (optimal tradeoff per Maniktala).
```
r = deltaI / Iout
```
Where deltaI = peak-to-peak inductor current ripple.

### Step 3: Calculate Inductance
```
L = Vout * (1 - D_min) / (r * Iout * fsw)
```
Or equivalently (all forms are mathematically identical for ideal buck where Vout = D*Vin):
```
L = (Vin - Vout) * D / (r * Iout * fsw)
L = Vin * D * (1 - D) / (r * Iout * fsw)
```
Worst-case (highest ripple) is at Vin_max for Buck (D_min gives highest volt-seconds).

### Step 4: Verify CCM/DCM Boundary
CCM guaranteed when:
```
Iout > deltaI/2 = r * Iout / 2
```
This is always true for r < 2.0. For light loads, converter enters DCM.

### Step 5: Component Stresses

**Inductor:**
```
I_avg = Iout
I_peak = Iout * (1 + r/2)
I_valley = Iout * (1 - r/2)
V_L_max = Vin_max - Vout (during t1)
V_L_min = -(Vout + Vf) (during t2)
```

**FET Q1:**
```
V_Q1_max = Vin_max + Vf
I_Q1_avg = Iout * D
I_Q1_rms = Iout * sqrt(D) * sqrt(1 + r^2/12)
```

**Diode D1 (or sync FET Q2):**
```
V_D1_max = Vin_max
I_D1_avg = Iout * (1 - D)
```

**Input Capacitor Ci:**
```
I_Ci_rms = Iout * sqrt(D * (1 - D)) * sqrt(1 + r^2/12)
```
(Highest stress at D = 0.5)

**Output Capacitor Co:**
```
I_Co_rms = Iout * r / (2 * sqrt(3))
Vripple = Iout * r / (8 * Co * fsw) + Iout * r * ESR
```

### Step 6: Select Components
- **FET**: V_DS rating >= 1.5 * V_Q1_max; I_D rating > I_peak; low Rds_on
- **Diode**: V_R rating >= 1.3 * V_D1_max; Schottky preferred for low Vf
- **Inductor**: L value from Step 3; current rating > I_peak; low DCR
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating > I_Ci_rms
- **Output cap**: voltage rating >= 1.5 * Vout; ESR low enough for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin - Vout) * t1 / L1
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) / (Vin + Vf)
t2 = 1/fsw - t1
Imin = Iout - Iripple/2
Imax = Iout + Iripple/2
Iin_avg = (Vout * Iout) / Vin + (Vf/Vin) * (Imin+Imax)/2 * t2 * fsw
```

### DCM Timing
```
t1 = sqrt(2 * Iout * L1 * (Vout+Vf) / (fsw * (Vin-Vout) * (Vin+Vf)))
t2 = t1 * (Vin+Vf)/(Vout+Vf) - t1
t3 = 1/fsw - t1 - t2
Imin = 0A
Imax = (Vin - Vout) * t1 / L1
```

### Inductor L1
```
I_L1_avg = (Imin + Imax)/2 * (t1 + t2) * fsw
V_L1_min = -(Vout + Vf)
V_L1_max = Vin - Vout
V_L1_t3 = 0V (DCM)
```

### FET Q1
```
I_Q1_avg = (Imin + Imax)/2 * t1 * fsw
V_Q1_min = 0V
V_Q1_max = Vin + Vf
V_Q1_t3 = Vin - Vout (DCM)
```

### Diode D1
```
I_D1_avg = (Imin + Imax)/2 * t2 * fsw
V_D1_min = -Vin
V_D1_max = Vf
V_D1_t3 = -Vout (DCM)
```

### Input Capacitor Ci
```
I_Ci_min_t1 = -Imax + Iin_avg
I_Ci_max_t1 = -Imin + Iin_avg
I_Ci_t2_t3 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Imin - Iout
I_Co_max = Imax - Iout
```

## Output Ripple Voltage (from Basso Ch1)

The peak-to-peak output ripple has two contributions:

### Capacitive contribution (ideal cap):
```
deltaV_cap = deltaI_L / (8 * C * fsw)
```
Where deltaI_L = Iripple = (Vin - Vout) * D / (L * fsw)

Normalized to Vout:
```
deltaV_cap / Vout = (1 - D) / (8 * L * C * fsw^2) = pi^2 / 2 * (f_LC / fsw)^2 * (1 - D)
```
Where f_LC = 1/(2*pi*sqrt(L*C)) is the LC filter cutoff frequency.

### ESR contribution:
```
deltaV_ESR = deltaI_L * R_ESR
```

### Total ripple (approximation):
```
deltaV_total = sqrt(deltaV_cap^2 + deltaV_ESR^2)
```
Or conservatively: deltaV_total ≈ deltaV_cap + deltaV_ESR

Design rule: If ESR dominates, ripple is triangular. If capacitance dominates, ripple is quasi-sinusoidal.

## RMS Current Formulas (from Basso Appendix 1D)

### CCM:
**Inductor RMS:**
```
I_L_rms = sqrt(I_avg^2 + (deltaI_L)^2 / 12)
```

**Switch RMS:**
```
I_sw_rms = sqrt(D) * sqrt(Iv^2 + Iv*deltaI + deltaI^2/3)  [exact]
         ≈ I_avg * sqrt(D) * sqrt(1 + r^2/12)              [simplified]
```
Where Iv = valley current, deltaI = ripple, r = ripple ratio

**Diode RMS:**
```
I_D_rms = sqrt(1-D) * sqrt(Ip^2 - Ip*deltaI + deltaI^2/3)  [exact]
        ≈ I_avg * sqrt(1-D) * sqrt(1 + r^2/12)              [simplified]
```
Where Ip = peak current

**Input Capacitor RMS:**
```
I_Cin_rms = sqrt(I_sw_rms^2 - (D * I_avg)^2)
          = I_avg * sqrt(D*(1-D)) * sqrt(1 + r^2/12)
```

**Output Capacitor RMS:**
```
I_Cout_rms = deltaI_L / (2*sqrt(3)) = I_avg * r / (2*sqrt(3))
```

### DCM:
**Inductor RMS:**
```
I_L_rms = Ip * sqrt((D1 + D2)/3)
```

**Switch RMS:**
```
I_sw_rms = Ip * sqrt(D1/3)
```

**Diode RMS:**
```
I_D_rms = Ip * sqrt(D2/3)
```
Where D1 = on-time duty, D2 = off-time duty, Ip = peak current

## Small-Signal Transfer Functions (from Basso Appendix 2A)

### Voltage-Mode CCM:
Control-to-output:
```
H(s) = Vout/Vpeak * (1 + s*R_ESR*C) / (1 + s/omega_0/Q + s^2/omega_0^2)
```
Where:
- omega_0 = 1/sqrt(L*C) (LC resonance)
- Q = R*sqrt(C/L) / (1 + R_ESR/R) (quality factor, can be very high)
- Zero at: f_z_ESR = 1/(2*pi*R_ESR*C)
- Vpeak = sawtooth amplitude of PWM modulator

### Current-Mode CCM:
Control-to-output is approximately single-pole:
```
H(s) ≈ Gdc * (1 + s/omega_z_ESR) * (1 - s/omega_z_sub) / ((1 + s/omega_p1) * (1 + s/omega_p2))
```
- Much easier to compensate than voltage mode (single dominant pole)
- Subharmonic oscillation pole at fsw/2 (needs slope compensation if D > 0.5)

### Compensation Selection Guide:
- **Voltage-mode Buck CCM**: Use Type 3 compensator (double pole from LC)
- **Current-mode Buck CCM**: Use Type 2 compensator (single pole system)
- **Buck DCM (any control)**: Use Type 2 or even Type 1 (first-order system)

## Practical Design Methodology (from Basso Ch5)

Complete Buck design checklist:
1. Calculate D at nominal and extreme Vin
2. Choose r = 0.3-0.4, calculate L
3. Verify CCM condition at minimum load
4. Calculate output ripple, select Cout (check both capacitive and ESR terms)
5. Calculate input ripple, select Cin (RMS current is the key stress)
6. Calculate switch voltage/current stress, select MOSFET
7. Calculate diode stress, select diode (Schottky preferred)
8. Determine control-to-output transfer function
9. Design compensator (Type 2 for CM, Type 3 for VM)
10. Verify stability (>45° phase margin, >10dB gain margin)
11. Simulate transient response (load step)
12. Check thermal budget (total losses vs cooling)

## Ngspice Netlist Template

```spice
* Buck Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Buck Converter

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

* High-side switch (ideal MOSFET model)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 in gate sw 0 NMOS W=100u L=1u
* Alternative: voltage-controlled switch
* .model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
* S1 in sw gate 0 SW1

* Freewheeling diode
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 0 sw DSCHOTTKY

* LC filter
L1 sw lx {L} ic=0
RL lx out 0.01
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
meas tran IL_avg avg i(L1) from=tstart to=tstop
meas tran IL_max max i(L1) from=tstart to=tstop
meas tran IL_min min i(L1) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Buck Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_avg IL_max IL_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata buck_results.csv v(out) v(sw) i(L1) i(Vin)
quit
.endc

.end
```

## Synchronous Buck Variant

Replace diode D1 with a low-side FET Q2 driven complementary to Q1 (with dead time):

```spice
* Complementary PWM with dead time
Vpwm_hi gate_hi 0 PULSE(0 10 {deadtime} 1n 1n {duty/fsw - deadtime} {1/fsw})
Vpwm_lo gate_lo 0 PULSE(10 0 0 1n 1n {duty/fsw + deadtime} {1/fsw})

* High-side and low-side FETs
.model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
S1 in sw gate_hi 0 SW1
S2 sw 0 gate_lo 0 SW1
D_body 0 sw DSCHOTTKY
```

## GaN Buck Converter Considerations (from Lidow et al.)

Source: Lidow et al., "GaN Transistors for Efficient Power Conversion" (3rd ed, 2020), Chapters 7 and 10.

### Hard-Switching Buck Converter with GaN

The buck converter is a hard-switching topology where the control switch (high-side) experiences full overlap losses. GaN provides 3-11x improvement in the hard-switching FOM = (QGD + QGS2) * RDS(on).

#### Loss Breakdown (48 V to 12 V, 700 kHz, EPC2045 example)

**Control switch (Q1) dominant losses:**
- COSS loss: 0.79 W (unavoidable in hard-switching, set by bus voltage)
- Turn-on overlap loss: 0.44 W (voltage fall time ~3 ns, current rise time ~0.15 ns)
- Conduction loss: 0.25 W (proportional to D * RDS(on) * I^2)
- Gate charge loss: 20 mW
- Turn-off overlap loss: 0.5 mW (negligible -- GaN turns off before VDS rises significantly)

**Synchronous rectifier (Q2) dominant losses:**
- Conduction loss: 0.73 W (proportional to (1-D) * RDS(on) * I^2)
- Reverse conduction (dead time) loss: 0.25 W (high VSD of GaN makes this significant)
- COSS-related reverse conduction: 4.5 mW
- Gate charge loss: 18 mW

**Key insight**: Turn-off overlap loss in GaN is 100-1000x smaller than turn-on overlap loss. This is because the gate voltage drops below threshold before VDS rises, avoiding the Miller plateau entirely. The transition analysis models for Si MOSFETs (based on Miller plateau) do not apply to GaN.

### Switching Transition Analysis for GaN Buck

The standard Miller-plateau-based switching model is generally not applicable to GaN transistors driven with low gate resistance. Instead:

**Turn-on voltage fall time:**
```
t_vf = (QOSS_Q1 + QOSS_Q2) / (0.5 * gfs * delta_VGS_vf)
```
Where delta_VGS_vf is the gate voltage rise during the voltage transition. The channel current overshoots IL, and the excess displaces the COSS charges.

**Turn-off current fall time:**
```
t_cf = QGS2 / I_G_cf
```
The channel turns off quickly (typically < 0.3 ns), and the remaining voltage transition is lossless (load current charges COSS_Q1 and discharges COSS_Q2).

### Impact of Parasitic Inductance

**Common-source inductance (LCS) impact on turn-on loss:**
- LCS opposes gate drive during current rise, extending the overlap period.
- With 100 pH of CSI, turn-on loss can increase by 20-50%.
- With 500 pH, the converter may not switch properly.
- Target: < 50 pH for high-performance designs.

**Power loop inductance impact:**
- Higher loop inductance reduces dv/dt (which reduces COSS-related losses slightly) but increases voltage overshoot.
- Voltage overshoot = I_L * sqrt(L_loop / COSS) approximately.
- With 2 nH loop inductance and 10 A at 48 V: overshoot can reach 80-90% of VDS.
- With 0.5 nH (optimal layout): overshoot drops to 30%.
- If overshoot exceeds device VDS rating, add a TVS/Zener clamp or redesign the layout.

### PCB Layout Impact on Buck Efficiency

Measured results (12 V to 1.2 V, 1 MHz, EPC2015C GaN):

| Layout | Loop L | Efficiency @ 20A | Overshoot |
|--------|--------|------------------|-----------|
| Vertical (Si MOSFET) | 2.0 nH | 85.0% | baseline |
| Vertical (GaN) | 1.8 nH | 86.5% | 85% |
| Lateral (GaN) | 1.0 nH | 87.5% | 45% |
| Optimal (GaN) | 0.5 nH | 88.5% | 30% |

The optimal layout GaN design provides ~3.5% higher efficiency than the Si MOSFET benchmark at full load.

### Dead Time in GaN Buck Converters

- GaN's higher reverse conduction voltage (VSD ~ 2.0-2.5 V vs 0.5-1.0 V for Si body diode) makes dead time losses significant.
- With 12 ns effective dead time at 700 kHz, reverse conduction loss is 0.25 W -- comparable to conduction loss in the control switch.
- Adaptive dead time is strongly recommended. Target 5-10 ns for optimized designs.
- For ZVS of the synchronous rectifier during the falling edge: the load current must be sufficient to charge/discharge COSS within the dead time. Minimum current for ZVS = 2 * QOSS / t_dead.
- Anti-parallel Schottky diode can reduce dead-time loss but adds COSS and package inductance. Beneficial mainly when dead time is not well optimized.

### High-Frequency GaN Buck (5-10 MHz)

At very high frequencies, GaN monolithic half-bridge ICs offer critical advantages:

- **Parasitic reduction**: Monolithic integration reduces loop inductance to ~150 pH (vs 250 pH discrete, 2 nH Si MOSFET package).
- **Die size optimization**: Monolithic half bridges allow asymmetric sizing (small Q1 for low switching loss, large Q2 for low conduction loss) without wasting package pins.
- **Thermal spreading**: Heat from the smaller Q1 conducts through the shared substrate to the larger Q2 area, improving thermal performance.

**Example results** (12 V to 1 V, 1 MHz, 2x parallel EPC2100 monolithic half-bridges):
- Efficiency at 40 A: ~87.5% (3% higher than discrete GaN, 5%+ higher than Si MOSFET)
- Total system power loss reduced by 25% vs discrete GaN at high load
- Peak switching voltage: 14 V (vs 12 V bus -- only 17% overshoot, no snubber needed)

### 48 V Buck Converter with Parallel GaN (Non-Isolated IBC)

For 48 V to 12 V intermediate bus conversion, a hard-switching buck with paralleled GaN transistors can compete with traditional isolated brick converters:

- 4 parallel EPC2001C per switch position, optimal power loop layout
- 300 kHz switching frequency
- Peak efficiency: 97.5%, full-load (30 A) efficiency: 97%
- Outperforms typical Si isolated eighth-brick converters (96% peak) while eliminating the transformer
- Maximum temperature rise with 4 parallel devices: ~60 degC at 30 A with good layout

### Frequency Selection Guidelines

| Input Voltage | Frequency Range | Key Consideration |
|--------------|----------------|-------------------|
| 3.3-5 V | 5-10 MHz | Gate loss dominates; use monolithic ICs |
| 12 V | 1-5 MHz | Balance switching and conduction loss |
| 48 V | 300 kHz-1 MHz | COSS loss significant; consider soft-switching above 500 kHz |
| 400 V | 100-500 kHz | Hard-switching COSS loss dominates; strongly consider LLC/ZVS topology |

### Magnetics Considerations at High Frequency

- At 300-500 kHz: Standard ferrite cores (e.g. MnZn ferrite, Ferroxcube 3F36/3F46) work well.
- At 1 MHz: Core losses increase; consider NiZn ferrites or thin laminated cores.
- Above 2 MHz: Magnetics materials become the limiting factor. Air-core inductors or PCB-embedded magnetics may be needed.
- The inductor/transformer loss becomes the dominant system loss (>48% of total in the 300 kHz eighth-brick example), not the transistors (~28%). Magnetics design becomes more critical than device selection.
