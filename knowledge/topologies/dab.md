---
description: Design a Dual Active Bridge (DAB) isolated bidirectional DC-DC converter, calculate components, generate ngspice netlist
---

# Dual Active Bridge (DAB) Converter Design

Primary reference: F. Krismer, "Modeling and Optimization of Bidirectional Dual Active Bridge DC-DC Converter Topologies," ETH Zurich, 2010 (DISS. ETH NO. 19177).

## When to Use
- Bidirectional isolated DC-DC conversion (battery charging/discharging, V2G, energy storage)
- Medium to high power (500 W to 50 kW typical, scalable higher with paralleling)
- Wide voltage range on one or both ports
- High efficiency needed (90-95% typical)
- Soft switching (ZVS) desired over wide load range
- Galvanic isolation required
- High power density required (high frequency transformer)

## Circuit Description

The DAB converter consists of two voltage-sourced full H-bridges connected through a high-frequency transformer and a series inductance L. The inductance L can be the transformer leakage inductance, an external inductor, or a combination of both.

```
        HV Side                              LV Side
    +---+---+---+             n:1          +---+---+---+
    |   |       |          +------+        |   |       |
V1 -+  T1     T3    vAC1  |      | vAC2  T5     T7   +- V2
    |   |       |    +--L--+  HF  +--+     |   |       |
    |  T2     T4     |     | Xfmr |  |    T6     T8    |
    |   |       |    +-----+      +--+     |   |       |
    +---+---+---+          +------+        +---+---+---+
```

Components: T1-T4 (HV full bridge MOSFETs), T5-T8 (LV full bridge MOSFETs), L (series inductor, may include transformer leakage), n:1 HF transformer, CDC1 (HV DC link cap), CDC2 (LV DC link cap).

Key properties:
- Power flow controlled by phase shift between the two bridges
- Inherently bidirectional: positive phase shift = HV-to-LV, negative = LV-to-HV
- ZVS achievable for all switches under appropriate conditions
- Three voltage levels possible per bridge: +V, 0, -V (full bridge) or +V, -V (half bridge)
- Four control parameters: phase shift phi, duty cycles D1 and D2, switching frequency fS

## Design Procedure

### Step 1: Transformer Turns Ratio (n)

The voltage conversion ratio d = n*V2/V1 determines the operating characteristics. Optimal efficiency occurs near d = 1 (matched condition: V1 = n*V2).

For wide voltage ranges, select n to minimize the deviation from d = 1 at the nominal operating point:
```
n_nom = V1_nom / V2_nom
```

For Krismer's converter (V1: 240-450V, V2: 11-16V, nominal 340V/12V):
- Phase shift modulation optimal: n = 19
- Extended triangular/trapezoidal modulation optimal: n = 16
- Suboptimal modulation optimal: n = 16
- Full optimal modulation optimal: n = 17

The choice of n depends on the modulation strategy. With advanced modulation, lower n values improve efficiency because the modulation compensates for voltage mismatch.

Design guideline: Start with n = V1_nom / V2_nom, then iterate with loss model.

### Step 2: Series Inductance (L)

The inductance L determines maximum power transfer capability. For phase shift modulation:
```
P_max = n * V1 * V2 / (8 * fS * L)    at phi = pi/2
```

Therefore, the upper limit on L is:
```
L_max = n * V1_min * V2_min / (8 * fS * P_rated)
```

For the general case with safety margin:
```
L < n * V1_min * V2_min / (8 * fS * P_rated)
```

Krismer's design results (fS = 100 kHz, P = 2 kW):
- Phase shift modulation: n = 19, L = 26.7 uH, eta_avg = 89.5%
- Extended tri/trap modulation: n = 16, L = 15.5 uH, eta_avg = 92.6%
- Suboptimal modulation: n = 16, L = 22.4 uH, eta_avg = 93.5%
- Optimal modulation: n = 17, L = 21.7 uH, eta_avg = 93.7%

Note: L and n are coupled design variables. The optimal pair depends on the modulation strategy and must be found iteratively using a loss model. Higher L reduces peak currents but limits maximum power; lower L increases RMS currents at light load.

### Step 3: Switching Frequency

Typical range: 50 kHz to 200 kHz. Higher fS reduces transformer/inductor size but increases switching losses and HF conduction losses (skin/proximity effects).

Krismer selected fS = 100 kHz based on optimum power density analysis (90-200 kHz range found optimal for similar converters in literature).

Constant switching frequency is preferred for EMI compliance. Variable frequency can improve light-load efficiency but complicates EMI filter design.

### Step 4: Dead Time for ZVS

Dead time must be long enough to allow drain-source capacitance charging/discharging:
```
T_deadtime > Q(V) / I_sw_min
```

Where Q(V) is the charge required to swing the MOSFET output capacitances:
```
Q(V1) ~ C_oss_eq * V1    (for HV side)
Q(V2) ~ C_oss_eq * V2    (for LV side)
```

For CoolMOS SPW47N60CFD (HV side): Q(V1) ~ 220 nC + V1 * 218 pF.
Krismer used T_deadtime = 200 ns (HV side) and 240 ns (LV side).

Too long dead time increases body diode conduction losses. Too short dead time causes incomplete ZVS (partial hard switching).

### Step 5: Component Selection

**HV Side MOSFETs (T1-T4):**
```
V_DS_max >= 1.5 * V1_max
I_rms: see RMS current equations below
```
Select for low R_DS(on), fast body diode, low C_oss. CoolMOS or SiC MOSFETs preferred.

**LV Side MOSFETs (T5-T8):**
```
V_DS_max >= 1.5 * V2_max
I_rms: see RMS current equations below (very high on LV side!)
```
LV side is the most challenging: high RMS currents require paralleled MOSFETs. Krismer used 4 MOSFETs per switch position (IRF2804), two full bridges in parallel (total 64 MOSFETs on LV side).

**Transformer:**
- Turns ratio n as determined in Step 1
- Core sized for peak flux density from LV side excitation
- B_peak = V2 / (4 * fS * N2 * Ae)
- Interleaved windings to minimize leakage (unless leakage is used as L)
- Litz wire to reduce AC resistance at fS

**DC Link Capacitors:**
- CDC1 (HV): sized for voltage ripple and RMS current
- CDC2 (LV): very high RMS current capability needed (up to 244 A in Krismer's design with SPS)

## Key Equations

### Power Transfer - Phase Shift Modulation (SPS)

The simplest and most common modulation. D1 = D2 = 0.5, only phi varies.

Lossless model:
```
P = n * V1 * V2 * phi * (pi - |phi|) / (2 * pi^2 * fS * L)
```
where -pi < phi < pi, and P > 0 means HV-to-LV power transfer.

Maximum power at phi = pi/2:
```
P_max = n * V1 * V2 / (8 * fS * L)
```

Required phase shift for desired power P:
```
phi = (pi/2) * [1 - sqrt(1 - 8*fS*L*|P| / (n*V1*V2))] * sgn(P)
```

Initial inductor current (steady state, 0 < phi < pi):
```
iL_0 = [pi*(n*V2 - V1) - 2*phi*n*V2] / (4*pi*fS*L)
```

### Power Transfer with Conduction Losses

With total series resistance R = R1 + n^2*R2, and time constant tau = L/R:
```
R1 = 2*RS1 + R_LHV + R_tr1        (HV side)
R2 = R_tr2 + R_PCB_a + 2*RS2 + R_PCB_b  (LV side)
R_total = R1 + n^2 * R2
```

Inductor current with losses (piecewise, for constant vR during interval):
```
iL(t1) = exp(-T1/tau) * iL(t0) + [1 - exp(-T1/tau)] * vR / R
```
where T1 = t1-t0, tau = L/R, vR = vAC1 - n*vAC2.

Steady-state initial current (general, k intervals per half-cycle):
```
iL_0 = -iL(TS/2)|_{iL(0)=0} / [1 + prod_{i=1}^{k} exp(-Ti/tau)]
```

### Modulation Strategies

#### Single Phase Shift (SPS) - Simplest
- D1 = D2 = 0.5, only phi varies
- Simple implementation, only one control variable
- High circulating currents when V1 != n*V2
- Limited soft-switching range at light load
- Best suited when V1 ~ n*V2 over entire operating range

#### Triangular Current Mode Modulation
For V1 > n*V2, the inductor current has a triangular shape with zero crossings each half-cycle:
```
P = phi^2 * V1 * (n*V2)^2 / [pi^2 * fS * L * (V1 - n*V2)]
```
Maximum power:
```
P_tri_max = n^2 * V2^2 * (V1 - n*V2) / (4 * fS * L * V1)
```
Enables LV side zero-current switching. Limited power range. Requires V1 != n*V2.

#### Trapezoidal Current Mode Modulation
Extends power range beyond triangular mode. Current is trapezoidal with zero crossings:
```
P_trap_max = (n*V1*V2)^2 / [4*fS*L * (V1^2 + n*V1*V2 + (n*V2)^2)]
```
Seamless transition from triangular to trapezoidal mode.

#### Combined Triangular + Trapezoidal
Use triangular for |P| < P_tri_max, trapezoidal for P_tri_max < |P| < P_trap_max.

#### Extended Triangular and Trapezoidal (Krismer's Extension)
Modifies the basic schemes to maintain ZVS on HV side by ensuring a minimum circulating current I0 at switching instants. Key improvement: avoids hard switching at zero current on HV side.

Mode a (V2 < V2_lim, low power, triangular):
```
D1 = (1/pi) * [n*V2/(V1-n*V2)] * |phi| + 2*fS*L*(-I0)/V1
D2 = (1/pi) * [V1/(V1-n*V2)] * |phi|
```
Mode b (V2 >= V2_lim, low power, triangular):
```
D1 = (1/pi) * [n*V2/(n*V2-V1)] * |phi| - 2*fS*L*(-I0)/(n*V2-V1)
D2 = (1/pi) * [V1/(n*V2-V1)] * |phi| - 2*fS*L*(-I0)/(n*V2-V1)
```
Mode c (high power, trapezoidal): uses equations (5.13)-(5.18) from Krismer.

#### Optimal Modulation (Minimum RMS Current)
For the lossless model, minimize IL_rms over D1, D2 for given P:
- Low power: triangular current mode is optimal
- Medium power: "optimal transition mode" (one duty cycle at 0.5, other calculated via complex expressions)
- High power: phase shift modulation (D1 = D2 = 0.5)

The optimal duty cycles result in the voltage sequence 3b (P > 0) or 7b (P < 0) from Krismer's classification.

#### Suboptimal Modulation (Practical Implementation)
Krismer's key practical contribution. Achieves near-optimal efficiency with manageable complexity.

Principles:
1. Minimize conduction losses (low RMS current)
2. Ensure HV side ZVS (maintain I0 current at switching)
3. Control LV side switching current to IS2_sw_opt ~ 20A for minimum switching losses

Low power: extended triangular mode with I0 for ZVS and IS2_sw_opt for LV switching.
High power: mode 3b/7b with D2 = 0.5 (or D1 = 0.5), other duty cycle and phi adjusted for minimum losses subject to ZVS constraints.

Result: eta_avg = 93.5% vs 89.5% for SPS (Krismer's design).

### RMS Currents (Lossless Model)

General expression for all modulation modes:
```
IL_rms = 1/(2*fS*L) * sqrt(D1^2*V1^2*(1 - 4*D1/3) + D2^2*(n*V2)^2*(1 - 4*D2/3) + n*V1*V2/3 * eRMS)
```
where eRMS depends on the operating mode (see Table 3.5 in Krismer).

For phase shift modulation (D1 = D2 = 0.5):
```
IL_rms = 1/(2*fS*L) * sqrt(V1^2/12 + (n*V2)^2/12 + n*V1*V2 * (1 - 2|phi|/pi)^3 / 3 * (1 + phi^2/pi^2 ... ))
```

Simplified approximation for SPS at moderate phi (Krismer eq. 3.13 context):
```
I_HV_rms ~ P / V1 * correction_factor    (HV side)
I_LV_rms = n * I_HV_rms                   (LV side, referred to LV)
```

For the actual design, use the full piecewise RMS calculation from the inductor current waveform.

### Efficiency Comparison (Krismer's Results, 2 kW Design)

| Modulation | n | L (uH) | eta_avg | Max I_LV_rms (A) | Max I_LV_peak (A) |
|---|---|---|---|---|---|
| Phase Shift (SPS) | 19 | 26.7 | 89.5% | 294 | 550 |
| Ext. Tri/Trap | 16 | 15.5 | 92.6% | 253 | 467 |
| Suboptimal | 16 | 22.4 | 93.5% | 240 | 399 |
| Optimal | 17 | 21.7 | 93.7% | 231 | 407 |

## Component Stresses

### Primary Switches T1-T4 (HV Side)
```
V_DS_max = V1_max                   (e.g., 450V)
I_rms_max = 13.6 - 15.9 A          (depends on modulation)
I_peak_max = 23.9 - 29.1 A         (depends on modulation)
```

### Secondary Switches T5-T8 (LV Side)
```
V_DS_max = V2_max                   (e.g., 16V)
I_rms_max = 231 - 294 A            (depends on modulation)
I_peak_max = 399 - 550 A           (depends on modulation)
```
LV side currents are extremely high due to the low voltage. Paralleling of MOSFETs and careful PCB layout are critical.

### Transformer
```
V_primary = V1 (square wave or quasi-square wave)
V_secondary = V2
B_peak = V2 / (4 * fS * N2 * Ae)   (excited primarily by LV side)
I_primary_rms: same as inductor RMS current
I_secondary_rms = n * I_primary_rms
```
Core: N87 or similar power ferrite. Krismer achieved B_peak = 126-142 mT.

### DC Link Capacitors
```
HV side: I_Cf1_rms up to 14.9 A (SPS) or 9.2 A (optimal)
LV side: I_Cf2_rms up to 244 A (SPS) or 125 A (optimal)
```

## ZVS Analysis

### ZVS Condition
ZVS occurs when the inductor current at the switching instant has the correct polarity to charge/discharge the MOSFET drain-source capacitances during the dead time.

For the HV side (rising edge of vAC1):
```
ZVS if: IS1_sw = -iL1(t_sw) > 0     (current flows OUT of the switching node)
```
Specifically, the current must supply enough charge to fully swing the voltage:
```
IS1_sw > IS1_sw_min ~ 2A             (for CoolMOS, depends on C_oss and dead time)
```

For IS1_sw between 0 and ~2A: incomplete ZVS, increased losses.
For IS1_sw < 0: hard switching, high losses due to body diode reverse recovery.
For IS1_sw = 0: capacitive hard switching, still significant losses.

### ZVS Boundaries for SPS
With phase shift modulation, ZVS is lost when:
- V1 >> n*V2 or V1 << n*V2 (voltage mismatch)
- Light load (small phi, small currents)
- Specific bridge legs lose ZVS before others

At the nominal operating point (V1 = n*V2), ZVS is maintained for all power levels. Away from this condition, ZVS is progressively lost, especially at light load.

### Extending ZVS Range
1. **Advanced modulation**: Use extended triangular/trapezoidal or suboptimal modulation to maintain a minimum circulating current I0 at switching instants
2. **Auxiliary circuits**: Resonant snubbers or active clamp circuits (adds complexity)
3. **Design optimization**: Choose n and L to maximize ZVS range for the expected operating profile
4. **Dead time optimization**: Adjust dead time for the actual switching current

### LV Side Switching
The LV side experiences different switching behavior due to parasitic package inductances. High di/dt during switching causes voltage spikes across package inductances. Krismer found:
- Zero-current switching on LV side reduces switching losses
- There exists an optimal switching current IS2_sw_opt ~ 20A that minimizes total LV switching losses
- This is exploited in the suboptimal modulation scheme

## Bidirectional Operation

### Forward Mode (HV to LV)
- phi > 0 (vAC1 leads vAC2)
- P > 0
- Power flows from high voltage port to low voltage port

### Reverse Mode (LV to HV)
- phi < 0 (vAC2 leads vAC1)
- P < 0
- Power flows from low voltage port to high voltage port
- Same topology, just reverse phase shift

### Transition Between Modes
Seamless transition by changing the sign of phi. The DAB is inherently symmetric for bidirectional operation. No topology change or mode switching needed. The controller simply ramps phi through zero.

## Dynamic Model and Control

### Small-Signal Model (Krismer Chapter 6)
The DAB small-signal model uses discrete-time modeling (sampling at TS/2 = TDAB).

State vector:
```
x = [iL, if1a, if1b, if2a/n, if2b/n, vf1, n*vf2]^T
```

The control-to-output transfer function for phase shift modulation:
```
G_PE_PS = E_T * (z*I - Q*R*A*Q)^-1 * Q*R*B_PS
```
where A, B, Q, R are matrices derived from the circuit equations.

### Control Structure
Krismer uses a cascaded control with:
1. **Inner current loop**: Controls if1 (HV port current) via phase shift modulation
2. **Outer voltage loop**: Controls vf2 (LV port voltage)
3. **Modulator**: Converts controller output to timing parameters
4. **Digital implementation**: DSP (TMS320F2808) + FPGA (LCMXO2280)

Sampling: Power stage at TDAB = 5 us (TS/2), control loops at 10*TDAB = 50 us.

### Simplified Transfer Function
For initial controller design, the DAB with SPS can be approximated as:
```
G_DAB(s) ~ K_DAB / (1 + s/omega_p)
```
where K_DAB depends on the operating point (V1, V2, phi) and omega_p is determined by the output filter.

## Practical Considerations

### Transformer Design for DAB
- Core: ferrite (N87 or similar), E-core or planar for high power density
- Winding: Litz wire mandatory for HV side at 100 kHz; copper foil or PCB windings for LV side
- Leakage inductance: can be part of L (reduces component count) but difficult to control precisely
- Magnetizing inductance: should be large to minimize magnetizing current contribution
- Krismer's design: N1 = n, N2 = 1 (single-turn secondary for minimum LV winding resistance)

### Dead Time Selection
- Too short: shoot-through risk, incomplete ZVS
- Too long: body diode conduction losses, reduced effective duty cycle
- HV side: ~200 ns typical for CoolMOS
- LV side: ~240 ns typical
- Can be made adaptive based on load current for optimization

### Start-up Sequence
1. Pre-charge DC link capacitors (soft-start through resistor or auxiliary supply)
2. Start with phi = 0 (no power transfer)
3. Slowly ramp phi to desired operating point
4. Enable closed-loop control once steady state is reached
5. Avoid large phi steps that could cause transformer saturation

### Light Load Operation
- SPS has poor efficiency at light load due to circulating currents
- Burst mode (skip switching cycles) improves light-load efficiency
- Variable frequency can help but complicates EMI filter
- Advanced modulation (triangular current mode) significantly improves light-load efficiency
- Krismer's suboptimal modulation achieves >90% efficiency down to ~500W (vs ~85% for SPS)

### Voltage Matching (d = n*V2/V1)
- d = 1 (V1 = n*V2): optimal condition, minimum RMS currents, best ZVS
- d > 1 (V1 < n*V2): increased circulating currents, partial ZVS loss possible
- d < 1 (V1 > n*V2): increased circulating currents, partial ZVS loss possible
- For wide voltage ranges (d varies significantly), advanced modulation is essential

### LV Side PCB Design
The LV side PCB carries hundreds of amps at 100 kHz. Key considerations:
- Multi-layer PCB with dedicated power layers
- Symmetric layout for current sharing between paralleled MOSFETs
- Minimize loop inductance (use bus bars, interleaved layers)
- Place high-side MOSFETs on outside, low-side on inside for best current distribution
- FEM simulation recommended for current distribution analysis

## Loss Model Summary (Krismer Chapter 4)

Total losses = conduction losses + switching losses + core losses + copper losses

### Conduction Losses
```
P_cond = R_total * IL_rms^2
R_total = 2*RS1 + R_LHV + R_tr1 + n^2*(R_tr2 + R_PCB_a + 2*RS2 + R_PCB_b)
```
Include frequency-dependent AC resistance for accurate results at 100 kHz+.

### Switching Losses
HV side: polynomial fit to measured data as function of IS1_sw and V1.
- Soft switching (IS1_sw > 2A): very low losses
- Hard switching (IS1_sw < 0): high losses, reverse recovery of body diode
- Zero current (IS1_sw = 0): moderate losses from capacitive discharge

LV side: affected strongly by package parasitic inductances.
```
P_S1_sw = 2 * fS * [E_S1_sw(IS1_a_sw) + E_S1_sw(IS1_b_sw)]  (HV side)
P_S2_sw = 2 * fS * [E_S2_sw(IS2_a_sw) + E_S2_sw(IS2_b_sw)]  (LV side)
```

### Core Losses
Use modified Steinmetz equation or iGSE for non-sinusoidal excitation.

## Ngspice Netlist Template

```spice
* Dual Active Bridge (DAB) Converter
* V1={V1}V, V2={V2}V, P={P}W, fsw={fsw}Hz
* Phase Shift Modulation

.title DAB Converter - Phase Shift Modulation

* Parameters
.param V1={V1}
.param V2={V2}
.param fsw={fsw}
.param n={n}
.param L={L}
.param phi_deg={phi_deg}
.param Rload={V2*V2/P}
.param Tperiod={1/fsw}
.param Thalf={0.5/fsw}
.param Tphi={phi_deg/360/fsw}
.param Tdead=200n
.param tstep={1/(fsw*200)}
.param tstop={100/fsw}
.param tstart={50/fsw}

* HV DC source
Vhv hv_pos hv_neg DC {V1}

* HV side full bridge gate signals
* T1,T4 pair: ON for first half-period, OFF for second
* T2,T3 pair: complementary to T1,T4
Vg14 g14 0 PULSE(0 15 {Tdead/2} 5n 5n {Thalf-Tdead} {Tperiod})
Vg23 g23 0 PULSE(0 15 {Thalf+Tdead/2} 5n 5n {Thalf-Tdead} {Tperiod})

* LV side full bridge gate signals (phase shifted by Tphi)
* T5,T8 pair
* T6,T7 pair
Vg58 g58 0 PULSE(0 15 {Tphi+Tdead/2} 5n 5n {Thalf-Tdead} {Tperiod})
Vg67 g67 0 PULSE(0 15 {Tphi+Thalf+Tdead/2} 5n 5n {Thalf-Tdead} {Tperiod})

* HV side full bridge (voltage-controlled switches)
.model SWMOD SW(Ron=0.1 Roff=1Meg Vt=7.5 Vh=3)
S1 hv_pos sw1 g14 0 SWMOD
S4 sw1_ret hv_neg g14 0 SWMOD
S2 hv_pos sw1_ret g23 0 SWMOD
S3 sw1 hv_neg g23 0 SWMOD

* Body diodes HV side
.model DHVBODY D(Is=1e-10 Rs=0.05 N=1.5 BV=600)
D1 sw1 hv_pos DHVBODY
D4 hv_neg sw1_ret DHVBODY
D2 sw1_ret hv_pos DHVBODY
D3 hv_neg sw1 DHVBODY

* Series inductor (HV side referred)
L1 sw1 pri_a {L} ic=0
R_L1 pri_a pri_b 0.1

* Ideal transformer n:1
* Primary: pri_b to sw1_ret
* Secondary: sec_a to sec_b
* Using coupled inductors
.param Lpri=1m
.param Lsec={Lpri/(n*n)}
.param K_coupling=0.9999
Lprimary pri_b sw1_ret {Lpri}
Lsecondary sec_a sec_b {Lsec}
K1 Lprimary Lsecondary {K_coupling}

* LV side full bridge
.model SWLV SW(Ron=0.005 Roff=1Meg Vt=7.5 Vh=3)
S5 lv_pos sec_a g58 0 SWLV
S8 sec_b lv_neg g58 0 SWLV
S6 lv_pos sec_b g67 0 SWLV
S7 sec_a lv_neg g67 0 SWLV

* Body diodes LV side
.model DLVBODY D(Is=1e-6 Rs=0.005 N=1.2 BV=30)
D5 sec_a lv_pos DLVBODY
D8 lv_neg sec_b DLVBODY
D6 sec_b lv_pos DLVBODY
D7 lv_neg sec_a DLVBODY

* LV DC link capacitor
Cdc2 lv_pos lv_neg 1000u ic={V2}

* HV DC link capacitor
Cdc1 hv_pos hv_neg 100u ic={V1}

* Load on LV side
Rload lv_pos lv_neg {Rload}

* Simulation
.tran {tstep} {tstop} 0 {tstep} uic

.control
run

* Steady-state measurements (last 50 cycles)
let tstart = {tstart}
let tstop = {tstop}

meas tran Vout_avg avg v(lv_pos,lv_neg) from=tstart to=tstop
meas tran Vout_ripple pp v(lv_pos,lv_neg) from=tstart to=tstop
meas tran Iin_avg avg i(Vhv) from=tstart to=tstop
meas tran IL_rms rms i(L1) from=tstart to=tstop
meas tran IL_max max i(L1) from=tstart to=tstop
meas tran IL_min min i(L1) from=tstart to=tstop

echo "=== DAB Converter Simulation Results ==="
print Vout_avg Vout_ripple
print IL_rms IL_max IL_min

let Pin = -Iin_avg * {V1}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata dab_results.csv v(lv_pos,lv_neg) v(sw1,sw1_ret) v(sec_a,sec_b) i(L1)
quit
.endc

.end
```

## Design Example (from Krismer Appendix A.2)

Specifications:
- V1: 240V to 450V (nominal 340V)
- V2: 11V to 16V (nominal 12V)
- P_rated: 2 kW bidirectional
- fS: 100 kHz
- Galvanic isolation required

Design result (suboptimal modulation):
- n = 16, L = 22.4 uH
- HV MOSFETs: SPW47N60CFD (CoolMOS), 4 devices
- LV MOSFETs: IRF2804, 64 devices (4 per switch x 8 switches x 2 parallel bridges)
- Transformer: ferrite core, N1 = 16, N2 = 1
- Peak flux density: ~129 mT
- Measured efficiency: 94.5% at nominal point (with optimal modulation)
- Average efficiency (over full V1/V2 range): 93.5%
- Converter dimensions: 273 mm x 90 mm x 53 mm
- Power density: ~1.5 kW/dm3

## Comparison with Other Isolated Topologies

| Property | DAB | LLC | Phase-Shifted Full Bridge |
|---|---|---|---|
| Bidirectional | Yes (inherent) | Difficult | No (needs redesign) |
| Soft switching | ZVS all switches | ZVS primary, ZCS secondary | ZVS primary only |
| Control complexity | Low (SPS) to High (TPS) | Medium (freq. control) | Medium |
| Light load efficiency | Poor (SPS), Good (advanced mod.) | Good (freq. control) | Fair |
| Wide voltage range | Good with advanced modulation | Limited (gain curve) | Limited |
| Power range | 500W - 50kW+ | 100W - 10kW | 500W - 10kW |
| Component count | 8 switches + transformer + L | 4 switches + transformer + Lr + Cr | 4 switches + transformer + L |

## TI DAB Practical Design (from SLUA848, TIDM_DAB)

NOTE: SLUA848 in the Papers directory does not contain DAB design content (it is about bq40z80 battery gauge learning cycles, despite its filename). The content below is from TIDM_DAB.pdf (TI reference design TIDM-02002, document TIDUEG2C).

### CLLLC Resonant DAB for HEV/EV Onboard Charger (TIDM-02002)

Reference design for a bidirectional CLLLC resonant Dual Active Bridge targeting HEV/EV onboard chargers and energy storage applications, controlled by a C2000 MCU (TMS320F28004x).

#### Key Specifications

| Parameter | Value |
|---|---|
| Primary voltage (Vprim) | 380-600 V DC (from PFC stage) |
| Secondary voltage (Vsec) | 280-450 V DC (battery) |
| Max power | 6.6 kW |
| Max output current | 18 A |
| Peak efficiency | 98% |
| PWM switching frequency | 500 kHz nominal (300-700 kHz range) |

#### Why CLLLC Over LLC or Phase-Shifted DAB

- **Full-bridge LLC vs half-bridge**: Full-bridge LLC better utilizes the transformer core on both sides, reduces current rating, enables higher power with same copper wires. Falls under the DAB category.
- **CLLLC vs LLC for bidirectional**: LLC operating in reverse power flow has switching frequency governed by transformer winding capacitance and leakage inductance, offering little control on gain and frequency. CLLLC's symmetric tank provides better control on switching frequency and an additional degree of freedom on gain in both directions.
- **Variable PFC bus voltage**: The design relies on varying the PFC output voltage (380-600 V) so that the CLLLC operates at or near resonance across the wide battery voltage range, avoiding the drawbacks of LLC operating far from resonance (increased tank current, loss of ZCS, higher switching losses).

#### CLLLC Tank Design Procedure

**Step 1: Transformer turns ratio (NCLLLC)**

Select to enable operation at resonance across the widest voltage range:
```
NCLLLC = Vprim_nom / Vsec_nom
```
For this design: NCLLLC = 1.33 (allowing resonant operation from 380 V/280 V to 600 V/450 V).

**Step 2: Magnetizing inductance (Lm)**

Ensure ZVS on primary FETs by requiring resonant tank energy > FET output capacitor energy:
```
Lm <= T_dead * T / (16 * Coss)
```
where T = 1/fsw. The effective Coss must be calculated from curve fitting (not datasheet Coss at a single voltage). Selected: Lm = 25 uH (must be less than the calculated 48 uH to account for interwinding capacitance that also needs discharging).

**Step 3: Resonant inductor ratio (Ln)**

```
Ln = Lm / Lrp
```
Select Ln to ensure voltage gain covers the operating range. Higher Ln reduces Lrp (hence losses) but limits gain variation. Selected: Ln = 13, providing at least 10% gain variation needed for PFC bus ripple.

**Step 4: Resonant capacitor (Crp)**

From the series resonant frequency:
```
fres = 1 / (2*pi*sqrt(Lrp*Crp))
```
Choose nearest available capacitor value.

#### FHA Gain Equations

Battery charging mode (BCM), primary to secondary:
```
Vsec/Vprim = (Zm || (Zrs' + RL')) / (Zrp + Zm || (Zrs' + RL'))
```
where primed quantities are referred to primary side through NCLLLC. Zm = impedance of Lm, Zrp = impedance of Lrp + Crp series, Zrs' = impedance of Lrs' + Crs' series, RL' = effective load.

Reverse power flow mode (RCM), secondary to primary:
```
Vprim/Vsec = NCLLLC * (Zm || (Zrp' + RL')) / (Zrs + Zm || (Zrp' + RL'))
```

The effective load with FHA: RL = (8/pi^2) * RL_dc.

#### Power Derating and Monotonic Gain

As load increases (RL_dc decreases), the CLLLC gain curve becomes non-monotonic below series resonance, leading to loss of ZVS on primary FETs and loss of control. The design clamps:
- BCM: RL_dc >= 20 ohm (limits max power per voltage pair)
- RCM: RL_dc >= 30 ohm

The resulting power profile is not flat 6.6 kW everywhere:
- BCM at 300 V battery / 400 V PFC: up to 4.5 kW
- BCM at 450 V battery / 600 V PFC: full 6.6 kW
- RCM at 300 V battery: up to 3 kW

#### Frequency Range vs PFC Bus Ripple

At a given operating point, PFC bus voltage ripple causes the CLLLC to sweep across frequencies:
- 10% PFC ripple (Vbus = 380-420 V at 300 V battery): frequency range 330-670 kHz
- 5% PFC ripple (Vbus = 390-410 V): frequency range 410-570 kHz

Implication: larger PFC bus capacitor reduces frequency variation and eases transformer/inductor design.

#### Active Synchronous Rectification

The design uses an active synchronous rectification scheme with Rogowski coil current sensing. The secondary side MOSFETs are driven with timing based on detected zero-crossings of the tank current. This is critical at 500 kHz switching frequency where body diode conduction would cause significant losses.

#### Measured Efficiency (500 kHz, fixed frequency)

| Vprim (V) | Vsec (V) | Pout (W) | Efficiency (%) |
|---|---|---|---|
| 375 | 280 | 1981 | 97.59 |
| 380 | 280 | 4619 | 97.53 |
| 385 | 280 | 6600 | 96.80 |
| 405 | 300 | 3300 | 97.79 |
| 410 | 300 | 6604 | 97.13 |
| 472 | 350 | 4620 | 97.72 |
| 475 | 350 | 6600 | 97.53 |
| 538 | 400 | 5283 | 97.63 |
| 540 | 400 | 6602 | 97.64 |
| 604 | 450 | 5282 | 97.30 |
| 606 | 450 | 6601 | 97.50 |

Peak measured efficiency: 97.79% at 300 V / 3.3 kW. Above 3 kW at any voltage, efficiency exceeds 97%. At light load (<1 kW), efficiency drops to 87-95% due to fixed-frequency operation traversing DCM/BCM/CCM boundaries.

#### Control Implementation

- **MCU**: TMS320F28004x with Control Law Accelerator (CLA)
- **Control variable**: PWM switching frequency (not phase shift, not duty cycle)
- **Voltage loop**: Two-pole two-zero compensator (DF22 structure), designed using SFRA-measured plant response
- **Soft-start**: Manual ramp of input voltage; no firmware-based soft-start at time of publication
- **Protection**: Over-voltage, over-current, trip flags cleared via software

#### Practical Design Notes

1. The transformer leakage inductance and magnetizing inductance variation with manufacturing tolerance directly shifts the series resonant frequency. The control must be robust to this shift.
2. Dead-band timing for primary and secondary sides are independently adjustable in software to optimize ZVS across the operating range.
3. The design supports running the control algorithm on either the C28x core or the CLA, with identical compensator coefficients.
4. Hardware design files, MATLAB tank simulation scripts, and Excel calculation spreadsheets are provided with the reference design (CLLLC_calculations.xlsx, CLLLC_tankSimulation.m).
