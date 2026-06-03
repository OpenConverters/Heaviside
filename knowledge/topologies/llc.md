---
description: Design an LLC resonant half-bridge converter from specs, calculate resonant tank and transformer, generate ngspice netlist
---

# LLC Resonant Converter Design

## When to Use
- Isolated DC/DC conversion, typically after PFC stage (380-400V bus)
- Hold-up time requirement with wide input range (e.g. 300-400V)
- High efficiency needed at medium-to-high power (200W to several kW)
- Soft-switching (ZVS primary, ZCS secondary) required for high frequency
- Fixed or narrow output voltage regulation (not suitable for wide Vout range)
- Applications: server PSUs, telecom rectifiers, TV power supplies, EV chargers, LED drivers

## Circuit Description
The LLC resonant converter uses a half-bridge (or full-bridge) driving a resonant tank consisting of a series inductor Lr, resonant capacitor Cr, and magnetizing inductance Lm (of the transformer). Output rectification is typically center-tapped or full-bridge with capacitive filter.

Components: Q1, Q2 (half-bridge MOSFETs), Lr (series resonant inductor), Cr (resonant capacitor), Lm (magnetizing inductance), T1 (transformer, n = Np/Ns), D1/D2 (output rectifier diodes or SRs), Co (output capacitor).

Key difference from series resonant converter (SRC): the magnetizing inductance Lm is deliberately small (3-8x Lr), actively participating in the resonant process. This enables ZVS over the full load range (including no load) and voltage gain above unity.

### Three Stages
1. **Square wave generator**: Half-bridge or full-bridge produces square wave Vd, switches driven at ~50% duty with dead time
2. **Resonant network**: Lr + Cr + Lm filters higher harmonics; only near-sinusoidal current flows. Current lags voltage (inductive operation) enabling ZVS
3. **Rectifier network**: Center-tapped or full-bridge diodes with capacitive output filter

### Two Resonant Frequencies
```
fo = 1 / (2*pi*sqrt(Lr*Cr))          (series resonant frequency)
fp = 1 / (2*pi*sqrt(Lp*Cr))          (parallel resonant frequency, Lp = Lr + Lm)
```
fo > fp always. Normal operation is at or near fo.

## Operating Regions

### Region 1: fs > fo (above resonance)
- Operates like SRC; Lm clamped by output voltage throughout
- Resonant current is sinusoidal, output current is continuous
- Secondary diodes NOT softly commutated (hard turn-off)
- Lower circulating current, better efficiency for low-Vout applications
- Frequency increases at light load (may need frequency clamping)

### Region 2: fp < fs < fo (below resonance)
- Two resonant intervals per half cycle: Lr-Cr resonance while Lm clamped, then Lr-Lm-Cr resonance after secondary current reaches zero
- Output current is discontinuous (ZCS for secondary diodes)
- ZVS maintained by magnetizing current
- Higher circulating current than Region 1
- Preferred for high-Vout (avoids reverse recovery of secondary diodes)
- Narrower frequency range since fs is bounded below by fp

### Region 3: fs < fp (capacitive region, AVOID)
- ZCS occurs on primary switches (body diodes reverse-recovered)
- Gain slope reverses: output becomes uncontrollable
- Must be avoided by limiting minimum switching frequency above peak gain frequency

### Design Rule
Always ensure the minimum switching frequency stays above the peak gain frequency with margin. The peak gain frequency lies between fp and fo. At full load (worst case), the peak gain frequency approaches fo.

## Design Procedure

### Step 1: Define System Specifications
```
Eff = estimated efficiency (0.88-0.92 low Vout, 0.92-0.96 high Vout)
Pin = Po / Eff
Vin_max = V_PFC_nominal (e.g. 400V)
Vin_min = sqrt(V_PFC^2 - 2*Pin*T_holdup/C_DClink)
```
Where T_holdup = hold-up time (typ. 20ms), C_DClink = DC link capacitor.

### Step 2: Determine Voltage Gain Range
Choose m = Lp/Lr (typically 3 to 7; higher m = better coupling but less gain range):
```
Mmin = m / (m - 1)                    (gain at fs = fo, for Vin_max)
Mmax = Mmin * Vin_max / Vin_min       (gain at min input voltage)
```
Where gain M = 2*n*Vo / Vin for half-bridge (M = n*Vo/Vin for full-bridge).

With an integrated transformer (leakage = Lr), the gain at fo includes a virtual gain factor MV = Lp / (Lp - Lr) due to secondary-side leakage.

### Step 3: Determine Transformer Turns Ratio
```
n = Np/Ns = Vin_max * Mmin / (2 * (Vo + Vf))
```
Where Vf = rectifier diode forward voltage drop (0 for SR). For full-bridge primary, remove the factor of 2.

### Step 4: Calculate Equivalent Load Resistance
Using fundamental harmonic approximation (FHA):
```
Rac = 8*n^2*Vo^2 / (pi^2 * Po)
```

### Step 5: Design Resonant Network
From peak gain curves (M_peak vs Q for chosen m), select Q that provides the required Mmax with 10-20% margin:
```
Required peak gain = Mmax * 1.15      (15% margin for ZVS stability)
```
Read Q from curves for chosen m value. Then:
```
Cr = 1 / (2*pi*fo*Q*Rac)
Lr = 1 / ((2*pi*fo)^2 * Cr)
Lp = m * Lr
Lm = Lp - Lr
```
Choose fo (resonant frequency, typically 80-200 kHz depending on application).

### Step 6: Design Transformer
Minimum switching frequency is found from gain curves at Mmax (full load, Vin_min).
```
Np_min = n * (Vo + Vf) / (2 * fs_min * deltaB * Ae * MV)
```
Where deltaB = flux density swing (0.3-0.4T), Ae = core cross-section area, MV = Lp/(Lp-Lr).

Choose Ns (integer), then Np = n * Ns >= Np_min.

### Step 7: Transformer Construction
For integrated magnetics (leakage inductance = Lr):
- Use sectional bobbin to control leakage inductance Lr
- Adjust air gap to set Lm (magnetizing inductance)
- Lp is measured with secondary open; Lr with secondary shorted
- Gap length primarily controls Lp; winding separation controls Lr

For discrete magnetics:
- Separate inductor for Lr on its own core
- Transformer with gap for Lm

### Step 8: Select Resonant Capacitor
```
I_Cr_rms = sqrt((pi*Po / (n*(Vo+Vf)*Eff))^2 + (4*n*(Vo+Vf) / (2*pi*fo*MV*(Lp-Lr)))^2) / (2*sqrt(2))
V_Cr_nominal = Vin_max/2 + I_Cr_rms / (2*pi*fo*Cr)
V_Cr_max = Vin_max/2 + I_OCP / (2*pi*fs_min*Cr)     (at OCP trip point)
```
Use film or C0G/NP0 ceramics for low ESR and AC current handling.

### Step 9: Select Output Rectifier Diodes
```
V_D_stress = 2 * (Vo + Vf) = 2 * n * Vin / (2*n)     (center-tapped)
I_D_rms = (pi / (2*sqrt(2))) * Io                       (for operation at fo)
I_D_avg = Io / 2                                        (per diode, center-tapped)
```
For synchronous rectifiers: use MOSFETs, drive from secondary-side controller or self-driven.

### Step 10: Select Output Capacitor
```
I_Co_rms = sqrt(I_D_rms^2 - Io^2)
delta_Vo = I_Co_rms * ESR_Co                            (dominated by ESR)
```

## Key Equations

### Gain Function (Fundamental Harmonic Approximation)

With separate Lr inductor:
```
M(omega) = m * j*omega_n / ((m-1) * (j*omega_n)^3 + j*omega_n * (m*Q^2 + 1) + (m-1)*Q*(1 - omega_n^2) * j ... )
```

Simplified gain magnitude:
```
|M| = 1 / sqrt((1 + 1/m * (1 - 1/omega_n^2))^2 + Q^2 * (omega_n - 1/omega_n)^2)
```
Where:
```
omega_n = fs / fo                     (normalized switching frequency)
m = Lp / Lr = (Lr + Lm) / Lr
Q = sqrt(Lr/Cr) / Rac                (quality factor)
Rac = 8*n^2*Ro / pi^2                (AC equivalent load resistance)
```

For integrated transformer (secondary leakage considered):
```
|M| = MV / sqrt((1 + 1/m_e * (1 - 1/omega_n^2))^2 + Qe^2 * (omega_n - 1/omega_n)^2)
```
Where:
```
m_e = Lp / Lr                         (measured from primary with sec open/shorted)
Qe = sqrt(Lr/Cr) / Rac_e
MV = Lp / (Lp - Lr)                   (virtual gain due to secondary leakage)
```

### At Resonant Frequency (fs = fo)
```
M(fo) = m / (m - 1) = Lp / (Lp - Lr)
```
This gain is independent of load -- a key advantage of LLC.

### Resonant Tank Design Summary
```
fo = 1 / (2*pi*sqrt(Lr*Cr))
fp = 1 / (2*pi*sqrt(Lp*Cr))
Zo = sqrt(Lr / Cr)                    (characteristic impedance)
Q = Zo / Rac                          (quality factor)
m = Lp / Lr = Lm/Lr + 1              (inductance ratio)
```

### Dead Time and ZVS Conditions
For ZVS, the primary-side MOSFET output capacitance Coss must be discharged by the magnetizing current during dead time:
```
I_Lm_peak = n*(Vo+Vf) / (2*Lm*fs)    (peak magnetizing current at turn-off)
t_dead >= 2 * Coss * Vin / I_Lm_peak  (minimum dead time for ZVS)
```
The magnetizing current at the switching instant provides the ZVS energy. Larger Lm reduces magnetizing current (less ZVS margin but lower conduction loss). Smaller Lm ensures ZVS but increases circulating current.

Design tradeoff for Lm:
```
Lm <= t_dead / (16 * Coss * fs)       (ensures ZVS with adequate margin)
```

ZVS is maintained:
- Above peak gain frequency (inductive region): resonant current lags Vd
- Over full load range: Lm provides minimum turn-off current at no load
- Lost in capacitive region (below peak gain frequency): AVOID

### Transformer Design
```
Bmax = n * (Vo + Vf) / (4 * fs * Np * Ae)    (peak flux density in half-cycle)
```
For integrated transformer:
- Leakage inductance (Lr): controlled by winding separation, bobbin sectioning
- Magnetizing inductance (Lm): controlled by air gap length
- Practical: build transformer, measure Lp (sec open) and Lr (sec shorted), iterate

### Output Rectifier
Center-tapped:
```
V_diode = 2 * n * (Vo + Vf) / n = 2 * (Vo + Vf)     (voltage stress per diode)
I_diode_avg = Io / 2                                   (per diode)
```
Full-bridge:
```
V_diode = (Vo + Vf)                                    (voltage stress per diode)
I_diode_avg = Io / 2                                   (per diode)
```

## Component Stresses

### Primary MOSFETs (half-bridge)
```
V_Q_max = Vin_max                               (each MOSFET blocks full Vin)
I_Q_rms = I_Lr_rms / sqrt(2)                    (each switch carries half the resonant current)
I_Q_turnoff = I_Lm_peak = n*(Vo+Vf)/(2*Lm*fs)  (turn-off current = magnetizing current)
```
Turn-on is ZVS (zero loss). Turn-off loss exists but is manageable since turn-off current equals magnetizing current (not full load current).

### Resonant Capacitor
```
V_Cr_dc = Vin / 2                               (DC bias, half-bridge)
V_Cr_peak = Vin/2 + I_Lr_peak / (2*pi*fs*Cr)
I_Cr_rms = I_Lr_rms                             (series path, same current)
```
Must handle full AC RMS current. Use film capacitors or high-quality ceramics (C0G/NP0).

### Resonant Inductor
```
I_Lr_rms = sqrt(I_load_component^2 + I_magnetizing_component^2)
I_Lr_peak = I_Lr_rms * sqrt(2)                  (approximately, for sinusoidal)
V_Lr_max = Vin/2 + n*(Vo+Vf)                    (voltage across Lr during transients)
```

### Output Capacitor
```
I_Co_rms ≈ Io * sqrt(pi^2/8 - 1) ≈ 0.483 * Io  (at resonance)
```
ESR-dominated ripple for electrolytic caps.

## High-Frequency Considerations (from Fei 2018)

### GaN-Based MHz LLC Design
- At MHz switching frequencies, PCB-integrated magnetics become practical
- Matrix transformer: paralleling multiple small transformers to reduce winding loss and leakage
- PCB windings: use planar cores (EI, ELP, ER) with PCB traces as windings
- Shielding layers between primary and secondary reduce CM noise by 30+ dB
- Optimal copper thickness is ~2x skin depth at switching frequency
- Core loss becomes significant: use low-loss materials (ML91S, 3F46, etc.)

### PCB Transformer Design Guidelines
- Interleave primary and secondary layers to minimize proximity effect
- Use multiple secondary windings in parallel to reduce AC resistance
- Optimal winding width minimizes combined DC and AC resistance
- Consider matrix transformer for high-current outputs (>20A)
- Shielding layer between P and S reduces inter-winding capacitance

### Synchronous Rectifier Driving
- At high frequencies, SR timing becomes critical
- Body diode conduction during dead time causes significant loss
- Adaptive SR driving adjusts timing based on drain-source voltage sensing
- Smart SR driver ICs available for automatic timing

## Integrated Magnetics (from Bo Yang 2003)

Two integration approaches:
1. **Design A**: Transformer magnetizing inductance = Lm, separate core for Lr
2. **Design B**: Single core implements Lr (via leakage), Lm (via gap), and transformer
   - Lr set by winding separation (sectional bobbin, or primary-secondary spacing)
   - Lm set by air gap in center leg
   - Most common practical approach

Benefits: reduced component count, smaller volume, lower cost, potentially lower loss.

## Overload Protection Methods (from Bo Yang 2003)
1. **Increase switching frequency**: raises fs to limit current, but LLC gain is complex at high frequency
2. **Variable frequency + PWM**: switch from frequency control to PWM at overload
3. **Clamping diode**: add diode across Lm to clamp voltage, prevents entering Region 3 (ZCS)

## Small-Signal Characteristics
- LLC has a right-half-plane zero at certain operating points
- At fs = fo: behaves like first-order system (easy to compensate)
- Below fo: more complex, resembles second-order with RHP zero
- At light load: nearly independent of load (advantage for regulation)
- Compensation: simple PI or Type 2 compensator is usually sufficient near fo
- Bandwidth typically limited to 1/10 to 1/5 of switching frequency

## Ngspice Netlist Template

```spice
* LLC Resonant Half-Bridge Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fo={fo}Hz

.title LLC Resonant Converter

* Parameters
.param Vin={Vin}
.param Vout={Vout}
.param Iout={Iout}
.param Rload={Vout/Iout}
.param fs={fs}
.param Lr={Lr}
.param Cr={Cr}
.param Lm={Lm}
.param n={n}
.param tdead={tdead}
.param tstep={1/(fs*200)}
.param tstop={80/fs}
.param tstart={40/fs}

* Input supply
Vin in 0 DC {Vin}

* Half-bridge gate drives (complementary with dead time)
Vg1 gate1 0 PULSE(0 15 0 1n 1n {0.5/fs - tdead} {1/fs})
Vg2 gate2 0 PULSE(0 15 {0.5/fs} 1n 1n {0.5/fs - tdead} {1/fs})

* Half-bridge switches (ideal switch model)
.model SW1 SW(Ron=0.1 Roff=1Meg Vt=7.5 Vh=0.5)
S1 in sw gate1 0 SW1
S2 sw 0 gate2 0 SW1

* Body diodes for ZVS
.model DBODY D(Is=1e-12 Rs=0.01 N=1.5 BV=800)
D1 sw in DBODY
D2 0 sw DBODY

* MOSFET output capacitances (optional, for ZVS transient analysis)
* Coss1 in sw 100p
* Coss2 sw 0 100p

* Resonant tank
Cr sw cr_lr {Cr} ic={Vin/2}
Lr cr_lr prim {Lr} ic=0

* Transformer model (ideal transformer + magnetizing inductance)
* Magnetizing inductance on primary side
Lm prim prim_ret {Lm} ic=0

* Ideal transformer using coupled inductors
* Primary: Lp_xfmr, Secondary: Ls_xfmr, coupling k=1
.param Lp_xfmr=1m
.param Ls_xfmr={Lp_xfmr/(n*n)}
Lp prim prim_ret {Lp_xfmr}
Ls sec_a sec_ct {Ls_xfmr}
K1 Lp Ls 1

* Center-tapped secondary (second half-winding)
Ls2 sec_ct sec_b {Ls_xfmr}
Lp2 prim prim_ret {Lp_xfmr}
K2 Lp2 Ls2 1

* Primary return to half-bridge midpoint ground reference
Rwire prim_ret 0 0.001

* Output rectifier diodes
.model DRECT D(Is=1e-5 Rs=0.02 N=1.05 BV=100)
D_rect1 sec_a out DRECT
D_rect2 sec_b out DRECT

* Center tap is output ground
Rct sec_ct 0_sec 0.001

* Output filter
Co out 0_sec {Co} ic={Vout}
Rload out 0_sec {Rload}

* Connect primary and secondary grounds
* (In isolated converter, these are separate; for simulation, tie through large R or use .nodeset)
Riso 0 0_sec 1Meg

.param Co={1/(2*pi*fs*Rload*0.01)}

* Simulation
.tran {tstep} {tstop} 0 {tstep} uic

.control
run

let tstart = {tstart}
let tstop = {tstop}
meas tran Vout_avg avg v(out,0_sec) from=tstart to=tstop
meas tran Vout_ripple pp v(out,0_sec) from=tstart to=tstop
meas tran ILr_rms rms i(Lr) from=tstart to=tstop
meas tran ILr_max max i(Lr) from=tstart to=tstop
meas tran ILm_peak max i(Lm) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== LLC Resonant Converter Simulation Results ==="
print Vout_avg Vout_ripple
print ILr_rms ILr_max ILm_peak
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata llc_results.csv v(out,0_sec) v(sw) i(Lr) i(Lm)
quit
.endc

.end
```

**Note on LLC simulation**: The ideal transformer model above uses coupled inductors. For more accurate simulation, replace with a VCVS/CCCS-based ideal transformer or use mutual inductance with realistic coupling. The magnetizing inductance Lm is in parallel with the ideal transformer primary. Simulation at resonance (fs = fo) should show near-sinusoidal resonant current and output voltage close to the designed value.

## Design Example Summary (from AN-4151)

Specifications: 400V input (from PFC), 24V/8A output, hold-up time 20ms, C_DClink = 220uF

| Parameter | Value |
|-----------|-------|
| Vin_max | 400V |
| Vin_min | 349V |
| m = Lp/Lr | 5 |
| n = Np/Ns | 9 |
| fo | 100 kHz |
| Q | 0.4 |
| Cr | 22 nF |
| Lr | 118 uH |
| Lp (Lm + Lr) | 630 uH |
| Lm | 512 uH |
| fs_min | 72 kHz |
| Efficiency | 94% at full load |

---

## Resonant Converter Theory (from Erickson Ch22)

Source: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapter 22, pp.931-990.

This section provides the theoretical foundation for LLC and other resonant converter analysis, complementing the practical design procedure above.

### First Harmonic Approximation (FHA) for LLC

The LLC tank is driven by a square-wave voltage source and loaded by a rectifier presenting an effective AC resistance. The FHA models only the fundamental component.

**Square wave fundamental amplitude:**
```
v_s1_peak = 4*V_g/pi     (full-bridge)
v_s1_peak = 2*V_g/pi     (half-bridge)
```

**Effective AC load resistance (rectifier + capacitive filter):**
```
R_e = (8/pi^2) * n^2 * R_load
```

This R_e is the load seen by the tank network at the switching frequency.

### LLC Gain Equation (FHA)

The LLC tank has two resonant frequencies:
```
f_r = 1/(2*pi*sqrt(L_r * C_r))           (series resonance)
f_m = 1/(2*pi*sqrt((L_r + L_m) * C_r))   (parallel resonance, lower)
```

Voltage conversion ratio:
```
M(F, Q, m) = 1 / sqrt((1 + 1/m - 1/(m*F^2))^2 + Q^2*(F - 1/F)^2)
```

where:
- F = f_s/f_r (normalized switching frequency)
- Q = sqrt(L_r/C_r) / R_e (quality factor)
- m = L_m/L_r (inductance ratio)

### Operating Regions

**Region 1: f_s > f_r (above series resonance)**
- M < 1 (buck mode)
- Tank impedance is inductive (current lags voltage)
- ZVS guaranteed for all loads
- Preferred region for high efficiency at full load

**Region 2: f_m < f_s < f_r (between resonances)**
- M > 1 possible (boost mode)
- ZVS still achievable if sufficient magnetizing current
- Used during low-line or holdup conditions
- Tank impedance can be inductive (ZVS) depending on load

**Region 3: f_s < f_m (below both resonances)**
- Tank impedance is capacitive
- ZCS operation -- body diode reverse recovery, NOT suitable for MOSFETs
- Must be avoided in MOSFET-based designs
- The boundary is load-dependent: heavier loads push the ZVS/ZCS boundary to higher frequencies

### ZVS Conditions for LLC

ZVS requires the tank current to lag the tank voltage (inductive impedance). At the switching instant, the tank current must be sufficient to charge/discharge the MOSFET output capacitances during the dead time.

**Minimum condition:**
```
(1/2) * L_m * I_Lm_peak^2 >= 2 * C_oss * V_bus^2

I_Lm_peak = V_out * n / (4 * L_m * f_s)    (magnetizing current at the switching instant)
```

**Dead time requirement:**
```
t_dead >= pi * sqrt(L_m * 2*C_oss)     (quarter resonant period)
t_dead <= T_s/2 - 1/(2*f_r)           (must not exceed the available time)
```

### Load-Dependent Properties

**Inverter output characteristics:** The LLC converter output voltage and current are constrained by the tank transfer function. The operating point moves along the gain curves as load varies.

**Transistor current vs. load:**
- At full load: resonant current is dominated by the load component
- At no load: resonant current equals the magnetizing current (circulating current)
- The LLC maintains lower circulating currents than the pure parallel resonant converter because L_m only carries the magnetizing current, not the full load current

**ZVS boundary dependence on load:**
- At heavy load: ZVS may be lost if f_s is too close to f_m (capacitive region expands)
- At light load: ZVS is easily maintained (magnetizing current dominates)
- The gain curve peak shifts with Q: higher Q pushes the peak to higher frequencies

### Exact Characteristics (State-Plane Analysis)

The FHA is accurate near resonance (F ~ 1) and for moderate Q. For operation far from resonance, the exact analysis using state-plane (phase-plane) techniques gives more accurate results.

**Series resonant converter exact characteristics:**
The output characteristic in normalized form (M vs J) traces elliptical paths:
```
(M - M_0)^2 + (J / J_0)^2 = 1
```

Different operating modes (continuous, discontinuous) have different exact expressions. The FHA underestimates the gain range in some operating modes.

**Practical note:** For most LLC designs operating near resonance, the FHA is sufficiently accurate for initial design. Final verification should use SPICE simulation or exact analysis for boundary operating conditions (minimum input voltage, maximum load).

## Exact LLC Analysis (from Kazimierczuk Ch17-18)

Reference: Kazimierczuk & Czarkowski, "Resonant Power Converters" 2nd ed., Wiley-IEEE 2011, Chapters 17-18.

**Nomenclature mapping**: Kazimierczuk treats the LLC as a CLL converter (Ch18). In his framework:
- L1 = series resonant inductance = Lr in standard LLC notation
- L2 = parallel (magnetizing) inductance = Lm in standard LLC notation
- C = resonant capacitor = Cr in standard LLC notation
- A = L1/L2 = Lr/Lm (inverse of the usual m = Lm/Lr ratio)

### Exact Gain Beyond FHA

Kazimierczuk derives the lossless DC voltage transfer function for the CLL (LLC) converter with a bridge rectifier:

```
Mv = 4*eta_I*eta_tr / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2))
```

Where:
- f0 = 1/(2*pi*sqrt(C*(L1+L2))) = 1/(2*pi*sqrt(Cr*(Lr+Lm))) -- the LOWER resonant frequency (not fr!)
- A = L1/L2 = Lr/Lm = 1/m (reciprocal of the usual inductance ratio)
- QL = Ri/(omega0*(L1+L2)) -- loaded quality factor referenced to Ri

This is the exact (within the high-QL sinusoidal approximation) gain including both resonant frequencies.

### Load-Independent Operating Point

The gain is INDEPENDENT of load at the normalized frequency:

```
f_independent/f0 = sqrt(1 + L2/L1) = sqrt(1 + 1/A) = sqrt(1 + m)
```

In standard LLC terms: f_independent = sqrt(1 + Lm/Lr) * fp, which equals fr = 1/(2*pi*sqrt(Lr*Cr)).

This confirms the well-known result: at f = fr (series resonant frequency), the LLC gain is load-independent, and equals M = Lp/(Lp-Lr) = m/(m-1).

**Critical advantage of CLL/LLC over LCC/SPRC**: At this load-independent frequency, the tank impedance is INDUCTIVE, meaning ZVS is naturally achieved. In contrast, the SPRC (LCC) has its load-independent point in the CAPACITIVE region (ZCS), which is undesirable for MOSFETs.

### Operating Mode Boundaries

The CLL/LLC has two key frequencies that define operating regions:

```
f0 = 1/(2*pi*sqrt(C*(L1+L2)))     Lower resonant frequency (= fp in standard LLC)
fr = 1/(2*pi*sqrt(C*L1))           Upper resonant frequency (= fr in standard LLC)
```

Three regions:
1. **f > fr**: Below-unity gain (buck mode), ZVS guaranteed, sinusoidal tank current, secondary diodes hard-commutated
2. **f0 < f < fr**: Above-unity gain possible (boost mode), ZVS maintained by magnetizing current, secondary diodes ZCS
3. **f < f0**: Capacitive region, ZCS on primary (AVOID for MOSFETs)

### Full Efficiency Model Including All Parasitics

Kazimierczuk's approach includes all loss mechanisms in closed form:

```
eta = eta_I * eta_R * eta_tr

eta_I includes: rDS (MOSFET on-resistance), rL (inductor ESR), rc (capacitor ESR)
eta_R includes: VF (diode threshold), RF (diode resistance), rLf (filter inductor ESR)
eta_tr: transformer efficiency
```

For the CLL with bridge rectifier:

```
eta_R = 1 / (1 + 2*VF/Vo + 4*RF/RL + pi^2*rc/(8*RL))
```

The overall Mv including losses:

```
Mv = 4*eta_I*eta_tr / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2) * (1 + 2*VF/Vo + 4*RF/RL + ...))
```

### Component Stress Equations (from Example 18.1)

For a CLL/LLC converter with given Vi, Vo, Io, A, QL, f0:

```
Peak switch current (= peak resonant inductor current):
ISM = Im = (2*Vi)/(pi*Ri) * sqrt(1 + [QL*(f/f0)*(1+A)]^2)

Peak switch voltage:
VSM = Vi  (half-bridge)
VSM = Vi/2  (per switch in full-bridge, each blocks Vi)

Peak voltage across L1 (Lr):
VL1m = omega*L1*Im

Peak voltage across L2 (Lm):
VL2m = 2*Vi / (pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + ...))

Peak voltage across C (Cr):
VCm = Im / (omega*C)

Diode peak current:
IDM = Io  (per diode, bridge rectifier)

Diode peak voltage:
VDM = n*Vo  (half-wave), 2*(Vo+VF) (center-tapped)
```

### Design Optimization Guidelines

From the CLL/LLC analysis, the key design tradeoffs:

1. **Inductance ratio A = Lr/Lm (= 1/m)**:
   - Lower A (higher m): narrower gain range but lower circulating current, better efficiency near resonance. Sensitivity of Mv to load DECREASES.
   - Higher A (lower m): wider gain range but higher magnetizing current, more conduction loss.

2. **Quality factor QL**:
   - Lower QL: wider gain range, easier to regulate across load, but larger components.
   - Higher QL: sharper gain curves, more efficient at resonance, but narrower operable range.
   - Kazimierczuk notes (Eq. 9.35 for CLL inverter): maximum efficiency occurs at QL = (f/f0)/2.

3. **Frequency selection**:
   - f0 should be high enough for small components but low enough for practical switching
   - Typical range: 80-200 kHz for Si MOSFETs, 500 kHz-2 MHz for GaN
   - The operating frequency range is bounded: f_min > peak gain frequency (to stay in ZVS region)

### Safety Considerations

From the Kazimierczuk analysis:
- **Short circuit**: NOT safe near fr (the series resonant frequency of C-L1). At RL = 0, L2 is shorted, and the circuit becomes a pure series resonant C-L1. At f = fr, impedance drops to parasitic resistance only.
- **Open circuit**: NOT safe near f0. At RL = infinity, the full inductance L1+L2 resonates with C. At f = f0, excessive current.
- **Protection**: The control circuit must clamp the minimum switching frequency above the peak gain frequency AND implement overcurrent protection.

## CLL/LLC Design Equations for All Rectifier Types (from Kazimierczuk Ch17-18)

Reference: Kazimierczuk & Czarkowski, "Resonant Power Converters" 2nd ed., Wiley-IEEE 2011, Chapter 17 (SPRC/LCC) and Chapter 18 (CLL/LLC). Complements the analysis above with rectifier-specific expressions and a worked design procedure.

In Kazimierczuk's notation, the LLC converter is a CLL converter (Ch18) where:
- C = resonant capacitor = Cr
- L1 = series resonant inductor = Lr
- L2 = parallel (magnetizing) inductor = Lm
- A = L1/L2 = Lr/Lm (reciprocal of the standard m = Lm/Lr ratio)

The resonant tank is a series C-L1 with L2 in parallel with the load.

### Exact DC Voltage Transfer Functions

**Half-bridge CLL with half-wave rectifier:**
```
Mv = (2*eta_I*eta_tr) / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2) * (1 + VF/Vo + (RF+rLF)/RL + ahw*pi^2/(2*RL)))
```

**Half-bridge CLL with center-tapped rectifier:**
```
Mv = (4*eta_I*eta_tr) / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2) * (1 + VF/Vo + (RF+rLF)/RL + act*pi^2/(4*RL)))
```

**Half-bridge CLL with bridge rectifier:**
```
Mv = (4*eta_I*eta_tr) / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2) * (1 + 2*VF/Vo + 2*RF/RL + pi^2*rc/(8*RL)))
```

**Full-bridge versions**: multiply Mv by 2 (all other equations remain the same, except r = 2*rDS + rL + rc).

### Key Definitions

```
f0 = 1 / (2*pi*sqrt(C*(L1+L2)))           Lower resonant frequency (= fp in LLC notation)
fr = 1 / (2*pi*sqrt(C*L1))                 Upper resonant frequency (= fr in LLC notation)
A = L1/L2 = Lr/Lm                          Inductance ratio (= 1/m in standard LLC)
QL = Ri / (omega0 * (L1+L2))               Loaded quality factor at f0
Ri = pi^2*n^2*RL / (2*eta_R)               Rectifier input resistance (for center-tapped)
Z0 = sqrt((L1+L2)/C)                       Characteristic impedance
```

### Load-Independent Operating Frequency

The CLL/LLC gain is INDEPENDENT of load at:
```
f_ind / f0 = sqrt(1 + L2/L1) = sqrt(1 + 1/A) = sqrt(1 + m)
```

In absolute frequency:
```
f_ind = fr = 1/(2*pi*sqrt(C*L1)) = 1/(2*pi*sqrt(Cr*Lr))
```

This is the series resonant frequency. At this point:
- Gain is determined solely by the tank parameters, not load
- The impedance seen by the switches is INDUCTIVE (ZVS guaranteed)
- The gain value at f_ind is: Mv = Mv_lossless * eta factors

This is the key advantage of CLL/LLC over LCC/SPRC: the LCC has its load-independent point in the CAPACITIVE region, causing ZCS (bad for MOSFETs).

### Maximum Efficiency Condition

From Kazimierczuk's analysis of the CLL inverter (Eq. 9.35):
```
QL_optimal = (f/f0) / 2
```

Maximum inverter efficiency occurs when the loaded quality factor equals half the normalized switching frequency. This provides a design target for selecting QL.

### Inverter Efficiency (Including All Parasitics)

```
eta_I = 1 / (1 + pi^2*r / (Ri * ((1+A)^2*(1-(f/f0)^2)^2 + (1/QL*(f/f0 - A*f0/((1+A)*f)))^2)))
```

where r = rDS + rL + rC (half-bridge) or r = 2*rDS + rL + rC (full-bridge).

### Component Stress Equations

**Peak switch current (= peak inductor current)**:
```
ISM = Im = (2*Vi) / (pi*Ri) * sqrt(1 + [QL*(f/f0)*(1+A)]^2)
```

For half-bridge: VSM = Vi. For full-bridge: VSM = Vi (each switch blocks full bus).

**Peak voltage across L1 (series inductor)**:
```
VL1m = omega*L1*Im
```

**Peak voltage across L2 (magnetizing inductor)**:
```
VL2m = 2*Vi / (pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2))
```

**Peak voltage across C (resonant capacitor)**:
```
VCm = Im / (omega*C)
```

**Diode peak current and voltage (bridge rectifier)**:
```
IDM = Io
VDM = 2*(Vo + VF)   (center-tapped)
VDM = Vo + VF        (bridge)
```

### Design Procedure (from Ch18 Example 18.1)

Given: Vi = 250V, Vo = 40V, Io = 0 to 2A, eta = 90%.

1. **Choose topology parameters**: A = 1 (L1 = L2), QL = 0.2 at full load, f0 = 100 kHz.

2. **Calculate rectifier parameters**:
```
Ri = pi^2*RL/(2*eta_R) * (1 + VF/Vo + (RF+rLF)/RL)  (for center-tapped)
```

3. **Find resonant circuit values**:
```
C = QL / (omega0 * Rimin) = 3.1 nF
L = Rimin / (omega0 * QL) = 807 uH
L1 = L / (1+A) = 403.5 uH
L2 = L*A / (1+A) = 403.5 uH   (for A=1)
```

4. **Calculate switching frequency**: solve Mv equation numerically for f/f0 -> f/f0 = 1.471, f = 147.1 kHz.

5. **Verify component stresses**:
```
ISM = 1.64 A
VL1m = 612 V
VL2m = 143.3 V
VCm = 572 V
VSM = Vi = 250 V
```

### Mapping to Standard LLC Notation

| Kazimierczuk (CLL) | Standard LLC | Relation |
|---|---|---|
| C | Cr | Same |
| L1 | Lr | Same |
| L2 | Lm | Same |
| A = L1/L2 | 1/m = Lr/Lm | A = 1/m |
| f0 = 1/(2*pi*sqrt(C*L)) | fp = 1/(2*pi*sqrt(Cr*(Lr+Lm))) | Same |
| fr = 1/(2*pi*sqrt(C*L1)) | fr = 1/(2*pi*sqrt(Cr*Lr)) | Same |
| QL = Ri/(omega0*L) | Q = sqrt(Lr/Cr)/Rac | Different definitions |
| Ri | Rac = 8*n^2*RL/pi^2 | Ri includes eta_R correction |

### Boundary Conditions Between Operating Modes

**Capacitive-inductive boundary**: The resonant frequency fr = 1/(2*pi*sqrt(C*L1)) forms the boundary. This frequency is load-dependent for the general case but becomes:
- For f > fr: inductive load (ZVS) -- preferred for MOSFET designs
- For f < fr and f > f0: still inductive if magnetizing current is sufficient
- For f < f0: capacitive load (ZCS) -- AVOID

**Mode transitions**:
- Above fr: resonant current is continuous sinusoid, secondary current continuous
- Between f0 and fr: resonant current has two intervals per half cycle, secondary current discontinuous (ZCS on secondary diodes)
- Below f0: capacitive region, hard switching on primary

### Short-Circuit and Open-Circuit Safety

**Short circuit at output (RL = 0)**: L2 is shorted, circuit becomes series C-L1. At f = fr, impedance = parasitic resistance only. Overcurrent WILL occur. Must implement frequency clamping and overcurrent protection.

**Open circuit at output (RL = infinity)**: full inductance L1+L2 resonates with C. At f = f0, excessive current through tank. Control must prevent operation near f0 at no load.

**Safe operating frequency**: always maintain f > fr (or at minimum, f significantly above the peak gain frequency which depends on load).

## GaN in LLC Converters (from Lidow et al.)

Source: Lidow et al., "GaN Transistors for Efficient Power Conversion" (3rd ed, 2020), Chapters 8 and 10.

### Why GaN Excels in LLC

The LLC converter operates with ZVS on the primary side and ZCS (or ZVS) on the secondary, making it a soft-switching topology where GaN's advantages are particularly strong:

1. **Lower QOSS reduces ZVS transition time**: At 48 V, GaN QOSS is 41.5 nC vs 57-71 nC for comparable RDS(on) MOSFETs. Shorter ZVS transitions increase the effective duty cycle and reduce RMS currents.

2. **Lower QG reduces gate drive loss**: At 1 MHz with 4 primary devices, gate drive power adds up quickly. GaN QG at 5 V drive is ~1 nC vs 6-8 nC for MOSFETs at 7-10 V drive. The gate FOM (QG * RDS(on) * VG) shows a 6-13x advantage for GaN.

3. **Lower QOSS means less energy needed for ZVS**: Use output charge (not output capacitance) for ZVS analysis. COSS is highly nonlinear; at 48 V, a MOSFET may have lower COSS than GaN but substantially higher QOSS.

4. **No reverse recovery**: Eliminates losses during dead-time body diode conduction on the secondary synchronous rectifiers.

### LLC FOM Comparison (1 MHz, 900 W, 48 V-12 V Example)

| Parameter | EPC2053 (GaN, 100V) | BSC037N08NS5 (Si, 80V) | BSC040N10NS5 (Si, 100V) |
|-----------|-------|------|------|
| RDS(on) typ | 3.2 mohm | 3.4-4.0 mohm | 3.4-3.8 mohm |
| QOSS @ 48V | 41.5 nC | 57.3 nC | 70.7 nC |
| QG @ 48V | 1.04 nC | 6.34 nC | 7.59 nC |
| FOM_gate (mohm-nC-V) | 126 | 784-1360 | 931-1700 |
| FOM_QOSS (mohm-nC) | 133 | 195-229 | 241-269 |

### Practical LLC Design with GaN

**Achieved performance** (1 MHz, 48 V-12 V, full-bridge primary, center-tapped SR):
- 900 W output, peak efficiency 98.4%
- Exceeds 98% over wide load range (200-800 W) and input range (40-60 V)
- Maximum temperature 64 degC at 900 W with 400 LFM airflow
- PCB-integrated matrix transformer in 14-layer board, Lm = 2.2 uH, Cr = 4.2 uF
- Board dimensions: 36 mm x 37 mm x 7 mm

**48 V-6 V variant** (8:1 ratio, same switching frequency):
- 900 W output, peak efficiency ~98.1%
- Maximum temperature 60 degC at 900 W with 400 LFM airflow
- Uses EPC2023 (1.15 mohm, 30 V) secondary rectifiers, 2 parallel per position

### LLC Design Guidelines with GaN

1. **Use QOSS, not COSS, for ZVS design**: Calculate output charge by integrating the nonlinear COSS curve from 0 to VDS_operating. Single-point datasheet values are insufficient.

2. **Gate voltage optimization**: 5 V gate drive for GaN gives lower total loss than 10 V drive for MOSFETs, even though MOSFET RDS(on) is lower at 10 V, because the gate power savings outweigh the conduction increase.

3. **PCB-integrated transformer**: At 1 MHz, the transformer can be embedded in a 14-layer PCB with planar windings. GaN's low switching loss enables this frequency without excessive device loss. The transformer and magnetics become the dominant loss mechanism (~50% of total), not the transistors (~28%).

4. **Secondary rectifier selection**: Use the lowest available RDS(on) GaN devices at the required voltage rating. Parallel devices if needed. At the secondary side, conduction loss dominates, so minimize RDS(on). Zero Qrr eliminates body diode recovery issues on the SR.

5. **Layout**: Use the optimal power loop technique (Section on PCB layout in gan-design-guide.md). The monolithic half-bridge GaN IC achieves ~150 pH loop inductance, enabling clean 1 MHz+ operation.

### LLC Resonant Bus Converter Results (48 V-12 V, from Ch8)

At 1.2 MHz switching frequency, direct comparison with identical topology and PCB:
- GaN: 97.2% peak efficiency, 42 ns ZVS transition, 42% effective duty cycle
- Si MOSFET: 96.2% peak efficiency, 87 ns ZVS transition, 34% effective duty cycle
- GaN reduces power loss by 25% at full load
- For thermally-limited designs (e.g. 14 W max dissipation), GaN increases output power capability by 65 W
- GaN at 1.6 MHz still outperforms Si at 800 kHz (0.9% higher peak efficiency)

### Loss Breakdown at 1.2 MHz (48 V-12 V bus converter)

At light load (2.5 A): Gate drive loss dominates. GaN's 5-10x lower QG provides major advantage.
At full load (20 A): Conduction loss dominates. GaN's shorter ZVS transition gives higher effective duty cycle, reducing RMS currents by ~10% and conduction losses proportionally.
Transformer core loss: Slightly higher with GaN due to longer power delivery period increasing flux density. However, this increase is more than offset by savings in gate drive and conduction losses.

## TI LLC Practical Design (from SLUAA13, SLUAA43, TIDM_LLC)

NOTE: The PDF files SLUAA13, SLUAA43, and TIDM_LLC_DCDC in the Papers directory do not contain LLC converter content (they contain unrelated TI application notes about image sensor PMICs, battery gauge functional safety, and industrial signage I/O respectively, despite their filenames). The CLLLC resonant DAB content from TIDM-02002 (TIDM_DAB.pdf) is documented in the DAB section instead, as it covers a full-bridge LLC variant used in a bidirectional DAB topology.

For practical TI LLC design guidance, refer to the existing content in this file (derived from SLUA560, SLUA694, SLUA880, and TIDA-010054 which are correctly filed) and the CLLLC tank design procedure in `dab.md` which details the LLC-variant resonant tank parameter selection used in TI's 6.6 kW onboard charger reference design.

## Multi-Element Resonant and Matrix Transformer LLC (from Huang VT 2013)

Source: D. Huang, "Investigation of Topology and Integration for Multi-Element Resonant Converters," PhD dissertation, Virginia Tech, 2013.

### Synthesis of Multi-Element Resonant Converters

Huang proposes a systematic synthesis method to discover LLC-like resonant topologies beyond the basic LLC. Starting from the two basic resonant cells (series and parallel), he constructs all possible three-element resonant tanks and identifies which ones share LLC's favorable properties (ZVS on primary, ZCS on secondary, holdup capability).

**Key findings from synthesis:**
- Three useful three-element topologies emerge: **LLC**, **CLL**, and **LCL**.
- CLL and LLC have similar V-I stress characteristics; LCL has higher stress.
- CLL offers better EMI (softer voltage edges across transformer) and allows primary-side current sensing for SR driving.
- LCL has inherently low startup current stress (unlike LLC which has very high inrush at startup frequencies).

### State-Plane Evaluation Method

A unified state-plane analysis with new normalization factors enables direct comparison of resonant converter topologies:

```
Normalization voltage:  V_N = V_in
Normalization current:  I_N = P_o / V_in
Normalized apparent power: P_appr / P_o = I_in_NRMS (dimensionless)
```

The minimum apparent power for any resonant converter at resonant frequency is 1.11x the output power (pi/2*sqrt(2) factor from fundamental approximation). State-plane trajectories visually show voltage/current stress and enable direct topology comparison.

**Design insight (LLC V-I stress):** For LLC, increasing L_n (= L_m / L_r) moves the knee point to lower voltage and current stress. Each L_n value traces a line in V-I space as Q sweeps; the optimal Q for each L_n is at the knee point.

### Startup, Short-Circuit Protection, and SR Driving Comparison

| Feature | LLC | CLL | LCL |
|---------|-----|-----|-----|
| Startup current | Very high | Very high | Low |
| Short-circuit protection | Poor (needs external) | Poor | Good (inherent) |
| SR driving from primary | Not possible (Lm integrated) | Possible | Possible |
| EMI (CM noise) | High at f_sw and harmonics | Lower at high freq | Low harmonics, peak at f_sw only |

**SR driving issue in LLC:** When L_m is integrated into the transformer, the magnetizing current creates a phase shift between primary and secondary currents. Primary-side current cannot be used to derive SR timing. CLL and LCL allow integration of leakage inductance while keeping the ability to sense primary current for SR driving.

### Matrix Transformer for High-Current LLC

For high step-down, high-current LLC (e.g., 400V-to-12V server PSU), transformer loss dominates. The matrix transformer addresses this by splitting the transformer into multiple small elements:

**Architecture (demonstrated at 400V/12V, 1 kW):**
- Transformer turns ratio 16:1:1 (center-tapped), split into 4 elemental transformers (4:1:1 each).
- Primary windings in series, secondary windings in parallel.
- 4-layer PCB (vs. 12-layer for conventional single-core): top/bottom for primary, inner layers for secondary.
- MMF between primary and secondary = 2x primary current (vs. 4x for single-core 12-layer PCB). Lower MMF means lower leakage inductance and lower AC winding resistance.

**Flux cancellation:** Because all elemental transformers see identical volt-seconds, their fluxes can be arranged to cancel in shared core legs:
1. Rearrange winding directions so adjacent cores have opposing flux.
2. Merge adjacent U-I cores into E-I cores.
3. Center legs of E-I cores carry nearly zero net flux and can be removed.
4. Result: 4 U-I cores reduce to 2 U-I cores -- half the core volume and core loss.

### Integrated Secondary Winding with SR Devices

The dominant loss mechanism in conventional high-current PCB transformers is **termination loss** -- the AC current crowding where secondary PCB traces connect to SR devices via external traces, vias, or copper bars.

**Huang's solution:** Mount SR MOSFETs and output capacitors directly on the secondary PCB winding layer, making them part of the secondary winding itself:
- Eliminates termination loss entirely (current path terminates at DC output, not at high-frequency AC connection).
- FEA comparison: secondary AC resistance drops from 6.81 mOhm (with termination) to 2.08 mOhm (integrated SR). Leakage inductance drops from 176 nH to 63 nH.
- The integrated winding has worse interleaving (secondary on outer layers), but the termination loss elimination more than compensates.

**Thermal heat extractor:** With SR devices mounted on the PCB winding inside the transformer, a copper heat extractor conducts heat from the SR dies to the outer surface. This enables effective cooling of devices buried inside the magnetic structure.

### Passive Integration for Multi-Element Resonant Converters

For 4- and 5-element resonant tanks (which add notch filter elements for inherent overcurrent protection), Huang proposes integrating all magnetic components into a single multi-winding transformer using the cantilever model:

**Principle:** Add a 4th winding to the transformer. The leakage inductance between winding 1 (primary) and winding 4 creates the additional resonant inductor L_p. When winding 4 has turns ratio n4 = 1, nodes A and B are virtually shorted, and L_p appears in the correct circuit position.

**Cantilever model parameters:** L_11^2/(L_11 + l_123) = L_m (magnetizing inductance), (L_11 * l_123)/(L_11 + l_123) = L_r (leakage/resonant inductance), l_14 = L_p (additional resonant inductor). All parameters are directly measurable on the prototype.

This integration reduces the discrete passive count from 6 components (L_r, L_m, L_p, C_r, C_p, transformer) to 3 (integrated magnetic module + C_r + C_p), enabling high power density in multi-element resonant converters.
