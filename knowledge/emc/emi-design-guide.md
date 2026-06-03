# EMI Design Guide for Power Converters

> Compiled from: Wang 2005 (EMI Filter Parasitics, VT), Chen 2006 (Integrated EMI Filters, VT),
> Basso 2014 (SMPS SPICE Simulations, McGraw-Hill Ch.1.10)

---

## 1. Noise Sources in SMPS

Every SMPS generates electromagnetic noise due to its switching action. The high di/dt loops and
high dv/dt nodes in the power stage are the primary noise sources. Noise couples to victim circuits
through four mechanisms:
1. **Conductive interference** -- through shared conductors
2. **Radiated interference** -- electromagnetic field propagation
3. **Capacitive interference** -- near-field electric coupling (dv/dt)
4. **Inductive interference** -- near-field magnetic coupling (di/dt)

### 1.1 Differential Mode (DM) Noise Sources

DM noise flows in opposite directions on the two power lines (Line and Neutral) and returns
through the power source. In a PFC boost converter, DM noise always flows through the boost
inductor L_B.

**DM noise equivalent circuit (PFC):**

The MOSFET switching node acts as a trapezoidal voltage source V_N with amplitude (V_o - V_i).
The DM noise voltage measured at the LISN is:

```
V_DM = V_N * 50 / (Z_LB + 100)    [V]

V_DM(dBuV) = 20*log(V_N/1uV) - 20*log(|Z_LB|) + 154    (when |Z_LB| >> 100 Ohm)
```

The DM noise spectrum has three components:
- **Noise source spectrum**: trapezoidal waveform --> -20 dB/dec envelope
- **Boost inductor impedance**: +20 dB/dec in inductive region
- **Net DM spectrum**: -40 dB/dec at low frequencies (150 kHz to first resonance)

Above the inductor's self-resonant frequency (SRF), the inductor becomes capacitive
(-20 dB/dec impedance), so the DM spectrum flattens to 0 dB/dec -- this is why high-frequency
DM noise is often problematic.

**Key design insight (Wang):** The boost inductor's parasitic winding capacitance and
transmission-line behavior shape the DM noise spectrum. Impedance peaks/valleys in the inductor
create corresponding valleys/peaks in the noise spectrum.

**Boost inductor design rules for low DM noise:**
1. Use core material with high HF loss (e.g., iron powder > cool-mu) for high HF impedance and
   damped parasitics
2. Choose moderately high permeability to reduce turn count and thus reduce winding capacitance
3. Consider coating material permittivity and inter-turn spacing as secondary factors
4. An iron-powder core inductor can achieve 10 dB improvement above 2 MHz and up to 30 dB
   improvement at parasitic resonance peaks compared to cool-mu

### 1.2 Common Mode (CM) Noise Sources

CM noise flows in the same direction on both power lines and returns through the ground
(earth/chassis). The primary CM noise source is the dv/dt across parasitic capacitance between
switching nodes and ground (e.g., MOSFET drain to heatsink capacitance C_C).

**CM current path in a PFC converter:**

```
2*i_CM flows through C_C --> through one or both power lines --> through LISN 50 Ohm
terminators --> back to ground
```

In an unbalanced structure (typical PFC with diode bridge), the CM current 2*i_CM flows
through only one power line at a time, depending on which diode pair is conducting. This creates
**mixed-mode (MM) noise** -- the measured DM noise contains a CM component:

```
V_DM_measured = 50 * (i_DM + i_CM)     [not purely DM]
V_CM_measured = 50 * i_CM
```

**Balancing the CM path:** Adding a balance capacitor C_BL after the diode bridge provides a
return path for CM current through both lines equally, eliminating mixed-mode noise:

```
With C_BL:  V1 = 50*(-i_DM + i_CM),  V2 = 50*(i_DM + i_CM)
            V_DM = 50*i_DM,  V_CM = 50*i_CM   [pure separation]
```

### 1.3 Switching Node dv/dt and di/dt

| Parameter | Typical Range | Primary Effect |
|-----------|--------------|----------------|
| dv/dt at drain | 1-50 V/ns | CM noise via parasitic C to ground |
| di/dt in power loop | 0.1-5 A/ns | DM noise, radiated emission from loop |
| Reverse recovery di/dt | 0.5-10 A/ns | Both CM and DM noise spikes |
| Ringing frequency | 10-200 MHz | Radiated emission |

---

## 2. CM/DM Noise Separation

### 2.1 Measurement with LISN

**LISN (Line Impedance Stabilization Network) circuit:**
- 50 uH inductor in series (passes 50/60 Hz power, blocks HF noise)
- 0.1 uF capacitor to 50 Ohm termination (passes HF noise to analyzer)
- 1 uF line-to-line capacitor
- 1 kOhm discharge resistor

**Standard measurement setup (per FCC/EN55022):**
- Two LISNs connected to L and N power lines
- Spectrum analyzer connected to one LISN (50 Ohm input)
- Other LISN terminated with 50 Ohm
- Equipment Under Test (EUT) on ground plane
- Distance: EUT 40 cm above ground, >2 m from walls, >0.8 m from LISN
- Resolution bandwidth: 9 kHz for 150 kHz-30 MHz

**Noise voltage definitions:**
```
V1 = 50 * (-i_DM + i_CM)    [Line side]
V2 = 50 * (i_DM + i_CM)     [Neutral side]

V_DM = (V1 - V2) / 2
V_CM = (V1 + V2) / 2
```

### 2.2 Noise Separator Circuit Design

A proper noise separator is a 3-port network (2 inputs from LISNs, 1 output to spectrum
analyzer). Wang identifies three requirements for a valid noise separator:

**Requirement 1: Input impedances are real 50 Ohm, independent of noise source impedance.**

Many published separators fail this requirement. For example, the transformer-based separator
from Guo (1996) has input impedances that depend on the source:
```
Z_in1 = 82 + (50 // 82 // Z_S2)    [source-dependent!]
```

**Requirement 2: Correct transmission ratio (DMTR = 0 dB, CMTR = 0 dB).**

The output must be |V1-V2|/2 for DM or |V1+V2|/2 for CM.

**Requirement 3: Small leakage (CMRR and DMRR as low as possible).**

**S-parameter characterization (Wang's method):**

The ideal S-matrix for a DM noise separator:
```
[S] = | 0    0    S13 |
      | 0    0    S23 |
      | 1/2 -1/2  S33 |
```

For a CM noise separator:
```
[S] = | 0    0    S13 |
      | 0    0    S23 |
      | 1/2  1/2  S33 |
```

Key parameters from measured S-parameters:
```
Input impedance:     Z_in1 ~ Z0 * (1+S11)/(1-S11)   [if S12*S21 << S11]
                     Z_in2 ~ Z0 * (1+S22)/(1-S22)

DM transmission:     DMTR ~ (S31 - S32) / (1 + S11 + S22)
CM rejection:        CMRR ~ (S31 + S32) / (1 + S11 + S22)
```

**High-performance separator design guidelines (Wang):**
- Avoid conventional wound transformers -- parasitic coupling (leakage inductance, interwinding
  capacitance) degrades CMRR/DMRR above a few MHz
- Use transmission-line transformers for better HF balance
- S11, S22 should be < -20 dB over 150 kHz - 30 MHz
- S12, S21 should be < -30 dB (minimizes source-dependent input impedance)
- Calibrate to the measurement plane using network analyzer SOLT calibration

---

## 3. EMI Filter Design

### 3.1 Impedance Mismatch Rule

The fundamental principle for EMI filter topology selection (Wang):

> The input impedance of the EMI filter should be much higher (or much lower) than the
> output impedance of the noise source. The output impedance of the EMI filter should be
> much higher (or much lower) than the input impedance of the LISN.

**DM filter topology selection:**
- Noise source output impedance: LOW (if balance capacitor exists) --> use series inductor at
  filter input (high Z_in)
- LISN DM input impedance: 100 Ohm --> use shunt capacitor at filter output (low Z_out)
- Topology: L-C or pi-section starting with inductor

**CM filter topology selection:**
- CM noise source impedance: HIGH (small parasitic C_C) --> use shunt Y-cap at filter input
  (low Z_in)
- LISN CM input impedance: 25 Ohm --> use series CM choke at filter output (high Z_out)
- Topology: C-L or T-section starting with capacitor

### 3.2 DM Filter (X-caps, DM inductors)

**DM filter equivalent circuit:**
```
                2*L_DM
    DM Noise ---[===]--+---[===]--- 100 Ohm (2 x 50 Ohm LISN)
                       |
                     [C_X]
                       |
                      GND
```

The leakage inductance of the CM choke typically serves as the DM inductor. For a
single-stage LC filter, the required attenuation at switching frequency f_sw is:

```
Attenuation(dB) = Noise_level(dBuV) - Limit(dBuV) + Margin(dB)
```

**DM filter corner frequency:**
```
f_0 = 1 / (2*pi*sqrt(L_DM * C_X))

Required: f_0 < f_sw / sqrt(10^(A_dB/20))    where A_dB = required attenuation
```

For a second-order LC filter, attenuation above f_0 is -40 dB/dec. For two cascaded
stages, -80 dB/dec.

**DM filter component equations:**
```
Single-stage attenuation at frequency f:
A(f) = (f/f_0)^2    for f >> f_0    [ratio, not dB]
A_dB = 40*log10(f/f_0)

Two-stage (identical):
A_dB = 80*log10(f/f_0)
```

### 3.3 CM Filter (Y-caps, CM choke)

**CM filter equivalent circuit (per line):**
```
                L_CM + L_DM/2
    CM Noise ---[=========]--- 25 Ohm (50 Ohm / 2)
                      |
                   [2*C_Y]
                      |
                     GND
```

Two CM capacitors (C_Y) are effectively in parallel for CM noise (each sees the same CM
voltage). Two DM inductors are in parallel for CM noise (L_DM/2 in series with L_CM).

The CM choke has two closely coupled windings on a high-permeability core:
- **CM inductance**: full magnetizing inductance (both windings in series-aiding)
- **DM inductance**: leakage inductance only (fluxes cancel for DM current)

### 3.4 Multi-Stage Filter Design

When single-stage attenuation is insufficient, cascaded stages are used. A typical
two-stage EMI filter combines CM and DM filtering:

```
L --[C_Y]--[CM_choke]--[C_X]--[L_DM]--[C_X]--[C_Y]-- to LISN
N --[C_Y]--[CM_choke]--[C_X]--[L_DM]--[C_X]--[C_Y]-- to LISN
              GND                               GND
```

For a two-stage filter with identical LC sections (Gamma+Pi topology):

**Critical: inductive coupling between the two inductors (Wang).**
The mutual inductance M between two inductors in a two-stage filter can be much larger than
the ESL of the inter-stage capacitor. In Wang's measurement: M = 1.79 uH vs ESL = 14 nH.
This coupling shifts the capacitor branch resonance from ~2 MHz down to ~174 kHz,
destroying filter performance.

**Countermeasure:** Orient inductors at 90 degrees to each other, or use magnetic shielding
between stages.

### 3.5 Parasitic Effects on Filter Performance

This is the central theme of Wang's dissertation. Parasitics that degrade HF filter performance:

#### 3.5.1 Inductive Coupling Between Inductor and Capacitor Branches (M1, M2)

The mutual inductance M between the DM inductor and a capacitor branch adds to or subtracts
from the capacitor's ESL, shifting its series resonant frequency:

```
Positive coupling:  f_res = 1 / (2*pi*sqrt((ESL + M) * C))    [lower --> worse LF]
Negative coupling:  f_res = 1 / (2*pi*sqrt((ESL - M) * C))    [higher if ESL > M]

Above resonance, impedance of capacitor branch:
  Positive: Z ~ omega*(ESL + M)
  Negative: Z ~ omega*(ESL - M)    [better if M ~ ESL --> Z ~ 0]
```

**Winding direction matters:** Reversing the inductor winding direction flips the coupling
polarity. The proposed 90-degree rotation of windings symmetrically reduces coupling to near zero.

#### 3.5.2 Inductive Coupling Between Capacitor Branches (M3)

Even tiny mutual inductance between input and output capacitor branches is critical because of
the large current ratio I2/I1 between them (40 dB/dec above corner frequency):

```
Voltage on ESL1:  U1 = j*omega*(ESL1*I1 + M3*I2)

When |I2/I1| > ESL1/M3, the mutual inductance dominates the capacitor branch impedance.
```

Wang measured M3 = 0.45 nH (coupling coefficient k = 3%) between two capacitor branches.
A 30 dB current difference amplifies this tiny coupling significantly.

**Countermeasure:** Shield capacitors with 3 mil nickel foil (6 dB improvement), plus magnetic
shield plate between capacitors (additional 10 dB, total 16 dB improvement).

#### 3.5.3 Capacitive Coupling Between Input and Output Traces (Cp)

Parasitic capacitance Cp between input and output traces bypasses the inductor at HF:
```
Cp = C_direct + C_through_ground_plane

C_through_ground_plane can be larger than C_direct due to shorter distance to ground plane.
```

Wang's measurement: Cp = 5.8 pF total (2 pF direct + 3.8 pF through ground plane).

The ground plane also reduces DM inductance via eddy current cancellation (M7 = 0.81 uH
reduction measured).

#### 3.5.4 Coupling Between Inductor and Trace Loops (M4, M5)

These couplings add equivalent inductance to the capacitor branches through the trace loop
current path, similar in effect to M1 and M2.

#### 3.5.5 Coupling Between Input and Output Trace Loops (M6)

Typically small (~0.2 nH) but relevant at very high frequencies.

### 3.6 Parasitic Cancellation Techniques (Wang)

#### Mutual Coupling Cancellation

**Approach 1:** Add a cancellation turn around the capacitor, wound to generate a flux opposing
the parasitic mutual inductance. The cancellation turn is integrated into the film capacitor
structure.

**Approach 2:** Integrated tubular film capacitor with built-in cancellation winding. The
equivalent mutual inductance between inductors and capacitor branches is reduced to near zero.

**Results:** 10-20 dB improvement in filter insertion loss at frequencies above 2 MHz.

#### ESL Cancellation

By connecting two capacitors with opposite ESL orientation (anti-series for ESL, parallel for
capacitance), the net ESL approaches zero:

```
Two capacitors in ESL-canceling arrangement:
  C_total = C1 + C2    (parallel for capacitance)
  ESL_total ~ 0         (ESL currents cancel)
  ESR_total = ESR1*ESR2/(ESR1+ESR2)    (parallel)
```

Implementation: PCB trace routing creates the anti-series ESL connection by routing current
through the two capacitors in opposite magnetic orientation.

### 3.7 Damping the Filter

An undamped EMI filter combined with the converter's negative input impedance can oscillate
(Basso, Middlebrook criterion).

**Negative input impedance of closed-loop converter:**
```
R_in = -V_in^2 / P_out    [negative incremental resistance]
```

**Middlebrook stability criterion:**
```
|Z_out_filter(f)| << |Z_in_converter(f)|    for all frequencies
```

**Parallel damping network (Basso):**

Add R_damp in series with C_damp across the filter output:

```
C_damp >= 4 * C_filter    (to 10x, blocks DC)

For Q ~ 1 (critically damped):
R_damp_parallel = sqrt(L / C) * (1 + R1*sqrt(C/L))    [~ Z0 = sqrt(L/C) for small R1]

Actual R_damp (accounting for converter loading):
1/R_damp = 1/R3_required - 1/Z_in_SMPS_dc

Where R3_required is from Q=1 condition.
```

**Numerical example (Basso):**
```
L = 100 uH, C = 1 uF, R1 = 100 mOhm, P = 60 W, V_in = 100 V
Z0 = sqrt(100e-6 / 1e-6) = 10 Ohm
f0 = 1/(2*pi*sqrt(LC)) = 15.9 kHz
Z_in_SMPS = V^2/P = 166.7 Ohm
R_damp ~ 10 Ohm    (from Q=1 calculation)
C_damp >= 4 uF     (minimum), 10 uF recommended
```

**Attenuation calculation (Basso):**

For the first harmonic approximation (FHA), neglecting R1 and R2:
```
A_filter(f) ~ 1 / (1 - (f/f0)^2)

Required f0 for given attenuation:
f0 = f_sw / sqrt(A_required + 1)    ~ f_sw / sqrt(A) for large A

where A = I_out_peak / I_in_max_allowed
```

---

## 4. Component Selection for EMI Filters

### 4.1 X Capacitors (Across-the-Line)

X capacitors are connected line-to-line (L-N) for DM filtering.

| Parameter | Typical Values | Notes |
|-----------|---------------|-------|
| Capacitance | 0.1 uF - 4.7 uF | Limited by inrush, power factor |
| Voltage rating | 275 VAC (X2), 440 VAC (X1) | X2 for residential |
| ESL | 5-20 nH (film), 1-5 nH (MLCC) | Determines SRF |
| ESR | 5-50 mOhm (film) | Affects damping |
| Self-resonant freq | 0.5-5 MHz (film), 5-50 MHz (MLCC) | Above SRF, cap is inductive |
| Safety class | X1: 4 kV surge, X2: 2.5 kV surge | Per IEC 60384-14 |

**Safety requirement:** X caps must be self-healing film type or safety-rated ceramic.
Must include discharge resistor: V < 60V within 1 second after disconnection.

```
R_discharge <= 1 / (C_X * ln(V_peak/60))    approximately
R_discharge ~ 1 MOhm per uF for 230 VAC
```

**ESL impact on filter performance:**

Above the self-resonant frequency f_SRF = 1/(2*pi*sqrt(ESL*C)), the capacitor becomes
inductive and no longer provides filtering. Additional mutual inductances from PCB layout
further degrade performance (see Section 3.5).

### 4.2 Y Capacitors (Line-to-Ground)

Y capacitors are connected from each line to ground (earth) for CM filtering.

| Parameter | Class Y1 | Class Y2 |
|-----------|----------|----------|
| Rated voltage | 500 VAC | 300 VAC |
| Peak test voltage | 8 kV | 5 kV |
| Typical capacitance | 1-10 nF | 1-100 nF |
| Leakage current limit | 0.5 mA (medical: 0.1 mA) | 0.5 mA |

**Leakage current constraint (the dominant limit on C_Y):**
```
I_leakage = 2*pi*f_line * C_Y * V_line

For 230V/50Hz, I_leakage < 0.5 mA:
  C_Y < 0.5e-3 / (2*pi*50*230) = 6.9 nF    per capacitor
  Typical: 2.2 nF or 4.7 nF

For medical (0.1 mA limit):
  C_Y < 1.4 nF    per capacitor
```

**Unbalanced Y-caps create mode conversion (Wang):**
If C_Y1 != C_Y2, CM noise converts to DM noise and vice versa. The mode-transformation
voltage:
```
V_conversion ~ V_CM * (C_Y1 - C_Y2) / (C_Y1 + C_Y2)
```
Use matched Y-cap pairs (1% tolerance ceramic) for minimal mode conversion.

### 4.3 Common Mode Chokes

The CM choke is typically the largest and most critical EMI filter component.

**Key parameters:**
| Parameter | Typical Range | Design Impact |
|-----------|--------------|---------------|
| CM inductance | 1-50 mH | Determines CM filter corner freq |
| Leakage (DM) inductance | 0.5-5% of L_CM | Free DM filtering |
| Impedance at 100 kHz | 100-10k Ohm | CM attenuation in CISPR band |
| SRF | 0.5-5 MHz | Above SRF, impedance drops |
| Rated current | 0.1-30 A | Must handle full load current |
| DC resistance | 10 mOhm - 1 Ohm | Conduction loss |

**Core material selection:**
- **MnZn ferrite** (mu_i = 5000-15000): Best for 150 kHz - 5 MHz, low SRF
- **Nanocrystalline** (mu_i = 20000-100000): Broadband, high impedance, expensive
- **NiZn ferrite** (mu_i = 100-1500): Good for >1 MHz, higher SRF

**CM choke impedance vs frequency:**

The useful impedance range is limited by:
- Low end: inductive rise (+20 dB/dec) starting from Z = omega*L_CM
- Peak: at SRF, impedance = Q * omega_SRF * L_CM
- High end: capacitive fall (-20 dB/dec) due to interwinding capacitance

**Equivalent parallel capacitance (EPC) reduction (Chen):**

EPC of the CM choke limits HF performance. Chen's structural winding capacitance
cancellation technique:

1. Embed a thin conductive shield layer between the inductor winding halves
2. The shield creates an equal and opposite displacement current that cancels the
   capacitive coupling between winding layers
3. Optimal shield area cancels EPC almost completely
4. Verified: improved integrated EMI filter with >20 dB better HF attenuation

**Design equations for integrated CM choke (Chen):**
```
L_CM = mu_0 * mu_eff * n^2 * A_e / l_e

where:
  mu_eff = mu_r * l_e / (l_e + mu_r * l_g - l_g)    [effective permeability with air gap]
  n = number of turns per winding
  A_e = effective core cross-section area
  l_e = effective magnetic path length
  l_g = air gap length (unavoidable in planar cores: 3-6 um typical)
```

**Leakage inductance enhancement (Chen):**

For planar CM chokes, intrinsic leakage inductance is often insufficient. Insert a magnetic
leakage layer (mu_s = 10-20) between windings:

```
L_leakage = mu_0 * mu_s * N^2 * l_w / (2 * b_w) * (h_delta + h1 + h2/3)

where:
  l_w = mean length per turn
  b_w = winding width
  h_delta = leakage layer thickness
  h1, h2 = winding thicknesses
  N = turns per winding
  mu_s = leakage layer permeability
```

### 4.4 DM Inductors

DM inductors handle the full differential load current and must not saturate.

| Parameter | Typical Range | Notes |
|-----------|--------------|-------|
| Inductance | 1-100 uH | Often from CM choke leakage |
| Rated current | Full load current | Check saturation |
| Core material | Iron powder, sendust | Low-mu, high saturation |
| SRF | 2-30 MHz | Higher is better |

**Standalone DM inductor vs CM choke leakage:**
- CM choke leakage: free, but limited value and hard to control
- Standalone: precise, but adds cost and volume
- Hybrid: add leakage layer to CM choke (Chen's approach)

---

## 5. PCB Layout for EMC

### 5.1 Minimizing Switching Loop Area

The switching loop is the primary source of both conducted DM noise and radiated emissions.

**Rules:**
1. Place the MOSFET(s), diode/SR, and input capacitor in the tightest possible loop
2. Use adjacent copper layers for send and return currents (field cancellation)
3. Minimize via count in the power loop
4. For half-bridge: the bootstrap capacitor loop is equally critical
5. Area of the loop directly determines the loop inductance and radiated emissions:
   ```
   L_loop ~ mu_0 * Area / perimeter    [approximate, for rectangular loop]
   Radiated E-field proportional to f^2 * I * Area
   ```

### 5.2 Ground Plane Strategy

**Ground plane effects on EMI filters (Wang):**

A ground plane under the EMI filter has both beneficial and detrimental effects:

**Detrimental:**
- Creates parasitic capacitance C_p between input and output traces through the ground plane
  (measured: 3.8 pF through ground vs 2 pF direct coupling)
- This capacitance bypasses the filter inductor at HF
- Reduces DM inductance via eddy current cancellation in the ground plane (measured: 0.81 uH
  reduction)

**Beneficial:**
- Provides shielding between layers
- Reduces radiated emissions from power traces
- Provides return path for HF currents

**Recommendations:**
1. Do NOT run a continuous ground plane directly under the EMI filter inductor
2. Use slotted or partial ground planes that break the parasitic coupling path
3. Keep input and output filter traces on opposite sides of the ground plane slot
4. Ground plane is beneficial under the power stage switching loop (reduces radiated EMI)

### 5.3 Component Placement for EMI

**Filter component placement rules (from Wang's parasitic analysis):**

1. **Separate input and output capacitors** -- even 3% coupling coefficient between capacitor
   branches degrades filter by >10 dB at high frequencies
2. **Orient inductors at 90 degrees** when multiple stages are used -- reduces mutual inductance
   from 1.79 uH (aligned) to near-zero
3. **Keep capacitor leads short** -- ESL of 14 nH is already significant; PCB trace adds more
4. **Place magnetic shield between closely spaced inductors** -- 3 mil nickel foil effective
5. **Minimize the area enclosed by capacitor branch traces** -- this area determines the
   susceptibility to inductive coupling from the inductor
6. **Mount filter inductor on opposite PCB side** from capacitors when possible -- distance and
   ground plane shield reduce coupling

### 5.4 Shield and Guard Traces

**Shielding strategies:**
- **Nickel foil (3 mil)** around film capacitors: 6 dB improvement in filter insertion loss
  (1-30 MHz)
- **Magnetic plate between capacitors**: additional 10 dB improvement
- **Guard traces**: grounded traces between sensitive signal lines and noise sources
- **Faraday shield in transformers**: reduces CM noise coupling from primary to secondary

---

## 6. Standards and Compliance

### 6.1 CISPR 32 / EN 55032 Limits

Conducted emission limits apply from 150 kHz to 30 MHz:

| Frequency Range | Class A (Industrial) QP | Class A Avg | Class B (Residential) QP | Class B Avg |
|-----------------|------------------------|-------------|--------------------------|-------------|
| 150 kHz - 500 kHz | 79 dBuV -> 73 dBuV | 66 dBuV -> 60 dBuV | 66 dBuV -> 56 dBuV | 56 dBuV -> 46 dBuV |
| 500 kHz - 5 MHz | 73 dBuV | 60 dBuV | 56 dBuV | 46 dBuV |
| 5 MHz - 30 MHz | 73 dBuV | 60 dBuV | 60 dBuV | 50 dBuV |

QP = Quasi-Peak detector; Avg = Average detector. Both must be satisfied simultaneously.

**Older equivalent standards:**
- FCC Part 15 (USA) -- similar to CISPR but with some differences in limits and methods
- VDE 0871 (Germany) -- largely superseded by EN 55032
- EN 55022 -- predecessor to EN 55032 (for ITE equipment)

**Design margin:** Target 6 dB below the limit for production margin (component tolerance,
temperature, aging).

### 6.2 Conducted Emission Measurement Setup

Per CISPR 16-2-1:
```
                    LISN
AC Mains ---[50uH]---+---[0.1uF]---[50 Ohm]--- Spectrum Analyzer
                      |
                    [1uF]
                      |
                     GND (Ground Plane)
```

- Ground plane: >= 2m x 2m, bonded to building ground
- EUT placed 40 cm above ground plane (non-conductive table)
- LISN bonded to ground plane
- Non-terminated LISN port: 50 Ohm terminator
- Cable length from LISN to analyzer: calibrate out insertion loss
- Warm-up EUT for specified time before measurement

### 6.3 Pre-Compliance Testing

**Low-cost pre-compliance approach:**
1. Use a proper LISN (even a simple one) -- do not measure directly on the AC line
2. Spectrum analyzer or EMI receiver with quasi-peak detector
3. RF current probes on power cord for quick relative measurements
4. Near-field probes (H-field and E-field) for identifying noise sources on PCB
5. Compare against limits with 6-10 dB margin to account for measurement uncertainty

**Key differences from compliance lab:**
- Ambient noise floor (use shielded room or time-domain gating)
- LISN quality (insertion loss flatness, impedance accuracy)
- Grounding quality (connection impedance at HF)

---

## 7. Troubleshooting EMI Failures

### 7.1 Common Conducted Emission Fixes

**Step 1: Identify the dominant noise mode**

Use a noise separator (see Section 2.2) or the two-measurement method:
```
Total noise on Line:     V_L = V_CM + V_DM
Total noise on Neutral:  V_N = V_CM - V_DM

If V_L ~ V_N (within 3 dB): CM dominant
If V_L and V_N differ by >6 dB: DM dominant or mixed
```

**Step 2: Fix DM noise (if dominant)**

| Frequency Range | Likely Cause | Fix |
|-----------------|-------------|-----|
| 150 kHz - 1 MHz | Insufficient DM inductance/capacitance | Increase L_DM or C_X |
| 1-5 MHz | Capacitor ESL resonance | Use lower ESL caps, parallel smaller caps |
| 5-10 MHz | Inductor parasitic capacitance | Use higher-SRF inductor, iron powder core |
| 10-30 MHz | PCB layout coupling | Rearrange filter, add shielding, reduce loop area |

**Step 3: Fix CM noise (if dominant)**

| Frequency Range | Likely Cause | Fix |
|-----------------|-------------|-----|
| 150 kHz - 1 MHz | Insufficient CM choke inductance | Larger CM choke, higher-mu core |
| 1-5 MHz | CM choke interwinding capacitance | Better winding technique, nanocrystalline core |
| 5-10 MHz | Y-cap ESL | Use shorter lead Y-caps, SMD ceramics |
| 10-30 MHz | Parasitic coupling bypassing filter | Improve layout, add second stage |

**Additional DM fixes:**
- Add RC snubber across switching node to reduce dv/dt ringing
- Increase gate resistance to slow down switching (cost: switching loss)
- Add ferrite bead in series with gate for HF damping
- Verify boost inductor is not resonating in the problem frequency range (Wang: redesign with
  iron powder core for 10-30 dB improvement)

**Additional CM fixes:**
- Reduce parasitic capacitance: use insulating thermal pad (lower C_C), increase creepage
- Add balance capacitor C_BL after diode bridge to equalize CM currents
- Use Y-caps on both sides of CM choke (input and output)
- Shield heatsink from switching node (Faraday screen)

### 7.2 Common Radiated Emission Fixes

| Issue | Fix |
|-------|-----|
| Broadband emission | Reduce switching loop area, use adjacent-layer return path |
| Emission at f_sw harmonics | Improve input filter, snub switching node ringing |
| Emission at >100 MHz | Add ferrite beads on power lines, reduce trace antenna lengths |
| Cable emission | Add CM choke on cables, improve cable shielding |
| Heatsink radiation | Bond heatsink to ground at HF, add bypass cap from drain to heatsink |

---

## 8. Integrated EMI Filters (Chen)

### 8.1 Motivation

Conventional discrete EMI filters occupy 15-20% of SMPS system volume. Issues:
1. Many components with different shapes, sizes, and form factors
2. Labor-intensive assembly with different processing technologies
3. Parasitic parameters limit effective frequency range to below a few MHz
4. Layout parasitics further degrade HF performance

### 8.2 Planar Electromagnetic Integration Concept

The integrated EMI filter combines L and C functions in a single planar structure:

**Basic structure:** Two spiral windings separated by a high-permittivity dielectric layer,
wound on a ferrite core. The structure has distributed inductance (from windings and core)
and distributed capacitance (from dielectric between conductors).

**Design equations for integrated LC (Chen):**

CM filter capacitance:
```
C_CM = epsilon_0 * epsilon_r * m * l_mean * w / d

where:
  epsilon_r = relative permittivity of dielectric (Y5V ceramic: ~14000)
  m = number of integrated LC turns
  l_mean = mean length per turn
  w = conductor width
  d = dielectric thickness
```

DM capacitance (integrated):
```
C_DM = epsilon_0 * epsilon_r2 * w2 * l_mean / d2
```

### 8.3 Technologies for HF Improvement

**Reducing ESL:** Use interleaved winding, multi-layer conductors, or transmission-line
geometry to minimize series inductance of the capacitive element.

**Reducing EPC:** Use sectioned windings, electrostatic shields, or Chen's structural winding
capacitance cancellation with embedded conductive shield layer.

**Increasing HF losses (beneficial for filters):**
- Electroplated nickel coating on conductors (high resistivity at HF due to skin effect)
- Use lossy ferrite materials that provide resistive impedance above a few MHz
- Lossy dielectric materials increase damping

### 8.4 Structural Winding Capacitance Cancellation

Chen's key contribution: embed a thin conductive shield between inductor winding halves:

1. The shield intercepts the electric field between winding layers
2. By proper sizing, the displacement current through the shield exactly cancels the
   parasitic winding capacitance
3. Result: inductor maintains high impedance to much higher frequencies
4. Sensitivity study shows the technique is robust to manufacturing tolerances
5. Applicable to both CM chokes and boost inductors

### 8.5 Integrated RF EMI Filter

For frequencies above 10-30 MHz, the integrated RF EMI filter uses nickel-coated conductors
on ferrite substrates to create a lossy transmission-line structure:

- Provides broadband attenuation from the switching frequency up to 1 GHz
- Modeled using multi-conductor lossy transmission-line theory
- Can combine CM and DM filtering in a single structure
- Parametric dependencies: nickel permeability, alumina layer thickness, total structure length

---

## Appendix A: Quick Reference Equations

### Filter Corner Frequency
```
f_0 = 1 / (2*pi*sqrt(L*C))
```

### Single-Stage LC Attenuation (f >> f_0)
```
A_dB = 40 * log10(f / f_0)
```

### Required Inductor Impedance for DM Noise
```
|Z_L| > 50 * V_N(f) / V_limit(f)    [at each frequency of concern]
```

### CM Choke Inductance from Required Attenuation
```
L_CM > 1 / ((2*pi*f)^2 * C_Y) * 10^(A_required_dB / 20)
```

### Input Filter Stability (Middlebrook)
```
|Z_out_filter|_peak = Z_0^2 / R1 = L / (R1*C)    [where R1 = inductor DCR]

Damping resistor: R_damp ~ sqrt(L/C)
Damping capacitor: C_damp >= 4*C_filter
```

### Parasitic Capacitor Branch Resonance with Mutual Coupling
```
f_res = 1 / (2*pi*sqrt((ESL +/- M)*C))
```

### Y-Cap Leakage Current
```
I_leak = 2*pi * f_line * C_Y * V_line    [must be < 0.5 mA, or 0.1 mA medical]
```

### X-Cap Discharge Time Constant
```
tau = R_discharge * C_X    [must achieve V < 60V in 1 second]
```

---

## EMI/EMC Debugging Techniques (from APEC 2026 S.17 -- Arturo Mediano)

> Compiled from: Mediano 2026 (EMI/EMC Debugging with Oscilloscopes, APEC S.17)

### 9.1 EMI/EMC Framework: Culprit -- Path -- Victim

Every EMI problem has three elements:
1. **Culprit (source)** -- the circuit generating the interference (switching node, clock, etc.)
2. **Path (coupling mechanism)** -- conducted, radiated, capacitive, or inductive coupling
3. **Victim** -- the circuit or system being disturbed

Debugging means identifying which element to address. Fixing the culprit is always preferred
over filtering the path or hardening the victim.

### 9.2 Near-Field vs Far-Field

| Region | Distance | Dominant Field | Characteristic |
|--------|----------|---------------|----------------|
| Near-field | d << lambda/(2*pi) | Depends on source geometry | E and H fields are independent |
| Far-field | d >> lambda/(2*pi) | E and H coupled (plane wave) | E/H = 377 Ohm (free space) |

- At typical SMPS frequencies (100 kHz - 30 MHz), wavelengths are 10 m to 10 km, so
  PCB-level debugging is always in the near field
- Near-field probes are the primary tool for PCB-level EMI investigation
- Near-field probes can distinguish E-field sources (high dv/dt nodes) from H-field
  sources (high di/dt loops)

### 9.3 Narrowband vs Broadband Emissions

**Narrowband emissions:** discrete spectral lines at specific frequencies (clock harmonics,
switching frequency harmonics). Appear as sharp peaks on spectrum analyzer.

**Broadband emissions:** continuous spectrum spread over wide frequency range (fast switching
edges, arcing, ringing). Appear as raised noise floor or broadband humps.

**Key debugging insight:** If emissions correlate with switching frequency harmonics, the
culprit is the power stage switching. If emissions appear at frequencies unrelated to switching
(e.g., resonant ringing frequencies), look for parasitic resonances.

### 9.4 EMI Debugging Process

**Typical (slow) process:**
1. Measure emissions at compliance lab
2. Identify failure frequencies
3. Apply trial-and-error fixes
4. Re-measure at lab
5. Repeat

**More powerful (Mediano) method:**
1. Use an oscilloscope with FFT as a pre-compliance tool on the bench
2. Capture time-domain waveforms at suspected noise sources
3. Use FFT to correlate time-domain events with spectral peaks
4. Identify root cause before applying fixes
5. Verify fix effectiveness immediately on the bench

The key advantage: the oscilloscope shows **time-correlated** information -- you can see
which switching event or timing interval causes which spectral peak. A spectrum analyzer
alone cannot provide this correlation.

### 9.5 Probes for EMI Debugging

#### 9.5.1 Voltage Probes

Standard oscilloscope voltage probes can measure:
- Switching node waveforms (dv/dt, ringing frequency, overshoot)
- Gate drive signals
- Power supply ripple
- LISN output voltage

**Important:** Use proper grounding technique. The standard probe ground lead acts as an
antenna at high frequencies. Use a spring-tip ground contact or a probe tip adapter with
minimal ground loop area for measurements above a few MHz.

#### 9.5.2 Current Probes

Current probes (clamp-on) measure:
- Conducted emissions current on power cables
- High-frequency CM current on cable bundles
- Switch current waveforms (di/dt)
- Inductor current waveforms

**Types:**
- **AC current probes (transformer-based):** Bandwidth 1 kHz - 100+ MHz. Measure HF
  current without breaking the circuit. Ideal for EMI debugging.
- **DC/AC current probes (Hall effect):** Measure DC + AC components. Lower bandwidth
  (typically up to 50 MHz). Use for power stage current measurements.

**Practical tip:** Clamping a current probe around both conductors of a power cable
(L + N together) measures only the CM current. Clamping around a single conductor
measures DM + CM current combined.

#### 9.5.3 Near-Field Probes (NFP)

Near-field probes are the most powerful tool for EMI source identification on PCBs.

**H-field probes (magnetic):**
- Small shielded loop antennas
- Sensitive to di/dt (current loops)
- Used to scan PCB traces, component leads, and power loops
- The probe output is proportional to dB/dt (rate of change of magnetic flux)
- Measure current flow direction and magnitude by orienting the loop

**E-field probes (electric):**
- Small monopole or dipole antennas
- Sensitive to dv/dt (voltage nodes)
- Used to identify high-voltage switching nodes, floating conductors, and shield gaps
- The probe output is proportional to dE/dt

**Scanning technique:**
1. Move the NFP slowly across the PCB surface
2. Monitor the oscilloscope in both time domain and FFT mode simultaneously
3. The location where signal amplitude peaks identifies the noise source
4. Rotate H-field probes to determine current flow direction
5. Use E-field probes near suspected high-dv/dt nodes (switching nodes, heatsinks)

**Practical applications of NFPs:**
- Measuring voltage waveforms on traces without direct contact
- Measuring current in traces without breaking the circuit
- Identifying leakage paths in shielded enclosures (scan seams and apertures)
- Verifying effectiveness of shielding and filtering changes

#### 9.5.4 LISN as a Probe

A Line Impedance Stabilization Network (LISN) connected between the AC mains and the EUT
provides a standardized 50-Ohm measurement point for conducted emissions. When connected
to an oscilloscope:
- Time-domain waveform shows the conducted noise superimposed on 50/60 Hz
- FFT provides the conducted emission spectrum
- Can be compared directly against CISPR limits (with appropriate corrections for
  detector type: peak vs quasi-peak vs average)

#### 9.5.5 Antennas

Small broadband antennas (e.g., biconical, log-periodic) connected to the oscilloscope can
capture radiated emissions in the time domain:
- Useful for quick radiated emission pre-screening
- Correlate time-domain antenna signal with switching events
- Identify which switching transitions produce the most radiation

### 9.6 Oscilloscope Setup for EMI Debugging

#### 9.6.1 FFT Capabilities

Modern oscilloscopes provide real-time FFT that is adequate for pre-compliance EMI debugging:
- Use a long time record to achieve adequate frequency resolution
- Set frequency span to cover the band of interest (150 kHz - 30 MHz for conducted,
  30 MHz - 1 GHz for radiated)
- Use peak-hold mode to capture worst-case emissions over multiple switching cycles

**Key FFT parameters:**
```
Frequency resolution = 1 / T_record
Number of FFT points = Sample_rate * T_record
Maximum frequency = Sample_rate / 2   (Nyquist)
```

For conducted emissions (150 kHz - 30 MHz with 9 kHz resolution BW):
- Need at least 111 us time record (1/9 kHz)
- Sample rate >= 60 MSa/s (2x 30 MHz Nyquist)

#### 9.6.2 FFT Gating

FFT gating is a critical feature for EMI debugging:
- Allows computing the FFT only during a selected time window within the switching cycle
- Enables isolating which part of the switching waveform causes which spectral component
- Example: gate the FFT to the turn-on transition only -- this reveals the spectral
  content of the turn-on ringing. Gate to the dead time -- this reveals parasitic
  oscillation frequencies.

**Practical application:** If the FFT shows an unexpected peak in the FM band (~100 MHz),
gate the FFT to different parts of the switching cycle. If the peak appears only during the
switch turn-off ringing interval, the ringing resonance is the root cause. Apply damping
(snubber, ferrite bead) to that specific ringing.

#### 9.6.3 Mask Triggering

Oscilloscope mask testing allows:
- Defining pass/fail limits on the time-domain waveform
- Triggering on waveform anomalies (glitches, overshoots, ringing exceeding limits)
- Capturing rare events that cause intermittent EMI failures
- Setting masks based on EMI limit lines on the FFT display

### 9.7 Debugging Conducted Emissions

**Step-by-step procedure (Mediano):**

1. **Measure with LISN + oscilloscope FFT** -- capture the conducted emission spectrum
   on both L and N lines
2. **Separate DM and CM** -- use a noise separator or measure both lines:
   - If L and N spectra are similar (within 3 dB): CM dominant
   - If L and N spectra differ significantly: DM dominant or mixed
3. **Identify frequency ranges of failure** -- compare against CISPR limits
4. **Apply targeted fixes:**
   - For DM failures: add/increase X-capacitors or DM inductance
   - For CM failures: add/increase Y-capacitors or CM choke
   - Always verify with re-measurement after each change

**Iterative filter design example (from demo):**
- Start with a single X-capacitor: observe DM attenuation
- Add a CM choke (e.g., 10 mH WE-CMB) with 150 nF Y-caps: observe CM attenuation
- Filter location matters: placing the filter close to the noise source is more effective
  than placing it at the cable exit
- Two-stage filters provide steeper rolloff but require attention to inter-stage coupling

**Filter simulation vs reality:**
- Simulate the filter using measured component impedance data (not ideal models)
- Verify filter response using network analyzer or bode plot measurement
- Parasitic effects (see Sections 3.5-3.6) often dominate above a few MHz

### 9.8 Debugging Radiated Emissions

**Step-by-step procedure (Mediano):**

1. **Identify the radiating structure:**
   - Use H-field near-field probe to scan the PCB for high di/dt loops
   - Use E-field probe to find high dv/dt nodes
   - Use a current probe on cables to check if cables are the antenna

2. **Correlate antenna and current probe measurements:**
   - If a current probe on the power cable shows the same spectral peaks as the antenna
     measurement, the cable is the primary radiating element
   - Cable radiation is typically caused by CM current flowing on the cable shield or
     on both conductors in phase

3. **Apply targeted fixes:**
   - **Cable CM current:** Add a ferrite (snap-on core) on the cable -- this adds CM
     impedance and reduces CM current. Example: Wurth 74271142 ferrite on cable.
   - **Feedthrough capacitors:** Replace standard connectors with feedthrough capacitor
     connectors to filter HF noise at the enclosure boundary
   - **Switching node ringing:** If radiated emission peaks correspond to ringing
     frequency, add damping (ferrite bead in series with the ringing path, or RC snubber)

4. **Verify with current probe and NFP after each fix:**
   - Current probe on cable should show reduced HF current
   - NFP scan should show reduced field intensity at the source location

### 9.9 Damping Switching Node Ringing

Ringing on the switching node is a common cause of both conducted and radiated emissions,
especially at frequencies above 30 MHz.

**Root cause:** Parasitic LC resonance between switch output capacitance (Coss) and
parasitic loop inductance (Lloop):

```
f_ringing = 1 / (2*pi*sqrt(Lloop * Coss))

Typical: Lloop = 5-20 nH, Coss = 50-500 pF
         f_ringing = 50-300 MHz
```

**Damping methods:**
1. **Ferrite bead in series with gate:** Damps the gate drive and slows switching
   transitions. Cost: slightly increased switching loss.
2. **RC snubber across the switch:** Damps the ringing directly. R chosen for critical
   damping (R ~ sqrt(Lloop/Coss)), C chosen much larger than Coss.
3. **Reduce loop area:** Shorter traces, tighter component placement. This is the
   best permanent fix.

**Using FFT gating to identify ringing:**
- Gate the FFT to the ringing interval only (e.g., 50-200 ns after switch transition)
- The resulting spectrum shows the ringing frequency and its harmonics
- If this matches the emission failure frequency, the ringing is confirmed as the root cause

### 9.10 Shielding Effectiveness Evaluation with NFPs

Near-field probes can evaluate shield effectiveness:
1. Scan the outside of a shielded enclosure with an H-field probe
2. High readings at seams, gaps, or apertures indicate shield leakage
3. Tightening fasteners, adding EMI gaskets, or overlapping seam geometry can
   improve shielding
4. Re-scan after modifications to verify improvement

**Key principle (Mediano):** "If you can see it, you can fix it." The oscilloscope with
appropriate probes makes the EMI problem visible in both time and frequency domains,
enabling systematic debugging rather than trial-and-error.

### 9.11 Summary of Recommended Equipment

| Equipment | Purpose | Typical Spec |
|-----------|---------|-------------|
| Oscilloscope (4-ch) | Time + FFT analysis | >= 500 MHz BW, >= 2 GSa/s |
| Voltage probe (passive) | Switching node, gate, supply | 500 MHz, 10x |
| AC current probe | Cable current, switch current | 1 MHz - 100 MHz |
| H-field NFP | PCB current loop identification | Shielded loop, 1 cm diameter |
| E-field NFP | PCB voltage node identification | Monopole, ~5 mm tip |
| LISN | Conducted emission measurement | 50 uH / 50 Ohm, per CISPR 16 |
| Small antenna | Radiated pre-screening | Biconical or log-periodic |

### 9.12 Practical Tips

1. **Use 4 oscilloscope channels simultaneously** -- e.g., Ch1: LISN output, Ch2: current
   probe on cable, Ch3: voltage probe on switching node, Ch4: NFP on PCB. This allows
   simultaneous correlation of all signals.
2. **Always check both time and frequency domains** -- some problems are obvious in one
   domain but hidden in the other.
3. **Document before and after** -- capture screenshots with identical scope settings before
   and after each modification for quantitative comparison.
4. **FFT gating is underused** -- it is the single most powerful feature for identifying
   which time-domain event causes which spectral peak.
5. **Near-field probes are cheap and effective** -- commercial sets are available, or they
   can be made from semi-rigid coax. They should be in every EMC debugging toolkit.
6. **Ferrites on cables are diagnostic tools, not final solutions** -- if a snap-on ferrite
   fixes a radiated emission problem, the root cause is CM current on the cable. The
   permanent fix should address why CM current is flowing (improve filtering, reduce
   parasitic capacitance to ground, improve grounding).

---

## Wurth EMC Application Notes

Sources:
- Wurth ANP049a: "EMC & Efficiency Optimization of High Power DC/DC Converters" (2018)
- Wurth ANP005b: "EMC Filter for DC/DC Switching Controller Optimized" (2012)

### 10.1 High-Power DC/DC EMC Design (ANP049a -- 100 W Buck-Boost Example)

#### Design Constraints

Achieving both high efficiency (>95%) and CISPR32 Class B compliance in a 100 W DC/DC converter with long cables (1 m input and output) and no shielding requires:
1. Very low-inductance, compact PCB layout
2. Filters harmonized with the converter at both input and output
3. Broadband interference suppression from 150 kHz to 300 MHz

#### Inductor Selection

Use manufacturer tools (e.g., REDEXPERT) to compare inductors based on complex AC+DC losses and resulting component heating, not just datasheet parameters. For a 4-switch buck-boost:
- Calculate inductance for both buck mode (larger L, smaller I_peak) and boost mode (smaller L, larger I_peak)
- Select a single inductor that satisfies both operating modes
- Shielded inductors with soft saturation and temperature-independent characteristics are preferred

#### Input/Output Capacitor Selection

**Input capacitance (buck-boost):**

```
C_in >= D * (1-D) * I_out_max / (dV_in_pp * f_sw)
```

**Output capacitance (buck mode):**

```
C_out >= dI_L / (8 * V_out_ripple * f_sw)
```

**Key considerations:**
- Use a combination of MLCC (X7R) + aluminum polymer capacitors
- MLCC DC bias derating: expect 20% capacitance loss at rated voltage for X7R 50 V parts (verify with manufacturer DC bias curves)
- Aluminum polymer provides bulk capacitance and fast transient response
- Voltage rating: select capacitors rated >= 1.25x the maximum operating voltage

#### PCB Layout Rules (EMC-Optimized)

**Top layer:**
1. Place ceramic blocking capacitors as close as possible to the switching IC -- minimizes high-dI/dt loop area
2. Separate AGND copper surface for sensitive analog circuits -- connect to PGND at a single point (e.g., IC analog ground pin)
3. Compact bootstrap circuit very close to the IC
4. Route current measurement connections as differential lines with Kelvin connection to shunts
5. Broadband pi filter to decouple the IC internal power supply
6. Maximize vias for low-inductance PGND connections to inner layers

**Bottom layer:**
7. Place FETs and blocking caps on the bottom for the tightest possible power loop
8. Use copper surfaces (not traces) for FET-to-FET and FET-to-shunt connections -- minimizes impedance and inductance
9. Current shunt with reverse geometry for lowest parasitic inductance
10. Bottom-side FET placement improves thermal dissipation (no large components blocking heat flow)
11. Place ultrafast Schottky diodes (for body diode clamping) directly adjacent to FETs

**Intermediate layers:**
12. Use all intermediate layers as essentially solid PGND planes:
    - Enables current return paths with minimum loop area
    - Converts some HF energy to heat via eddy currents (absorption effect increases with proximity to HF components)
    - Provides partial shielding
    - Route gate drive traces between two PGND layers for complete shielding
13. Place PGND vias at regular intervals around the board edge to suppress edge radiation

#### Filter Design for Class B Compliance

**Without filters:** Even with an optimized layout, a high-power converter with long cables will not meet Class B limits for conducted emissions (150 kHz - 30 MHz) or radiated emissions (30 MHz - 1 GHz).

**Filter architecture (three frequency ranges):**

| Frequency Range | Primary Filter Element | Purpose |
|---|---|---|
| 150 kHz - 10 MHz | CM choke (sectional winding) + X/Y caps | CM and DM suppression at fundamental and harmonics |
| 10 MHz - 100 MHz | T-filter with ferrite beads + ceramic caps | Broadband suppression of HF harmonics and ringing |
| 100 MHz - 1 GHz | Multilayer power suppression beads | Parasitic resonance and switching edge suppression |

**CM choke selection criteria:**
- Maximum possible CM impedance over a wide frequency range (150 kHz - 300 MHz)
- Sectional winding for maximum leakage inductance (free DM filtering)
- Low Rdc to minimize efficiency impact
- Compact SMT package

**Filter placement rules:**
1. Arrange filter banks to eliminate inductive and capacitive coupling with the converter main circuit
2. No copper in inner layers below the filter banks -- prevents galvanic coupling that reduces filter capacitor effectiveness
3. Design T-filters so that inter-component capacitive and inductive couplings are minimized
4. No copper under CM chokes to minimize capacitive coupling to ground plane

**Achievable performance:** With proper filter design, >80 dB differential mode insertion loss up to 500 MHz is achievable. The simulated filter response should be verified against measured component impedance data (not ideal models).

**Efficiency impact of filters:**
```
P_filter_output = I_out^2 * Rdc_filter_output
P_filter_input = I_in^2 * Rdc_filter_input
```
In the 100 W example: output filter loss = 5.5^2 * 0.046 = 1.4 W, input filter loss = 7^2 * 0.078 = 3.8 W. Total filter loss ~5.2 W (~5% efficiency penalty). Use state-of-the-art low-Rdc components to minimize this.

**Measured results:** Buck mode efficiency 96.5%, boost mode 95.6% (including all filter components). Maximum component temperature below 64 C at 22 C ambient.

### 10.2 Input Filter Design for DC/DC Switching Controllers (ANP005b)

#### Fundamental Design Rules

1. **Corner frequency placement:** Set the input filter corner frequency to 1/10 of the switching controller frequency. This ensures adequate separation from the control loop crossover frequency for stability.

2. **Filter inductor selection:**
   - Choose a high self-resonant frequency (SRF) -- the coil loses filtering capacity above SRF due to parasitic capacitance
   - Saturation current >= 1.1x the peak input current
   - Minimize Rdc to reduce DC voltage drop

3. **Filter capacitor selection:**
   - Maximum operating voltage >= 1.25x the supply voltage (accounts for DC bias derating)
   - Low ESL for high SRF
   - A relatively high ESR is acceptable (even beneficial) because it damps the filter resonance peak
   - Electrolytic capacitors are preferred for their inherent damping

4. **Impedance matching:** The switching controller input impedance is low (due to the input capacitor). Place the filter inductor between the source and the controller input cap, with the filter capacitor after the inductor and parallel to the source. This matches the impedance mismatch rule (Section 3.1).

#### Negative Input Resistance and Stability

The switching controller presents a negative input resistance:

```
R_in = -Vin^2 / Pout
```

If the filter's output impedance peak (at its resonant frequency) exceeds |R_in|, the system oscillates. The filter resonance must be well-damped:
- Electrolytic filter capacitors provide natural damping via ESR
- If using MLCC filter caps (very low ESR), add explicit damping (series R or parallel RC)
- The filter corner frequency must be far below the control loop crossover

#### Practical Results

A single LC filter stage (1 uH inductor + 10 uF electrolytic) on a 2 MHz switching controller achieved:
- 30 dB attenuation of the fundamental
- All higher harmonics reduced to ambient noise floor
- Increasing inductance to 4.7 uH with the same capacitor further improved attenuation by ~10 dB

**Caution with increased inductance:** Larger inductance lowers SRF, potentially degrading HF performance. Always verify the inductor impedance at the frequencies of concern.

---

## EMI Filter Design -- Comprehensive Treatment (from Ozenbaugh & Pullen, 3rd ed 2012)

This is the only standalone book dedicated to EMI filter design. It covers the full methodology from fundamentals through worked design examples, filling the gap between "you need a filter" and "here is exactly how to design one."

### Why EMI Filter Design Is Called "Black Magic" (Ch1-2)

EMI filter design is NOT black magic -- it is parametric uncertainty. The mystique comes from:

1. **No single deterministic solution.** Unlike a Butterworth active filter where you place poles precisely, EMI filters face unknown and varying source/load impedances across frequency. Each application is unique.

2. **EMI filters vs conventional filters.** Conventional filter designers know source and load impedances (usually equal), care about group delay, ripple, and precise pole placement. EMI filter designers think in insertion loss, stability, and number of stages. EMI components are flexible standard values -- precise placement of the -3 dB corner is secondary to meeting insertion loss where needed.

3. **Most stop-band energy is reflected, not absorbed.** This is often forgotten. The remaining energy is dissipated in inductor DCR, core losses, and capacitor ESR.

4. **The filter interacts with the system.** A filter that passes 50-ohm bench testing can fail in-system because real source impedance is NOT 50 ohm at low frequencies. The LISN does not reach 50 ohm until well above 100 kHz.

5. **Parasitic uncertainty.** Layout coupling between input and output, component SRF limits, stray capacitance, and ground impedance are never fully known at design time.

**Key insight:** "Black magic" is really a name for parametric uncertainty -- factors and inherent physics not realizable from the outset. Mathematics allows the solution to be numerically approximated through iterative steps, but the filter MUST be designed defensively with room for tuning during test.

**The 50-ohm trap:** Most off-the-shelf filters are designed and tested at 50 ohm (the LISN impedance). But real source impedance is near zero at DC, about 4 ohm at 10 kHz, and only reaches 50 ohm near 100-250 kHz. A Pi filter with input capacitor facing a low-impedance source loses that capacitor's contribution entirely -- the CIP (current injection probe) test method exposes this, whereas the 220-A test method masks it.

### Common Mode vs Differential Mode (Ch3)

#### Definitions
- **Differential mode (DM, normal mode):** Voltage appears between line and return. Noise travels in opposite directions on the two conductors. Caused by power supply switching, load switching, PWM ripple currents.
- **Common mode (CM):** Voltage appears between BOTH lines and ground, in the same direction. Caused by dv/dt coupling through parasitic capacitances (transformer interwinding capacitance, switch-to-heatsink capacitance, PCB stray capacitance to chassis).

#### Origin of CM Noise
In a flyback converter: each time the power switch conducts, a large dv/dt is impressed across the transformer interwinding capacitance. This forces current to flow from primary to secondary through Cc (parasitic capacitance). This current flows twice per switching cycle and must find a return path -- it flows through any available ground connection. If the ground path has inductance L, the rapid di/dt creates V = L(di/dt) noise on the ground bus.

**Critical rule:** Grounds must be solid and low inductance. Loop areas must be minimized.

#### What Removes Each Mode

| Component | DM | CM |
|---|---|---|
| Line-to-line capacitors (X-caps) | YES | No |
| Line-to-ground capacitors (Y-caps, feed-throughs) | No | YES |
| Differential-mode inductor | YES | No |
| Common-mode choke (Zorro) | No | YES (flux cancels for DM) |
| Isolation transformer | Partially (core losses) | YES (breaks galvanic path) |
| Faraday shield in transformer | No | YES (reduces Cp-s) |
| MOV/TVS (line-to-line) | DM transients | No |
| MOV/TVS (line-to-ground) | No | CM transients |

#### Balanced Line Leakage Current Trick (220V Systems)
For two-phase 220V balanced systems, two equal capacitors from each line to a junction point, plus a third capacitor from junction to ground. If line voltages are equal and opposite and capacitors match, ground current is nearly zero at line frequency. The common-mode noise (in-phase signals) sees all three capacitors -- low impedance to ground.

Leakage current calculation for the three-capacitor arrangement:
```
C = 3 * I_leakage / (4 * pi * F * V * t)
```
where t = capacitor tolerance (e.g., 0.05 for 5%), V = line-to-ground voltage, F = line frequency.

**WARNING:** Never use this for medical equipment -- if one line opens, full line-to-ground voltage appears across one capacitor, driving ground current to dangerous levels (e.g., 45 mA).

### Source and Load Impedance (Ch4-6)

#### Why Impedance Matters
The insertion loss of any filter depends on both the source and load impedance it sees. A filter optimized for 50-ohm source/load will perform very differently when connected to a real power line (near 0 ohm at low frequencies) and a real load (switching converter with negative incremental resistance).

#### AC Power Line Source Impedance
- At DC to ~5 kHz: nearly resistive, close to DCR of the wiring (near zero for commercial power, higher for remote/shipboard)
- At ~10 kHz: approximately 4 ohm on most lines
- At ~100 kHz (longer lines) to ~250 kHz (shorter lines): impedance ripples its way to approximately 50 ohm
- The characteristic impedance of open-wire power lines: typically 50-180 ohm
- Paired/twisted wire in conduit: typically 50-90 ohm
- Lines appear electrically about 8x their physical length due to slow velocity of propagation

**Skin effect in power lines:** The characteristic impedance Z0 = sqrt(L/C) is NOT affected by skin effect. However, the loss per unit length increases with frequency. For copper:
```
Skin depth (cm) = 6.61 / sqrt(F)
```
This dissipation of HF energy in the line is beneficial -- it helps attenuate noise.

**For filter inductors: NEVER use Litz wire.** Allow single-strand wire to dissipate upper frequencies via skin effect. Exception: capacitor leads to ground should use Litz or braid for lowest impedance to preserve the capacitor's SRF.

#### DC Circuit Source and Load
- Battery sources look capacitive at mid-frequencies (plate capacitance shunts noise) but inductive at high frequencies (cable inductance)
- DC power supply output impedance: milliohms at DC, but rises to high impedance at switcher frequency unless specifically designed otherwise
- The filter "fixes" either condition -- the input inductor presents high impedance regardless of source impedance

**DC filter capacitor sizing for switcher load:**
```
C = 10 * i_peak / (2 * pi * F_sw * V_dc)    [farads]
L = 10 * V_dc / (2 * pi * F_sw * i_peak)     [henries]
```
where F_sw = switching frequency, i_peak = peak on-current, V_dc = DC voltage.

### EMI Filter Topologies -- Pros and Cons (Ch7)

#### Pi Filter
- Looks excellent under 220-A 50-ohm bench test (capacitor works into 50-ohm source)
- **FAILS in real-world** when source impedance is low: the input capacitor is shunted, reducing the filter to a two-pole response (12 dB/octave instead of 18 dB/octave)
- CIP test method exposes this -- shows ~6 dB less loss at lower frequencies
- Still widely used because it passes the 220-A specification
- Center capacitor in multi-stage Pi is twice the end capacitor values (two half-caps combine)

#### T Filter
- Input inductor works regardless of source impedance -- always adds loss
- Does not suffer the Pi filter's source-impedance problem
- Good for CIP testing and real-world applications
- Disadvantage: does not perform as well in the 220-A 50-ohm test as the Pi (inductor must reach 50 ohm before adding loss)

#### L Filter
- Simplest two-pole filter (one inductor + one capacitor)
- **Capacitor-facing-load** orientation is preferred for DC applications (low-impedance supply for the switcher)
- L filter with capacitor facing the LINE has the same problem as the Pi filter -- CIP shunts it
- Inductor facing the line: always functions, regardless of source impedance

#### Multi-stage Filters
- Double L, double Pi, double T: four poles, 24 dB/octave asymptotic slope
- Triple: six poles, 36 dB/octave
- **Key advantage of more stages:** the cutoff frequency moves HIGHER for the same total insertion loss. This reduces the resonant rise at low frequencies (critical for 400 Hz systems)
- **Impedance matching between stages:** Adjacent LC sections must be optimized for impedance to avoid peaking at mismatch frequencies

#### Cauer (Elliptic) Filter
- One or more inductors are tuned (resonant at a specific frequency, e.g., 100 kHz) with a parallel capacitor
- Adds a notch of very deep loss at the tuned frequency
- Allows all other component values to decrease, raising the overall cutoff frequency
- Excellent for 400-Hz systems where the cutoff must be as high as possible

#### dQ (De-Queing) Networks
- RC shunt across a capacitor, or series LR in parallel with an inductor
- Purpose: reduce circuit Q to below 2 to prevent parasitic oscillations and resonant rises
- Required in virtually all practical EMI filters

### Filter Components (Ch8-10)

#### Capacitors (Ch8)

**Voltage ratings:**
- AC capacitors: must withstand 4.2x RMS peak voltage (e.g., 132V peak in 120V system -> 554V test voltage)
- DC capacitors: must withstand 2.5x working voltage

**Self-Resonant Frequency (SRF):**
- Every capacitor becomes INDUCTIVE above its SRF
- SRF is determined by the parasitic ESL (equivalent series inductance)
- Feed-through capacitors have the highest SRF (lowest ESL) due to their construction
- Large can-type capacitors may have SRF as low as 50 kHz -- useless above that frequency

**Capacitor types for EMI filters:**
- **Extended-foil:** lowest ESR and ESL, highest SRF. Preferred for EMI filters. Optimal diameter-to-height ratio is 2:1.
- **Tab type:** for high-current applications. Tabs inserted during winding carry current.
- **Metallized film:** smaller, self-healing, but cannot handle high pulse currents. OK for upstream stages, NOT for the capacitor facing the load.
- **Inductive (chicklet):** NEVER use for main filter capacitors. ESR approximately 12 ohm, ESL approximately 45 uH for a 100-ft foil. Only usable for RC shunt dQ networks at low frequencies.

**Veeing the capacitor:** Connect inductor self-leads directly to the capacitor terminals. This eliminates the parasitic inductance of separate lead wires that would lower the capacitor SRF. The lead inductance simply adds a tiny increment to the inductor value.

**Foil vs metallized for load-facing capacitor:** The last capacitor in the filter (closest to the switching load) MUST be foil type, not metallized film. It must handle pulse currents from PWM switching.

#### Inductors (Ch9)

**Core types for EMI filter inductors:**

| Core Type | Saturation (gauss) | Usable (gauss) | Gap | Notes |
|---|---|---|---|---|
| MPP (Molypermalloy Powder) | 7,000 | 3,500 | Distributed | High Q, primary choice for DM inductors |
| High Flux (HF) | 15,000 | 7,000 | Distributed | Higher current capability |
| Kool Mu (Sendust) | 10,000 | 5,000 | Distributed | Lighter, less expensive |
| Powdered Iron | varies | varies | Distributed | Least expensive, least stable (permeability varies with drive level) |
| Ferrite | ~4,000 | low | None (would need gap) | Use ONLY for CM chokes, NOT for DM inductors -- saturates too easily under DC bias |
| C-Core (steel) | ~18,000 | 12,000 | Cut gap | High current (300A+), low Q |
| Tape-wound toroid | ~16,000 | 15,500 | Cut gap | Heavy, primarily for transformers |
| Nanocrystalline | high | high | No | Very high mu (20k-80k), fewer turns, lighter. For CM chokes. |

**For 400 Hz and above:** Do not use permeabilities above 125 for powder cores.

**For DC filters:** Restrict flux density to 3,500 gauss for powder cores.

**Inductor design using AL values:**
```
N = N_ref * sqrt(L_required / A_L)
```
where N_ref is the reference turns (typically 1000), L_required and A_L are in the same units (mH).

Check window fill: circular_mils_per_amp * N_turns / winding_factor < window_area_circular_mils. Winding factor = 0.4 for toroids.

**Increasing SRF of inductors:**
1. Coat or tape cores to reduce wire-to-core capacitance
2. Progressive (pilgrim step) winding: 6 forward, 4 back -- reduces turn-to-turn voltage differential
3. Section wind: clumps of turns with gaps between sections
4. Series-connect smaller inductors (each has higher SRF)
5. Use heavier insulation to increase turn-to-turn spacing

**Converting unbalanced to balanced:** Split each inductor L into two L/2 inductors (one per leg). The Y-cap to ground becomes an X-cap line-to-line. This removes the cap from the ground-capacitance budget. Then add a common-mode choke and feed-through Y-caps.

#### Common-Mode Components (Ch10)

**Common-mode choke ("Zorro") design:**
- Each winding reads the rated inductance individually (e.g., 10 mH per winding)
- Both windings in series aiding: 4x the single-winding inductance (40 mH)
- But windings are split across two legs and effectively in parallel: back to 10 mH
- Leakage inductance (typically 0.5%-2% of CM inductance) contributes to DM loss
- Window fill factor drops to 0.2 (two windings instead of one)

**Leakage current limits (capacitance to ground):**
- Commercial: 5 mA system total
- Medical (non-patient-contact): 300 uA system
- Medical (patient-contact): 100 uA total, filter often limited to 20-50 uA
- MIL-STD-461 at 400 Hz: max 0.02 uF to ground

**Virtual ground technique (3-phase):** Connect a capacitor from each phase to a common junction, plus a fourth capacitor from junction to ground. If voltages are balanced and capacitors are matched, ground current approaches zero. The common-mode noise sees all capacitors in parallel -- very low impedance to ground.

**Double Zorro technique:** Split the filter into two cavities with a grounded barrier/shield between them. Place a CM choke in each cavity with feed-through capacitors at input, center shield, and output. This creates a double-Pi common-mode filter and dramatically reduces the size of each CM inductor.

### Transformer's Role in EMI Filtering (Ch11)

The isolation transformer is often overlooked as a filter element but provides:
- **Common-mode rejection:** CM current does not create a magnetic field in the transformer; coupling is only through parasitic interwinding capacitance
- **Differential-mode loss:** Core losses at higher frequencies provide approximately 6 dB/octave (20 dB/decade) starting near the 5th harmonic of the line frequency
- **Isolation:** breaks galvanic CM path
- **Ruggedness:** handles voltage spikes without difficulty

**Core loss as a filter element:**
For 12-mil steel at 900 gauss: approximately 6 dB/octave loss starting from about 300 Hz (5th harmonic of 60 Hz). Example: 6 dB at 600 Hz -> 36 dB at 20 kHz. A double-L filter added after the transformer adds another 24 dB, reaching 60 dB total at 20 kHz with very small components.

**Interwinding capacitance (primary-to-secondary):**
```
C_ps = I_leakage / (2 * pi * V * F)
```
For 100 uA leakage at 120V/60Hz: approximately 2,210 pF. A Faraday shield between windings reduces this substantially.

**The transformer becomes ineffective** at frequencies where the interwinding capacitance impedance equals the source+load impedance (approximately 700 kHz for well-designed transformers at 50-ohm test impedance).

### Surge Protection Integration with EMI Filter (Ch12)

#### Arrester Placement -- Three Theories

1. **No arrester -- let the filter handle it.** Risky: inductor must withstand full pulse voltage without arcing; capacitor must handle 2x pulse voltage. Requires special insulation, separated turns.

2. **Arrester at equipment end.** Protects equipment but not the filter. If filter components fail from the pulse, equipment may still be unprotected.

3. **Arrester at filter input (RECOMMENDED).** Place TVS/MOV at the input, followed by a series inductor. The inductor presents high impedance to the pulse, causing voltage to rise quickly and fire the arrester fast. The arrester then clamps the voltage, and only the clamping voltage reaches the filter capacitors.

**Critical design rule:** Never place a capacitor in parallel with the TVS. This creates a low-impedance path that delays TVS turn-on, allowing higher transient stress on downstream components.

**TVS selection procedure:**
1. Standoff voltage VWM > max operating voltage (including tolerance)
2. Breakdown voltage VBR = VWM x 1.2 (20% margin for temperature coefficient)
3. Clamping voltage VCL = VBR x 1.3 to 1.4
4. All filter capacitors downstream must be rated >= 2 x VCL
5. Joule rating based on pulse waveform shape factor K:
   - Ramp: K = 0.5
   - Constant height: K = 1.00
   - Sine pulse: K = 0.637
   - Damped sine: K = 0.86
   - Exponential: K = 1.4

**For balanced single-phase:** Three TVS devices needed -- hot-to-ground, neutral-to-ground (CM protection), and line-to-line (DM protection). For three-phase: six devices (three line-to-ground, three line-to-line).

### What Compromises the Filter (Ch13)

This is CRITICAL practical content. A 60 dB filter can become a 24 dB filter through layout and integration mistakes.

#### Layout Failures
1. **Input/output proximity:** The filter input (dirty side) and output (clean side) must be physically separated. If they are close, capacitive and inductive coupling bypasses the filter entirely.
2. **Short leads and traces:** Every millimeter of lead adds inductance that lowers component SRF and degrades HF performance.
3. **Adjacent component coupling:** Magnetic coupling between inductors in adjacent stages. Mount inductors in quadrature (perpendicular orientations) to minimize mutual inductance.
4. **Filter on PCB next to sensitive circuits:** The filter needs its own shielded area. Open/exposed components rarely work -- adjacent magnetic fields couple into filter inductors.

#### Grounding Failures
5. **Poor ground connection of filter enclosure:** Feed-through capacitors and CM arresters require a low-impedance ground. A resistive or missing ground wire renders them useless.
6. **Equipment ground shorts bypassing balanced filter:** If the equipment has an internal ground connection to chassis, the bottom-leg inductors get bypassed and the X-cap gets an inductor in series, destroying filter performance.

#### Component Failures
7. **Capacitor above SRF:** A capacitor becomes an inductor above its SRF. Two capacitors in parallel where the larger one is above SRF create a parallel tank circuit -- high impedance exactly where you need low impedance.
8. **Inductor saturation under DC bias:** Ferrite cores saturate easily under load current. The filter passes bench testing at low drive levels but fails under operating current.
9. **Wrong capacitor type:** DC-rated metallized film capacitor in an AC filter position. It cannot handle the harmonic currents and will overheat and fail within months.

#### System Integration Failures
10. **Filter output impedance vs converter negative resistance:** If |Z_out| >= |R_negative|, the system WILL oscillate. This is the Middlebrook criterion.
11. **Resonant rise at line frequency:** For 400-Hz systems, the filter resonant rise can be at or near 400 Hz, causing voltage overshoot. Multi-stage filtering raises the cutoff frequency away from 400 Hz.
12. **Untested filter-to-system interaction:** A filter can pass insertion loss testing and fail when connected to the actual system.

### Initial Filter Design Requirements (Ch15)

#### Design Requirements Checklist
1. Equipment application and EMC specification (CISPR, DO-160, MIL-STD-461)
2. Mechanical constraints (form factor, weight)
3. Input power source (AC single/three-phase, DC single-ended/floating)
4. Output power load requirements
5. Switching frequency (for PWM converters)
6. DM design goals (defined through FFT analysis of current signature)
7. CM design goals (approximated, often requires defensive over-design)
8. Inrush, lightning, and power-interrupt requirements

#### DM Design Goal: Filter Transparency
The DM filter should be transparent to the power line. The filter input impedance at the Nth harmonic should equal the load impedance:
```
Z_in at N*f_line = R_load
```
The cutoff frequency should be above the 15th harmonic of the line frequency (easy for 50/60 Hz, very hard for 400 Hz where the 15th harmonic = 6 kHz).

#### DM Filter Output Impedance
```
Z_out at N*f_line = R_source
```
For DC filters feeding a switching converter, the output impedance must be very low at the switching frequency and its harmonics:
```
Z_out << R_negative = -(V_in^2 * eta) / P_out
```
At 10% of the negative resistance:
```
C >= 10 * i_peak / (2*pi*F_sw*V_dc)
```

**Incremental negative resistance** is the key stability constraint. A PWM converter holds its output constant; if input voltage rises, input current falls. This negative dV/dI creates a negative resistance at the converter input:
```
R_n = -V_in / I_in
```
R_n is smallest (most dangerous) at full load and minimum input voltage. The filter output impedance MUST be less than |R_n| at all frequencies.

#### CM Design Goals
- No bandwidth or harmonic transparency requirements
- Cutoff frequency can be arbitrarily low (limited only by inductor size)
- Leakage inductance of the CM choke (typically 1-2%) contributes to DM loss
- The CM source impedance is much higher than DM source impedance (parasitic capacitances are small), so the CM filter design impedance is also higher

### Matrices, Transfer Functions, and Insertion Loss (Ch16-17)

#### The K-Value Method

The K value is the ratio of the frequency of interest to the -3 dB cutoff frequency:
```
K = F_required / F_cutoff
```

Given a required insertion loss in dB at a known frequency, look up the K value from the topology tables (Appendix A), then:
```
F_cutoff = F_required / K
```

Then calculate L and C for the chosen design impedance Rd (typically 50 ohm):
```
L = Rd / (2*pi*F_cutoff)     [henries]
C = 1 / (2*pi*F_cutoff*Rd)   [farads]  (= L / Rd^2)
```

#### dB Loss Equations by Topology (at 50 ohm source and load)

**Single L filter:**
```
dB = 20*log10( sqrt(K^4 + 4) / 2 )
```

**Single Pi or T filter:**
```
dB = 20*log10( sqrt(K^6 + 64) / 8 )
```

**Double L filter:**
```
dB = 20*log10( sqrt(K^8 - 4*K^6 + 4*K^4 + 4) / 2 )
```

**Double Pi or T filter:**
```
dB = 20*log10( sqrt(K^10 - 4*K^8 + 64*K^6 + 4) / 8 )
```

**Triple Pi or T filter:**
```
dB = 20*log10( sqrt(K^14 - 8*K^12 + 22*K^10 - 24*K^8 + 9*K^6 + 64) / 8 )
```

#### Quick Design Example (Single Pi)

Given: Outages at 160 kHz (need 15 dB) and 250 kHz (need 28 dB).

From the K tables: K=3.6 for 15 dB single Pi, K=5.9 for 28 dB single Pi.

Cutoff frequency: 160k/3.6 = 44.4 kHz, 250k/5.9 = 42.4 kHz. Use the lower: 42.4 kHz.

Component values at 50 ohm:
```
L = 50 / (2*pi*42400) = 187.7 uH
C = L/R^2 = 75 nF total (split: 37.5 nF each end)
```

Use nearest standard values: L = 200 uH, C = 2 x 39 nF.

#### Matrix Method for Any Topology

Each filter element is represented as a 2x2 ABCD matrix. The cascade product of all element matrices (including source and load) gives the overall transfer function.

Source matrix: [[1, R_s], [0, 1]]
Series inductor: [[1, jKR], [0, 1]]
Shunt capacitor: [[1, 0], [j*2/(KR), 1]]  (half-value cap uses j*2/(KR))
Load matrix: [[1, 0], [0, 1/R_L]]

Multiply left to right in circuit order. The voltage ratio V_i/V_o gives insertion loss.

### Network Analysis of LC Structures (Ch18)

#### Transfer Function of LC L-Section
For an L-section filter (series L, shunt C) with inductor series resistance r:

```
H(s) = 1 / (s^2*LC + s*Cr + 1)
```

In standard form:
```
H(s) = omega_0^2 / (s^2 + 2*zeta*omega_0*s + omega_0^2)
```

where:
- omega_0 = 1/sqrt(LC) (natural frequency)
- zeta = (r/2) * sqrt(C/L) (damping factor)
- Q = 1/(2*zeta) = (1/r) * sqrt(L/C) (quality factor)

**For stability: Q must be <= 1** (zeta >= 0.5). If Q > 2, parasitic oscillations and ringing are likely.

#### Stability Criterion for PWM Converter Load

The filter output impedance Z_out must satisfy:
```
|Z_out| << |R_n| for all frequencies
```

If Z_out and Z_in of the converter intersect on an impedance-vs-frequency plot, the overlap region must be analyzed for phase margin. The poles of (1 + Z_s/Z_i) must lie in the left-hand s-plane.

For a series L with DCR feeding a capacitor in parallel with negative resistance R_n:
```
Stability requires: R_dc << |R_n|
```

In practice, add a dQ shunt RC network to reduce filter output impedance at the resonant frequency.

#### Coefficient Matching for Butterworth Response
For a doubly-terminated LC section (R_s = R_l = R):
```
L = sqrt(2) * R / omega_0
C = sqrt(2) / (R * omega_0)
```
Filter characteristic impedance = sqrt(L/C) = R (matched). DC gain = -6 dB (voltage divider).

### Filter Design Procedure with Worked Examples (Ch19)

#### Complete Design Flow

1. **Verify EMC requirement** (CISPR 22, DO-160, MIL-STD-461, etc.)
2. **Specify input voltage and current,** including transient/lightning requirements
3. **Define inrush protection** needs (may drive filter component selection)
4. **Calculate converter negative resistance:**
   ```
   R_n = -(V_in_min)^2 * eta / P_out
   ```
   This is worst case at minimum input voltage and full load.
5. **Set filter output impedance limit:** Z_out << R_n (typically Z_out <= R_n/10)
6. **Define current signature** or estimate current waveform
7. **Option A:** Simulate PWM topology in SPICE, measure DM current, perform FFT to get harmonic magnitudes in dBuV or dBuA
8. **Option B:** Estimate peak fundamental harmonic analytically using Fourier coefficients
9. **Define insertion loss requirements** by overlaying FFT spectrum on limit curve (e.g., CISPR 22: 66 dBuV at 0.15-0.5 MHz). Add minimum 6 dB safety margin.
10. **Calculate -3 dB pole-Q frequency** using K-value method for chosen topology
11. **Define filter structure:** number of poles, topology (L, Pi, T)
12. **Calculate L and C values.** For 4+ pole filters: ensure impedance of each section < R_input
13. **Verify Z_out << R_n**
14. **Define stability factor (Q)** via step response analysis
15. **Add dQ damping** (RC shunt or series LR) as needed

#### Worked Example: 28V DC Flyback, 100W, 150 kHz, CISPR 22 Class A

**Given:**
- V_in = 18-32V DC (nominal 28V), P_out = 100W, eta = 90%
- Flyback, discontinuous mode, F_sw = 150 kHz
- CISPR 22 Class A: max 66 dBuV at 150 kHz, 60 dBuV at 0.5-50 MHz
- Isolated +28V (ground not connected to chassis at equipment)

**Step 1 -- Negative resistance:**
```
R_n = (18)^2 * 0.9 / 100 = 2.9 ohm
```
Filter output impedance must be <= 2.9 ohm.

**Step 2 -- Harmonic analysis:**
Simulate the flyback in SPICE or estimate the current waveform. The peak amplitude of the fundamental at 150 kHz determines how much insertion loss is needed to get below 66 dBuV with 6 dB margin (target: 60 dBuV or lower at the LISN output).

**Step 3 -- Pole-Q frequency and topology selection:**
From the required insertion loss at 150 kHz, use the K-value tables to find the cutoff frequency. If a two-pole (single L) filter gives a cutoff frequency that is impractically low, move to a four-pole (double L) structure.

**Step 4 -- Component values:**
```
L = R_d / (2*pi*F_cutoff)
C = 1 / (2*pi*F_cutoff*R_d)    where R_d = design impedance
```

**Step 5 -- Stability check:**
- Calculate Q = (1/R_dc) * sqrt(L/C). If Q > 2, add dQ damping.
- Verify Z_out < R_n across frequency (simulate output impedance magnitude plot)

**Step 6 -- dQ shunt design:**
An RC shunt network across one of the filter capacitors:
- R_dQ chosen so that at the resonant frequency, R provides sufficient damping
- C_dQ chosen so its impedance at the resonant frequency equals R_dQ
- Typical starting point: R_dQ = 2-3x the characteristic impedance, C_dQ = 2-4x the filter capacitor

**Step 7 -- Common-mode filter:**
- Add CM choke (Zorro inductor) before the DM filter
- CM choke inductance typically 0.5-33 mH
- Feed-through Y-caps to ground (limited by leakage current spec)
- The DM inductors in balanced configuration add to CM inductance (their parallel combination)
- Convert to equivalent unbalanced circuit for CM loss calculation

#### Four-Pole Filter Design Notes
For four-pole (double L or double Pi) designs:
- Separate the two pole-Q frequencies by at least one octave to avoid interaction
- Each section's impedance must be less than R_input
- The section closest to the load should have the lower impedance (larger C, smaller L)
- Simulate the complete filter including source, load, and dQ networks before finalizing

### Packaging and Layout (Ch20)

#### Physical Layout Rules
1. **Long and thin enclosure:** Length much greater than height and width
2. **Input at one end, output at the other:** Maximum physical separation between dirty and clean sides
3. **If input/output must be on the same face:** Install a full-length grounded shield between the two signal paths. Components run from front to back on one side, then double back on the other side.
4. **Mount inductors in quadrature** (perpendicular orientations) to minimize magnetic coupling between stages
5. **Separate inductors with capacitors:** The capacitor between two inductors increases their physical distance and reduces mutual coupling
6. **Enclosure must be a good conductor:** Silver plating (inside and out) is best for military applications. The H-field induces current on the enclosure surface -- better conductivity means less field escaping.
7. **Feed-through capacitors must have good ground to enclosure:** If the enclosure ground is resistive or the ground lead has inductance, feed-through capacitors and CM arresters will NOT function.
8. **Use Capcon lossy suppression tubing** on hookup wires for additional loss above 10 MHz (up to 100 dB at 10 GHz per foot of material).
9. **Do NOT use PCBs for high-current EMI filters** -- trace inductance and parasitic capacitance between power and ground planes degrade filter performance.

#### Volume and Weight Estimation
Using McLyman's area product method:
```
Energy: E = L * I_peak^2 / 2
Area product: Ap = |2E * 10^4 / (Bm * Ku * Kj)|^(1/X)
Volume: V_total = Kv * sum(Ap for all inductors)
```

| Core Type | Kj (25C) | X | Kv (cm3) | Kw (g) |
|---|---|---|---|---|
| Pot core | 433 | 1.20 | 14.5 | 48.0 |
| Powder core | 403 | 1.14 | 13.1 | 58.8 |
| Laminations | 366 | 1.14 | 19.7 | 68.2 |
| C core | 323 | 1.16 | 17.9 | 66.6 |

Weight-to-volume ratio for tubular EMI filters: approximately 1.5 oz per cubic inch.

Total filter volume: inductor volume / 0.6 (to account for capacitors, wiring, enclosure).

### Appendix: K-Value Quick Reference Tables

#### Single-Stage Filters (L, Pi, or T)

| K | Single L (dB) | Single Pi/T (dB) |
|---|---|---|
| 3.0 | 13.3 | 10.9 |
| 4.0 | 18.1 | 18.1 |
| 5.0 | 22.0 | 23.9 |
| 6.0 | 25.1 | 28.6 |

#### Double-Stage Filters (Double L, Double Pi/T)

| K | Double L (dB) | Double Pi/T (dB) |
|---|---|---|
| 3.0 | 30.0 | 27.5 |
| 4.0 | 41.0 | 41.0 |
| 5.0 | 49.2 | 51.1 |
| 6.0 | 55.7 | 59.3 |

#### Triple-Stage Filters (Triple L, Triple Pi/T)

| K | Triple L (dB) | Triple Pi/T (dB) |
|---|---|---|
| 4.0 | 63.9 | 63.9 |
| 5.0 | 76.4 | 78.3 |
| 6.0 | 86.4 | 89.9 |
| 7.0 | 94.7 | 99.5 |

**Usage:** Find the required dB loss in the table. Read the K value. Divide the problem frequency by K to get the cutoff frequency. Then L = Rd/(2*pi*Fc) and C = 1/(2*pi*Fc*Rd).

### Butterworth Normalized Coefficients (Appendix B)

For filters requiring a specific amplitude response, normalized Butterworth coefficients are available for orders 1-10. These assume omega_c = 1 rad/s and R_source = R_load = 1 ohm. Scale to actual frequency and impedance:

```
L_actual = L_normalized * R / omega_c
C_actual = C_normalized / (R * omega_c)
```

| Order | C1 | L2 | C3 | L4 | C5 |
|---|---|---|---|---|---|
| 1 | 2.000 | | | | |
| 2 | 1.414 | 1.414 | | | |
| 3 | 1.000 | 2.000 | 1.000 | | |
| 4 | 0.765 | 1.848 | 1.848 | 0.765 | |
| 5 | 0.618 | 1.618 | 2.000 | 1.618 | 0.618 |

### Conversion Factors (Appendix C)

```
dBuV = 20*log10(V) + 120
V = 10^((dBuV - 120)/20)
dBuV to dBuA: dBuA = dBuV - 20*log10(Z)     [Z typically 50 ohm]
dBuA to dBuV: dBuV = dBuA + 20*log10(Z)
dBV to dBuV: dBuV = dBV + 120
```

---

## System-Level EMC Engineering (from Henry Ott, 2009)

> Source: Ott, Henry W. "Electromagnetic Compatibility Engineering" (Wiley, 2009).
> THE system-level EMC reference. Covers grounding, shielding, cabling, passive component
> parasitics, conducted emissions, transient immunity, and ESD from a practical engineering
> perspective. Content below supplements the filter-focused material above with system-level
> design guidance.

---

### S1. Cable Coupling and Shielding (Ch2)

#### S1.1 Capacitive (Electric Field) Coupling Between Cables

Noise voltage coupled from conductor 1 to conductor 2 via stray capacitance C12:

```
V_N = j*omega*R*C12*V1        (when R << 1/(omega*(C12 + C2G)))
```

where R is the receptor circuit resistance to ground. The noise is proportional to frequency,
mutual capacitance, source voltage, and receptor impedance.

**Reduction methods:**
- Decrease receptor circuit impedance R
- Increase conductor separation (most benefit in first 40x conductor diameter)
- Add a grounded shield (shield must be grounded or it provides no capacitive shielding)

#### S1.2 Inductive (Magnetic Field) Coupling Between Cables

Noise voltage induced in a receptor loop of area A by magnetic flux density B:

```
V_N = j*omega*B*A*cos(theta)    or equivalently    V_N = j*omega*M*I1
```

where M is the mutual inductance between source and receptor circuits, I1 is the source current.

**Key identity:** The mutual inductance between a shield and its center conductor equals the
shield self-inductance: M = L_S. This is fundamental to understanding magnetic shielding
by cable shields.

**Reduction methods:**
- Reduce receptor loop area (use ground plane return, twisted pair)
- Increase separation (B falls as 1/r)
- Use twisted pair (cancels B fields from each wire)
- Twist pitch must be < lambda/20 at frequencies of concern (1 twist/inch good to ~500 MHz)

#### S1.3 Shield Effectiveness for Magnetic Fields

A nonmagnetic cable shield grounded at ONE end provides:
- Good electric field (capacitive) shielding
- NO magnetic field shielding

A nonmagnetic cable shield grounded at BOTH ends provides:
- Good electric field shielding
- Magnetic field shielding above the shield cutoff frequency

**Shield cutoff frequency:**
```
f_c = R_S / (2*pi*L_S)
```

Effective magnetic shielding begins at ~5*f_c. Typical values of 5*f_c:

| Cable Type | 5*f_c |
|---|---|
| RG-6A (double shielded) | 3.0 kHz |
| RG-213 | 3.5 kHz |
| RG-58C | 10.0 kHz |
| 22-ga shielded pair (Al foil) | 35 kHz |

**For maximum magnetic shielding:** Minimize shield resistance R_S (includes termination
resistance and any resistance in shield ground path). Never terminate a shield through a
resistor if magnetic shielding is needed.

#### S1.4 Shield Grounding Decision Guide

**Low frequency (< 100 kHz):**
- Ground shield at ONE end (source end preferred, or load end if source is floating)
- Prevents ground-loop current from flowing in shield
- Terminate shield to equipment enclosure, NOT to circuit ground
- Use shielded twisted pair: shield blocks E-field, twist blocks H-field

**High frequency (> 100 kHz):**
- Ground shield at BOTH ends
- Shield current generates canceling magnetic field
- Use 360-degree terminations (BNC, Type-N connectors), never pigtails
- An 8-cm pigtail on a 3.7-m cable can increase coupling by 40 dB at 1 MHz

**Both ends grounded, but signal ground at one end only:**
- Use circuit configurations A-D in Ott Fig. 2-46

**Hybrid grounding:** Capacitor at one end gives single-point ground at low frequency,
multipoint ground at high frequency.

#### S1.5 Coaxial Cable vs. Twisted Pair

| Property | Coax | Twisted Pair (UTP/STP) |
|---|---|---|
| Useful frequency | DC to UHF | DC to 100s MHz (Cat5e: 125 MHz, Cat6: 250 MHz) |
| Electric field protection | Good (grounded shield) | Poor unless balanced terminations |
| Magnetic field protection | Moderate (via loop area) | Excellent (twist cancellation) |
| Common impedance coupling | Yes (shield is signal return) | No (separate shield from signal) |
| Above ~1 MHz | Skin effect separates signal/noise currents | Inherently separate |

**Best practice:** Shielded twisted pair combines advantages of both -- shield blocks E-field,
twist blocks H-field, shield is not signal conductor (no common impedance coupling).

#### S1.6 Shield Transfer Impedance

```
Z_T = (1/I_S) * (dV/dl)    [ohm/m]
```

Lower Z_T = better shielding. At low frequency Z_T = R_DC of shield.

| Shield Type | HF Transfer Impedance |
|---|---|
| Solid tube | Decreases with frequency (best) |
| Braid (>95% coverage) | Increases above ~1 MHz (holes) |
| Braid + foil | Good to ~100 MHz |
| Spiral | Increases above ~100 kHz (use only for audio) |

**Braided shields:** Use >= 95% coverage for best shielding. Braid is 5-30 dB less effective
than solid shield for magnetic fields.

---

### S2. Grounding Strategies (Ch3)

#### S2.1 Signal Grounding Topologies

**Single-point ground (< 100 kHz):**
- Controls ground current path (directs I_g)
- Parallel (star) preferred over series (daisy-chain)
- Series ground: V_A = (I1+I2+I3)*Z1 -- all currents affect circuit 1
- Star ground: V_A = I1*Z1 -- only own current matters
- Fails at high frequency because parasitic capacitance creates multipoint ground

**Multipoint ground (> 100 kHz, all digital circuits):**
- Minimizes ground impedance Z_g (minimizes inductance L_g)
- Use ground planes or grids
- At HF, ground impedance = j*omega*L (resistance is irrelevant)
- A 1-ft length of 24-gauge wire has more inductive reactance than resistance above 13 kHz

**Hybrid ground (wideband signals):**
- Single-point at low frequency, multipoint at high frequency
- Implement with capacitor to ground at the "floating" end

#### S2.2 Ground Impedance

```
Z_g = R_g + j*omega*L_g
V_g = I_g * Z_g
```

At low frequency: minimize V_g by controlling I_g (single-point topology).
At high frequency: minimize V_g by minimizing Z_g (ground planes).

**At HF, ground plane current flows directly under the signal trace** (path of least
inductance, smallest loop area). Increasing plane thickness does NOT reduce HF impedance
(skin effect -- current only flows on surface).

#### S2.3 Ground Loops: When They Matter and How to Break Them

**Ground loops are mostly benign.** Problems occur at low frequency (< 100 kHz) with
sensitive analog circuits. Ground loops are seldom a problem in digital circuits.

**Three approaches:**
1. **Avoid:** Single-point or hybrid grounding (effective at LF only)
2. **Tolerate:** Low-impedance ground (ZSRP), higher signal levels, balanced circuits
3. **Break:** Transformers, common-mode chokes, optical isolators

**Common-mode choke analysis:**

Signal transmission (differential mode): Choke has no effect on signal when
L >> R_C2/omega (inductances cancel for differential current).

Common-mode rejection: Noise voltage across load:
```
V_N = V_G * (R_C2/L) / (j*omega + R_C2/L)
```

For good rejection: L >> R_C2/omega at noise frequency. Keep winding resistance R_C2 small.

**HF choke limitation:** Above ~30 MHz, parasitic capacitance C_S across windings limits
insertion loss to 6-12 dB. At HF the shunt capacitance, not inductance, determines performance.

#### S2.4 Equipment Grounding Best Practices

**Zero Signal Reference Plane (ZSRP):**
- Solid metallic ground plane connecting all equipment enclosures
- 3-4 orders of magnitude less impedance than any single wire
- Even at resonance, parallel paths keep impedance low
- Bond each enclosure to ZSRP at >= 4 points using short wide straps (L/W <= 3:1)

**Ground strap inductance (flat rectangular conductor):**
```
L = 0.002*l * [2.303*log(2*l/(w+t)) + 0.5 + 0.235*(w+t)/l]    [uH, cm]
```

| L/W Ratio | Inductance Reduction vs 100:1 |
|---|---|
| 100:1 | 0% (reference) |
| 10:1 | 33% |
| 5:1 | 45% |
| 3:1 | 54% |
| 1:1 | 72% |

**Strap resonance warning:** Parasitic capacitance between enclosure and ground plane
resonates with strap inductance. Keep f_r = 1/(2*pi*sqrt(LC)) above operating frequencies.

#### S2.5 Grounding Myths (Ott's List)

1. Earth is NOT a low-impedance path (seldom < few ohms, NEC allows up to 25 ohm)
2. Earth is NOT an equipotential
3. Conductor impedance is NOT just resistance (inductance dominates at HF)
4. Circuits do NOT need earth ground to operate (laptops, satellites, cars work fine)
5. Separate "quiet ground" rods do NOT reduce noise (and are dangerous -- violate NEC)
6. Current flows in LOOPS, not just "into" ground
7. Isolated receptacles ARE grounded (just grounded differently)

#### S2.6 PCB Ground-to-Chassis Connection

Connect circuit ground to chassis at the I/O connector area. This minimizes common-mode
voltage driving cables as antennas. Use multiple low-inductance connections. If metallic
backshell connectors are used, make 360-degree contact to enclosure (via EMC gasket).

---

### S3. Balancing and Power Supply Decoupling (Ch4)

#### S3.1 CMRR of Balanced Circuits

For a balanced circuit with source resistance R_S and load resistance R_L:
```
CMRR = 20*log(R_L / (delta_R_L * R_S))    [dB]
```

where delta_R_L is the unbalance in the load.

**Differential amplifier CMRR** (limited by resistor matching):
```
CMRR = 20*log(1/(2*p))    [dB]
```
where p is resistor tolerance (numeric, not percentage). With 0.1% resistors: CMRR = 54 dB.

**Instrumentation amplifier CMRR:**
```
CMRR = 20*log(A_dm / (2*p))    [dB]
```
With gain of 100 and 0.1% resistors: CMRR = 94 dB (40 dB better than differential amp).

#### S3.2 Common-Mode Filter Design

CM filters suppress noise on cables while passing the differential-mode signal.

**Key challenge:** Source impedance (PCB ground, low and inductive) and load impedance
(cable as antenna, high except at resonance) are usually unknown.

**Filter topology selection:**
- Source low, load high (typical CM case): Use L-filter with series element facing source,
  shunt capacitor facing load (cable)
- Series element effective near cable resonance (~35-70 ohm)
- Shunt capacitor effective above cable resonance (high impedance)
- More stages = less dependence on terminating impedances

**Series element choice:**
- Resistor: if DC voltage drop is acceptable
- Inductor: if DC drop not acceptable, effective below 10-30 MHz
- Ferrite: effective above 10 MHz, common-mode choke treats all conductors with one component

**Shunt capacitor connection:** Connect to chassis/enclosure ground (not signal ground,
unless they are tied together in the I/O area).

**Parasitic limits:** Above some frequency, every low-pass filter becomes a high-pass filter
due to parasitic capacitance across series element and parasitic inductance of shunt capacitor.
Layout determines this transition frequency (can be 10s of MHz with poor layout, 100s of MHz
with good layout).

#### S3.3 Power Supply Decoupling Strategy

Decoupling capacitor selection for analog circuits:

**Low-frequency analog decoupling (< 1 MHz):**
- Use electrolytic + ceramic in parallel
- Size electrolytic for energy storage: C >= delta_I * delta_t / delta_V
- Place ceramic close to IC for HF bypass

**Amplifier decoupling:** Place capacitor between supply pin and ground pin, not between
supply pin and distant ground.

---

### S4. Passive Component Parasitics for EMI (Ch5)

#### S4.1 Capacitor Parasitic Model

```
Z_cap = R_ESR + j*(omega*L_ESL - 1/(omega*C))
```

Self-resonant frequency: f_r = 1/(2*pi*sqrt(L_ESL*C)). Below f_r: capacitive. Above f_r: inductive.

| Capacitor Type | Useful Frequency | Typical ESL | Notes |
|---|---|---|---|
| Aluminum electrolytic | < 25 kHz | High (large size) | 1+ ohm ESR, ESR rises at low temp |
| Solid tantalum | < few MHz | Moderate | Derate to 70% V_rated |
| Film/Mylar | < few MHz | Moderate | Band/dot end = outer foil, connect to ground |
| Mica/ceramic | < 500 MHz | Low | SMD versions to GHz range |
| MLCC (surface mount) | < GHz+ | 1-2 nH typical | Best HF capacitor; 0.01 uF SMD: f_r ~ 50 MHz |
| Feed-through | < GHz | Very low (no ground lead) | 3-terminal; lead inductance helps (forms T-filter) |

**Paralleling capacitors:** Resonance problems can occur between paralleled capacitors of
widely different values due to series/parallel resonance of capacitors and interconnect inductance.

#### S4.2 Inductor Parasitic Model

```
Z_ind = R_DC + j*omega*L, shunted by distributed capacitance C_d
```

Parallel self-resonance at f_r = 1/(2*pi*sqrt(L*C_d)). Above f_r: capacitive (impedance falls).

- Open magnetic core: most susceptible to external fields (and most radiating)
- Closed core (toroid): flux stays inside, much less radiation and susceptibility
- Shield inductors with copper/aluminum for E-fields, mu-metal for LF magnetic fields

#### S4.3 Conductor Inductance and Resistance

**Round conductor over ground plane:**
```
L = 0.005 * ln(4*h/d)    [uH/inch]    (h = height above plane, d = diameter)
```

**Flat conductor (PCB trace) over ground plane:**
```
L = 0.005 * ln(2*pi*h/w)    [uH/inch]    (w = trace width, h >> w)
```

**Skin depth:**
```
delta = 2.6 / sqrt(f * mu_r * sigma_r)    [inches]
```
Copper at 1 MHz: delta = 0.003 in. At 100 MHz: delta = 0.00026 in.

Flat rectangular conductors have LESS inductance and LESS AC resistance than equivalent
round conductors. Inductance depends logarithmically on diameter -- doubling diameter
barely changes inductance. Use flat straps, not round wire, for low-inductance connections.

#### S4.4 Ferrite Selection for EMI Suppression

Ferrites act as frequency-dependent resistors (high-frequency AC resistors with no DC loss).

```
|Z_ferrite| = sqrt(R^2 + (2*pi*f*L)^2)    (R and L both vary with frequency)
```

**Effective frequency ranges by material type (Fair-Rite designations):**

| Material | Frequency Range |
|---|---|
| Type 73 | 1-10 MHz |
| Type 43 | 10-300 MHz |
| Type 31 | 30-500 MHz |
| Type 61 | 200 MHz - 2 GHz |

**Design rules:**
- Use ferrites in the frequency range where impedance is primarily resistive
- Ferrite must add impedance > source + load impedance to be effective
- Most effective in low-impedance circuits (ferrite Z typically < few hundred ohms)
- Multiple turns: impedance increases as N^2, but parasitic capacitance increases too (limit to 2-3 turns)
- DC bias reduces ferrite impedance (check manufacturer curves)
- Ferrite on multiconductor cable = common-mode choke (one component treats all conductors)

---

### S5. Shielding Design (Ch6)

#### S5.1 Near Field vs Far Field

Transition at r = lambda/(2*pi). In near field, E/H ratio depends on source:
- High-voltage source (rod antenna): E/H > 377 ohm (electric field dominates)
- High-current source (loop antenna): E/H < 377 ohm (magnetic field dominates)
- Far field: E/H = 377 ohm (plane wave)

#### S5.2 Shield Material Properties

```
|Z_S| = 3.68e-7 * sqrt(mu_r/sigma_r) * sqrt(f)    [ohm, for any conductor]
```

| Material | sigma_r | mu_r | Comments |
|---|---|---|---|
| Copper | 1.00 | 1 | Best conductor, good reflection loss |
| Aluminum | 0.61 | 1 | Lighter, nearly as good as copper |
| Steel (SAE 1045) | 0.10 | 1000 | Much better absorption loss (high mu) |
| Mumetal | 0.03 | 25,000 | Best for LF magnetic shielding |

#### S5.3 Absorption Loss

```
A = 3.34 * t * sqrt(f * mu_r * sigma_r)    [dB]
```

where t is shield thickness in inches. One skin depth = 8.69 dB absorption.
Doubling thickness doubles absorption loss in dB.

**Practical values (0.02-in copper):**
- 1 kHz: negligible
- 1 MHz: 66 dB
- Steel gives more absorption than copper at any thickness

#### S5.4 Reflection Loss

**Plane wave (far field):**
```
R = 168 + 10*log(sigma_r / (mu_r * f))    [dB]
```

**Electric field (near field, high-impedance source):**
```
R_E = 322 + 10*log(sigma_r / (mu_r * f^3 * r^2))    [dB]
```

**Magnetic field (near field, low-impedance source):**
```
R_H = 14.6 + 10*log(f * sigma_r / mu_r) + 10*log(r^2)    [dB]
```

where r is distance from source to shield in meters.

**Key insight:** Copper has MORE reflection loss than steel (higher conductivity), but steel
has MORE absorption loss (high permeability). For magnetic field shielding at low frequency,
steel or mu-metal is essential (copper provides almost no LF magnetic shielding).

#### S5.5 Aperture Effects (Usually Dominant at HF)

**Single aperture shielding effectiveness:**
```
SE = 20*log(lambda / (2*l))    [dB]
```

where l is the maximum linear dimension of the aperture. This limits SE at high frequencies
regardless of material thickness.

**Rule of thumb:** To achieve 20 dB shielding at frequency f, maximum aperture dimension:
```
l_max = lambda/20 = c / (20*f)
```

| Frequency | Max Aperture for 20 dB SE |
|---|---|
| 100 MHz | 15 cm (6 in) |
| 300 MHz | 5 cm (2 in) |
| 1 GHz | 1.5 cm (0.6 in) |

**Multiple identical apertures (N apertures):**
```
SE_reduction = 20*log(sqrt(N))    [dB penalty from single aperture SE]
```

**Seams:** Treat as slot antennas. For screws along a seam, the slot length = screw spacing.
Closer screw spacing = smaller slots = better shielding. Use contact fingers or gaskets for
best seam performance.

**Waveguide below cutoff (ventilation holes):**
```
SE_additional = 27.2 * (t/l)    [dB, rectangular hole]
SE_additional = 32 * (t/d)      [dB, round hole]
```

where t = depth (thickness) of hole, l = largest dimension. A honeycomb panel with 1/8-in
holes and 1/2-in depth gives t/d = 4, adding 128 dB.

#### S5.6 Gaskets and Seams

- Welded/brazed seams: maximum shielding
- Screws: space as close as possible (slot length = screw spacing)
- Conductive gaskets: require >100 psi contact pressure on mating surfaces
- Avoid dissimilar metals at gasket joints (galvanic corrosion)
- Gasket mounting: overlap surfaces preferred over butt joints
- Conductive coatings (paint, spray, plating) on plastic enclosures: 30-50 dB SE typical

---

### S6. Conducted Emissions in SMPS (Ch13)

#### S6.1 Common-Mode Emissions

The primary CM noise source is parasitic capacitance from the switching node to ground.

**CM equivalent circuit:** High-impedance voltage source (VP through CP) driving 25-ohm LISN.

Three main CM capacitance contributors:
1. **Switching transistor to heatsink** (usually largest)
2. **Transformer interwinding capacitance**
3. **Primary-side wiring capacitance**

**CM emission envelope:**
```
V_CM = 100 * V_P * F_0 * C_P    [V, at fundamental]
```

where V_P = peak switching voltage (~160 V for 115 VAC input), F_0 = switching frequency,
C_P = total primary-side parasitic capacitance to ground. Flat from F_0 to 1/(pi*t_r), then
falls at 20 dB/decade.

**CM reduction techniques:**
- Faraday-shielded thermal washer between MOSFET and heatsink (shield to source terminal)
- Thicker ceramic washer (beryllium oxide)
- Float heatsink from ground (safety concern)
- Faraday shield in transformer between primary and secondary windings
- Minimize primary-side wiring capacitance through careful layout

**Slowing the rise time does NOT reduce peak CM emission** -- it only moves the breakpoint
to a lower frequency. The flat region amplitude is unchanged.

#### S6.2 Differential-Mode Emissions

DM noise is caused by parasitic ESL and ESR of the input ripple filter capacitor C_F.

**DM equivalent circuit:** Low-impedance current source (IP through C_F parasitics) driving
100-ohm LISN.

**DM emission envelope (ESL-limited):**
```
V_DM = 2 * F_0 * L_F * I_P    [V, at fundamental]
```

where L_F = ESL of ripple capacitor, I_P = peak switching current. Flat from F_0 to 1/(pi*t_r),
then falls at 20 dB/decade.

**DM emission envelope (with ESR):** Additional breakpoint at f = R_F/(2*pi*L_F). Below this
frequency, emission rises at 20 dB/decade. Keep R_F <= pi*10^6*L_F to place breakpoint
at <= 500 kHz (where limits are more relaxed).

**CM vs DM dominance criterion:**
```
If V_P > L_F*I_P / (50*C_P), then CM dominates
```
High-voltage low-current supplies: CM dominates. Low-voltage high-current supplies: DM dominates.

#### S6.3 Power-Line Filter Design

**Generic topology (Fig. 13-16):** Y-caps (C1, C2) + CM choke (L1) + X-cap (C3).

| Element | CM Filter Role | DM Filter Role |
|---|---|---|
| Y-caps (line-to-ground) | Main CM capacitance (2*C_Y effective) | Negligible (C_Y/2 effective) |
| CM choke L1 | Main CM inductance (2-10 mH typical) | Leakage inductance only (0.5-5% of L_CM) |
| X-cap (line-to-line) | None | Main DM capacitance (0.1-2 uF typical) |

**Y-capacitor limits (safety agency leakage requirements):**
- Consumer (UL): 0.5 mA leakage -> max C_Y = 0.01 uF at 115V
- Medical: as low as 10 uA -> no Y-caps allowed in some cases
- Must be safety-rated (Y1 or Y2 class)

**Design sequence:**
1. Design CM filter first: Start with maximum allowable Y-caps, choose CM choke for required attenuation
2. Design DM filter: Use leakage inductance of CM choke + X-cap for DM filtering
3. If more DM attenuation needed: add discrete DM inductors (200 uH typical, low-permeability core)

**CM filter impedance matching:**
- CM source: high impedance (small C_P) -> inductor faces low-impedance LISN (25 ohm)
- Capacitor faces high-impedance source

**DM filter impedance matching:**
- DM source: low impedance (large C_F) -> capacitor C3 faces high-impedance LISN (100 ohm)
- Leakage inductance faces low-impedance source

**Filter mounting is critical:**
- Locate filter at the point where power enters the enclosure
- Input and output wiring must be physically separated
- If input/output wiring runs parallel, filter is bypassed by mutual coupling
- Mount filter to chassis with low-impedance connection
- Grounding of the filter case is part of the CM current path

#### S6.4 Primary-to-Secondary CM Coupling

Parasitic capacitance between transformer primary and secondary windings couples CM
noise to the secondary (output) side. This is a major source of CM noise in isolated supplies.

**Mitigation:**
- Faraday shield between primary and secondary (connect to primary ground)
- Increase winding separation (trades off against transformer size)
- Use transformer with known low interwinding capacitance

#### S6.5 Frequency Dithering (Spread Spectrum Clocking)

Modulating the switching frequency spreads harmonic energy across a wider bandwidth,
reducing the peak amplitude at any single frequency.

```
Peak reduction ~ 20*log(delta_f / RBW)    [dB]
```

where delta_f is the frequency deviation and RBW is the measurement receiver bandwidth.
With 10% frequency deviation and 9 kHz RBW at 100 kHz fundamental: ~7 dB reduction.

**Caution:** Dithering does NOT reduce total energy, only peak spectral density. It is most
effective for meeting narrowband limits (e.g., FCC quasi-peak measurements). Some standards
(military) use broadband measurements where dithering provides no benefit.

#### S6.6 Rectifier Diode Noise

Reverse recovery of rectifier diodes produces sharp voltage spikes and HF ringing.

**Mitigation:**
- Use soft-recovery diodes (slower turn-off, less ringing)
- R-C snubber across diode
- Ferrite bead in series with each diode (most effective combined with snubber)
- SiC Schottky diodes: essentially zero reverse recovery

---

### S7. RF and Transient Immunity (Ch14)

#### S7.1 Audio Rectification (RF Susceptibility)

RF energy coupled to analog circuits is rectified by semiconductor junctions, producing
a DC offset or low-frequency interference. Most common problem with AM radio transmitters.

**Protection techniques:**
- Filter RF from cable at enclosure entry (CM filter with ferrite + capacitor)
- Shield enclosure (SE > 20 dB at problem frequency)
- Decouple all IC power pins for RF (100 pF to 1 nF ceramic close to pin)
- Add small capacitor (100-1000 pF) at op-amp inputs
- Add ferrite bead in series with signal inputs

**Rule of thumb:** Conductors > lambda/20 long act as efficient antennas:

| Frequency | lambda/20 |
|---|---|
| 30 MHz | 50 cm (1.6 ft) |
| 100 MHz | 15 cm (6 in) |
| 300 MHz | 5 cm (2 in) |
| 1 GHz | 1.5 cm (0.6 in) |

#### S7.2 High-Voltage Transient Characteristics

| Transient | Voltage | Rise Time | Pulse Width | Energy | Source Impedance |
|---|---|---|---|---|---|
| ESD | 4-8 kV | ~1 ns | 60 ns | 1-10s mJ | 330 ohm |
| EFT (single) | 0.5-2 kV | 5 ns | 50 ns | ~4 mJ | 50 ohm |
| EFT (burst) | 0.5-2 kV | n/a | 15 ms | 100s mJ | 50 ohm |
| Surge | 0.5-2 kV | 1.25 us | 50 us | 10-80 J | 2 ohm |

ESD and EFT have similar rise times and energy (nanosecond, millijoules) -- treat similarly.
Surge has 1000x more energy (joules) and microsecond rise time -- needs different approach.

#### S7.3 Transient Suppression Design

**Three-pronged approach:**
1. Divert the transient current (away from sensitive circuits)
2. Protect sensitive devices (clamp voltage, limit current)
3. Write transient-hardened software

**General suppression network:** Series element (R, L, or ferrite) + shunt element (TVS, MOV, gas tube).

**Series element** limits current through shunt device and reduces voltage at protected circuit.
Must exist somewhere in circuit (could be source impedance, wiring, or discrete component).

**Signal line protection:**

For signal lines, use combinations of:
- Series ferrite bead or resistor (limits di/dt)
- Shunt TVS diode (clamps voltage) -- bidirectional for AC, unidirectional for DC
- Shunt capacitor (absorbs fast transients, must not distort signal)

**High-speed signal protection:** TVS diodes with low capacitance (<< 1 pF) are available
for high-speed data lines. Placement: as close to connector as possible.

**Power line surge protection (3-stage):**
1. Gas tube or large MOV at service entrance (handles bulk energy)
2. MOV at equipment power entry (clamps to safe level)
3. TVS or zener at sensitive circuit (precise clamping)

Series impedance between stages (wire length, inductor, or ferrite) allows coordination.

---

### S8. Electrostatic Discharge Protection (Ch15)

#### S8.1 ESD Fundamentals

**Human body model:** 150 pF capacitance, 330 ohm series resistance.
Peak discharge current for 8 kV: I_peak = 8000/330 = 24.2 A.

**ESD spectral content:** Predominantly 100-500 MHz. Rise time ~1 ns.

**Static voltage generation:**
- Walking on carpet: 1.5-35 kV (depends on humidity)
- Walking on vinyl floor: 0.25-12 kV
- Vinyl envelope for work instructions: 600-7000 V
- Common chair with polyester fabric: 18 kV

**Charge decay:** In humid environments (>65% RH), charge bleeds off quickly.
In dry environments (<20% RH), charge persists.

#### S8.2 Three-Prong ESD Protection Strategy

**1. Prevent entry of discharge:**
- Metallic enclosure: bond all seams, gasket all apertures, max aperture < 2.5 cm (1 in)
- I/O cables: filter or shield at enclosure entry point
- Plastic enclosure: use internal "ESD ground plate" (conductive plate behind plastic)
- Keyboards/control panels: conductive coating on inside of keys, grounded to chassis

**2. Harden sensitive circuits:**
- Most vulnerable digital inputs: resets, interrupts, control lines
- Add transient filter on critical inputs: ferrite + capacitor (connect cap to device ground,
  not chassis ground)
- Typical filter: 100 ohm ferrite + 100 pF ceramic = 500 MHz low-pass

**3. Transient-hardened software:**
- Watchdog timer: resets processor if locked in infinite loop
- Software input filtering: read input N times, accept only when readings agree (N=2-3)
- Parity/CRC on stored data
- Trap unused interrupt vectors (jump to error handler)
- Fill unused program memory with NOP + jump to error handler

#### S8.3 ESD Protection for Cables

All cables must be treated at the enclosure boundary:
- Shield: bond to enclosure with 360-degree termination
- Unshielded: ferrite core on cable at enclosure entry + filter capacitors to chassis
- USB/HDMI/Ethernet: use connectors with integrated shield contact to chassis

#### S8.4 Insulated (Plastic) Enclosures

- Place conductive plate (ESD ground plate) behind all user-accessible surfaces
- Ground plate must be connected to circuit ground
- Plate intercepts discharge and routes it to ground before reaching circuitry
- Conductive coating on inside of plastic case can serve same function
- Keep PCB components 2.5 cm from any unshielded aperture

---

## EMC for Product Designers -- Practical Compliance (from Tim Williams, 4th ed 2007)

> Source: Tim Williams, "EMC for Product Designers", 4th Edition, Newnes 2007 (503 pages)
> Focus: Product-level EMC compliance -- CE marking, immunity testing, test planning, and
> practical design-for-compliance techniques not covered in depth by Ott or Ozenbaugh.

### W1. CE Marking and the EMC Directive (2004/108/EC)

#### W1.1 Essential Requirements

The EMC Directive requires that all apparatus placed on the EU/EEA market satisfies two
essential requirements:

1. **Emissions**: EM disturbance generated shall not exceed a level above which radio,
   telecom, and other equipment cannot operate as intended
2. **Immunity**: apparatus shall have a level of immunity to expected EM disturbance that
   allows operation "without unacceptable degradation of its intended use"

**Key compliance facts:**
- Applies to every individual item, not just the product type -- no grandfather clause
- Components (ICs, resistors) are excluded; finished appliances are not
- Plug-in cards for PCs need their own CE mark (tested in a representative host)
- Battery-operated devices are covered (any electricity-powered device)
- Second-hand goods within EEA are excluded; imports of used goods are not
- "Benign" equipment (simple switches, fuses, filament lamps) may be excluded if both
  inherently non-emissive AND inherently immune

#### W1.2 Routes to Compliance

The 2nd edition Directive (2004/108/EC) replaced the old TCF route with a simpler framework:

1. **EMC Assessment** -- mandatory for all products. The manufacturer must perform and
   document an electromagnetic compatibility assessment
2. **Harmonised Standards** -- applying all relevant harmonised standards correctly is
   "equivalent to" carrying out the EMC assessment
3. **Partial Standards** -- if you don't apply all standards fully, you must still perform
   the assessment and document it; no third-party review required
4. **Notified Bodies** -- voluntary use only; they review your assessment if you choose

**Practical interpretation:**
- Pre-compliance testing is still viable -- document partial application of standards with
  engineering justification in the technical documentation
- The manufacturer assesses the risk of not fully following harmonised standards vs. the
  cost of doing so
- Testing to harmonised standards and demonstrating compliance by the methods described
  in those standards is the strongest position

#### W1.3 Declaration of Conformity (DoC)

The DoC must include:
- Reference to the EMC Directive (2004/108/EC)
- Description of the apparatus (type, batch, serial number)
- Name and address of manufacturer
- Dated references to specifications/standards used (with edition dates)
- Date of the declaration
- Identification of the empowered signatory

**Practical notes:**
- A single DoC can cover all applicable Directives (EMC + LVD + others)
- Technical documentation must be kept for 10 years from last date of manufacture
- Product modifications require re-assessment: determine if the change affects EMC
  performance; if so, re-test and re-issue the DoC
- The CE mark must be at least 5 mm high, "visibly, legibly and indelibly" affixed

#### W1.4 Production Quality

The CISPR 80/80 rule: at least 80% of series production must comply with limits at 80%
confidence level. Practically this means targeting about 95% compliance.

**Sampling schemes (CISPR):**

| Sample size n | Max failures c |
|---------------|---------------|
| 7             | 0             |
| 14            | 1             |
| 20            | 2             |
| 26            | 3             |
| 32            | 4             |

A single-unit test is allowed but should be followed by periodic random sampling from
production. Design margin of 6 dB below limits protects against production variation.

### W2. Standards Quick Reference

#### W2.1 Generic Emissions Standards

| Standard | Environment | Conducted Limits | Radiated Limits |
|----------|-------------|-----------------|----------------|
| EN 61000-6-3 | Residential/commercial | EN 55022 Class B | EN 55022 Class B |
| EN 61000-6-4 | Industrial | EN 55011 Class A | EN 55011 Class A |

Generic standards apply only when no dedicated product standard exists.

#### W2.2 Key Product Emissions Standards

| Standard | Scope | Key Tests |
|----------|-------|-----------|
| EN 55011 (CISPR 11) | ISM equipment | Conducted 150 kHz-30 MHz + radiated 30-1000 MHz |
| EN 55014-1 (CISPR 14-1) | Household appliances, tools | Conducted + discontinuous (click) + absorbing clamp 30-300 MHz |
| EN 55022 (CISPR 22) | IT equipment | Conducted + radiated + telecom port conducted |
| EN 55015 | Lighting equipment | Conducted + radiated |

**Class A vs Class B:**
- Class B (residential): tighter limits, about 10 dB stricter than Class A
- Class A products sold for residential use MUST carry a warning notice
- Class A limits are NOT acceptable for residential products

#### W2.3 Generic Immunity Standards -- Test Levels

**EN 61000-6-1 (Residential/Commercial/Light Industry):**

| Test | Standard | Level |
|------|----------|-------|
| ESD (contact/air) | EN 61000-4-2 | 4 kV / 8 kV |
| Radiated RF | EN 61000-4-3 | 3 V/m, 80-1000 MHz |
| EFT/Burst (power) | EN 61000-4-4 | 1 kV |
| EFT/Burst (signal) | EN 61000-4-4 | 0.5 kV |
| Surge (L-E) | EN 61000-4-5 | 2 kV |
| Surge (L-L) | EN 61000-4-5 | 1 kV |
| Conducted RF | EN 61000-4-6 | 3 V rms, 150 kHz-80 MHz |
| Power freq magnetic | EN 61000-4-8 | 3 A/m |
| Voltage dips | EN 61000-4-11 | 0%, 40%, 70%, 80% of nominal |

**EN 61000-6-2 (Industrial):**

| Test | Standard | Level |
|------|----------|-------|
| ESD (contact/air) | EN 61000-4-2 | 4 kV / 8 kV |
| Radiated RF | EN 61000-4-3 | 10 V/m (3 V/m in broadcast bands) |
| EFT/Burst (power) | EN 61000-4-4 | 2 kV |
| EFT/Burst (signal) | EN 61000-4-4 | 1 kV |
| Surge (L-E AC power) | EN 61000-4-5 | 2 kV |
| Surge (L-L AC power) | EN 61000-4-5 | 1 kV |
| Surge (signal, DC) | EN 61000-4-5 | 0.5 kV |
| Conducted RF | EN 61000-4-6 | 10 V rms (3 V in broadcast bands) |
| Power freq magnetic | EN 61000-4-8 | 30 A/m |
| Voltage dips | EN 61000-4-11 | 0%, 40%, 70%, 80% of nominal |

#### W2.4 Immunity Performance Criteria

Test results must be classified per the standard:

| Criterion | Description | Typical Application |
|-----------|-------------|-------------------|
| A | Normal performance within spec during AND after test | ESD, EFT, surge (after), dips (after) |
| B | Temporary degradation, self-recoverable | Radiated RF, conducted RF (during test) |
| C | Temporary degradation, requires operator reset | Voltage dips (during test) |
| D | Hardware damage or data loss | Not acceptable for any test |

The product standard specifies which criterion applies to each test. Criterion B during
RF immunity means brief glitches are acceptable if the system recovers on its own.

### W3. Immunity Tests -- What They Are and How to Pass Them

#### W3.1 ESD (IEC 61000-4-2)

**The test:** At least 10 single discharges per polarity to pre-selected points accessible
to personnel. Contact discharge preferred; air discharge used where contact impossible.
Also 10 discharges to horizontal and vertical coupling planes (indirect discharge).

**How products fail ESD:**
- LCD display corruption (discharge to bezels, LEDs)
- Processor crash (discharge to keyboards, controls, exposed metalwork)
- ADC/interface IC destruction (discharge to exposed metal surfaces)
- Memory corruption (discharge to card slots -- CF, SD, USB)

**How to pass:**
1. **Prevent the discharge from happening**: clear plastic windows over LEDs, LCDs,
   and indicator areas; maintain 6 mm creepage path from accessible surfaces to
   internal conductors; 2 mm clearance from PCB edge traces to enclosure interior
2. **Divert discharge current away from circuits**: metal-to-metal bonding of all
   enclosure panels; ESD current takes the path of least inductance -- provide one
   that avoids the PCB
3. **Protect sensitive pins**: low-capacitance TVS diodes (<1 pF for USB/DVI/HDMI);
   steering diodes to rails for lower-speed interfaces; series resistors + parallel
   protection at ADC inputs
4. **Low-inductance ground**: circuit ground must remain stable during the event;
   couple to chassis via capacitors or direct bonds at multiple points
5. **Guard traces**: run an unconnected trace around exposed PCB edges, bonded to
   ground -- sacrificial path for discharge current

**For plastic enclosures:** no direct discharge occurs if no apertures provide air gap
or creepage paths to interior. Still protect against indirect discharge field effects.

#### W3.2 EFT/Burst (IEC 61000-4-4)

**The test:** Bursts of fast transients (5 ns rise / 50 ns duration) at 5 kHz or 100 kHz
repetition rate, 15 ms burst duration, 300 ms period. Applied to power supply
terminals and via capacitive coupling clamp to I/O cables.

**Why products fail:** The burst consists of many fast edges that can trigger false
clocking in digital circuits. The repetitive nature means that even low-probability
bit errors accumulate.

**How to pass:**
- Mains filter with good CM rejection; Y-caps on BOTH sides of CM choke
- Capacitive filtering at ALL I/O interfaces to chassis ground
- Ground all interfaces physically close together so transient currents take
  a short path through metalwork, not through PCB traces
- Ferrite chokes on I/O cables at enclosure entry
- Watchdog circuit on every microprocessor
- Avoid edge-triggered digital inputs; protect them if unavoidable

#### W3.3 Surge (IEC 61000-4-5)

**The test:** 1.2/50 us voltage waveform (open circuit) or 8/20 us current waveform
(short circuit). At least 5 positive and 5 negative surges, minimum 1 per minute.
Applied line-to-line (2 ohm source Z) and line-to-earth (12 ohm source Z) on power
ports. Signal ports: 42 ohm source Z, capacitively coupled.

**Severity levels:** 0.5, 1, 2, 4 kV. All lower levels must also be applied. Typical
residential requirement: 2 kV L-E, 1 kV L-L.

**How to pass:**
- MOVs or TVS diodes at mains input BEFORE the filter (to clamp voltage before
  it reaches filter components)
- Gas discharge tubes (GDTs) for high-energy surges on telecom/signal ports
- Surge energy is substantial: MOV must handle the energy without degradation
  over product lifetime
- For signal ports, combine GDT (high energy handling) with TVS (fast clamping)
  in a two-stage protection circuit with decoupling impedance between stages

#### W3.4 Radiated RF Immunity (IEC 61000-4-3)

**The test:** 80% AM modulated at 1 kHz, swept 80 MHz to 1 GHz (and above for
some standards). Field calibrated using substitution method in anechoic chamber.
Step size no more than 1% of preceding frequency, dwell time >= 0.5 s.

**Test levels:** 3 V/m (residential), 10 V/m (industrial), higher for military/automotive.

**Power amplifier sizing:** For 10 V/m at 1 m distance, 80% AM modulation adds
5.2 dB (3.3x power) over unmodulated requirement. In non-anechoic rooms, add
further 6 dB margin.

**How products fail:**
- Rectification of RF in op-amp inputs causes DC offset shifts
- RF picked up on cables couples into signal processing circuits
- Clock/data lines act as receiving antennas at their resonant frequencies

**How to pass:**
- Minimize analogue signal bandwidths (RC filter at every op-amp input)
- Maximize dynamic range of analogue signal paths
- Balance high-impedance analogue inputs
- Use ferrite chokes on all cables entering the enclosure
- Ground plane on PCB reduces loop area for RF pickup
- Stability check: wideband amplifiers may oscillate under RF stress
- Series ferrite chips in power supply lines create isolated segments

#### W3.5 Conducted RF Immunity (IEC 61000-4-6)

**The test:** RF voltage, 80% AM 1 kHz, swept 150 kHz to 80 MHz. Applied via
CDNs (coupling/decoupling networks) to cable ports. Alternative: EM-clamp or
current injection probe (not on supply lines).

**Test levels:** 3 V rms (residential), 10 V rms (industrial).

**How to pass:**
- Same techniques as for radiated RF, focused on cable-borne interference
- CM chokes at every cable interface
- Filter capacitors to chassis at cable entry points
- For analogue inputs: bandwidth limitation is the single most effective technique

#### W3.6 Voltage Dips and Interruptions (IEC 61000-4-11)

**The test:** Supply voltage reduced to 0%, 40%, 70%, or 80% of nominal for
durations of 0.5 to 250 cycles (10 ms to 5 s at 50 Hz).

**How products fail:**
- Processor brownout at intermediate voltages (40-70% dip)
- Loss of relay/contactor state
- Memory corruption during undervoltage
- Motor stall and restart problems

**How to pass:**
- Power supply holdup time must cover the specified interruption duration
- Brownout detection with controlled shutdown/restart sequence
- Relay circuits must have defined behavior during and after dips
- Software: save critical state to non-volatile memory before brownout

#### W3.7 Mains Harmonics (IEC 61000-3-2)

Applies to equipment with input current up to 16 A per phase.

**Classes and limits:**

| Class | Equipment Type | Limit Style |
|-------|---------------|-------------|
| A | Balanced 3-phase + everything not in B/C/D | Absolute mA limits on harmonics 2-40 |
| B | Portable tools, non-professional arc welders | Class A x 1.5 |
| C | Lighting (except dimmers) | % of fundamental current |
| D | PCs, monitors, TVs (< 600 W) | mA per watt |

**Exemptions (no limits apply):**
- Equipment rated <= 75 W (except lighting)
- Professional equipment > 1 kW total rated power
- Symmetrically controlled heaters <= 200 W
- Independent dimmers for incandescent lamps <= 1 kW

**How to comply:**
- Active PFC is the standard solution for Class D equipment > 75 W
- Passive PFC (valley-fill, LC filter) works for lower power
- For lighting: active PFC or specific harmonic reduction circuits

#### W3.8 Voltage Flicker (IEC 61000-3-3)

Limits voltage fluctuations caused by equipment switching (motor start, heater cycling,
etc.). Key parameter: short-term flicker severity Pst.

**Practical implication:** Places a limit on allowable inrush current at switch-on.

### W4. The 20 MHz Conducted Emissions Peak

A near-universal problem with SMPS products: a conducted emissions peak in the
5-25 MHz range that is difficult to eliminate with conventional filtering.

#### W4.1 Root Cause

The peak arises from resonances in the overall test circuit:

```
VN (CM noise) --> CCM (coupling cap) --> CY (Y-cap) --> LCM (CM choke) --> 
  CW (choke winding cap) --> LCBL (mains cable inductance) --> LISN
```

**Primary resonance:** CM choke winding self-capacitance (CW) resonating with mains
cable inductance (LCBL). This is affected by cable length and mutual coupling between
conductors.

**Secondary resonance:** Transformer leakage inductance (LLKG) resonating with
interwinding capacitance (CCM), typically in the 5-10 MHz range. This is essentially
fixed by the transformer design and impossible to shift without a complete redesign.

#### W4.2 Fixes

1. **Add subsidiary Y-cap (100-470 pF) on the mains cable side of the CM choke** --
   lowers the resonant frequency and reduces its amplitude (the single most effective fix)
2. **Add a small extra CM choke before CY** -- addresses the transformer leakage
   inductance resonance when CY cannot be increased
3. **Deliberately increase transformer leakage inductance** -- may reduce the coupling
   peak amplitude (undesirable for other reasons but sometimes necessary)
4. **Check mains cable length** -- the resonance frequency depends on cable inductance;
   shorter cables shift the peak higher and may move it out of the measurement range

**Why conventional filtering fails:** The resonance bypasses the main filter through
parasitic capacitances. Adding more filter stages may not help if the bypass path
remains. The fix must address the specific resonant circuit.

### W5. Test Planning for Product Compliance

#### W5.1 What to Include in an EMC Test Plan

Prepare the test plan at the start of the project. It must cover:

1. **Product definition:** Description, variants, operating modes, interfaces, cable types
2. **Applicable standards:** Which emissions and immunity standards apply; which test
   levels; which performance criteria
3. **Test configuration:** Operating mode for worst-case emissions; exercise all
   functions during immunity tests
4. **Support equipment:** Simulators for sensors, loads, communication partners; these
   must not contribute to emissions or be susceptible themselves
5. **Performance monitoring:** How to verify the EUT is operating correctly during
   immunity tests (CCTV, remote monitoring, automated data logging)
6. **Test sequence:** Emissions first (non-destructive), then immunity (potentially
   destructive); ESD and surge last

#### W5.2 Operating Mode Selection

**For emissions:**
- The EUT must be exercised in the mode that produces maximum emissions
- For IT equipment: access disk drives, run CPU-intensive tasks, exercise all I/O
- For motor-driven equipment: test at maximum load/speed
- For SMPS: test at maximum input voltage AND maximum load (worst case for
  conducted emissions is often Vin_max)

**For immunity:**
- Monitor ALL functions simultaneously -- an error in any function constitutes a failure
- Use automated monitoring where possible; manual observation is unreliable for
  transient effects during RF sweep
- Run worst-case functional scenario: the mode most susceptible to interference

#### W5.3 Pre-Compliance vs. Full Compliance Testing

**Pre-compliance advantages:**
- Catch problems early when fixes are cheap (PCB respin vs. add-on ferrites)
- Can be done in-house with modest equipment
- Spectrum analyzer + LISN is sufficient for conducted emissions
- Near-field probes identify noise sources on the PCB before enclosure is ready

**Pre-compliance limitations:**
- Ambient noise floor is higher than in a shielded room
- LISN quality and ground plane quality affect accuracy
- Cannot replace full compliance testing for the Declaration of Conformity
- Under the 2nd EMCD, referencing harmonised standards requires demonstrating
  compliance by the methods described in those standards

**Practical approach:**
- Target 6-10 dB margin below limits in pre-compliance to account for measurement
  uncertainty and production spread
- Use a proper LISN even for development testing -- never measure directly on AC lines
- Near-field probes (H-field loops, E-field tips) are invaluable for identifying which
  component or trace is the noise source

#### W5.4 Using an External Test Lab

- Provide full test plan in advance (saves expensive lab time)
- Bring spare components, ferrites, capacitors, cable ties, copper tape for on-the-spot fixes
- Attend the test session -- the engineer's judgment during testing is invaluable
- If a failure is found, diagnose it immediately while the setup is still configured
- Agree in advance on which tests can be truncated and which need full sweeps
- Budget for at least one re-test session (first-time pass is rare for new designs)

### W6. Practical EMC Fixes -- The Product Designer's Toolkit

#### W6.1 The Wall-Wart Problem

External DC power supplies ("wall-warts") are a frequent source of conducted
emissions failures, even when the wall-wart itself meets EN 55022.

**Root cause:** The wall-wart's CM noise source (VO at its DC output) couples through
the product and out through signal ports (LAN, USB, etc.) to the measurement point.
The product is merely a conduit for the wall-wart's noise.

**Diagnostic:** Put a 1 uF cap across DC + and - at the product's input. If it makes NO
difference to emissions, the problem is common mode from the wall-wart, not
differential mode from your circuit.

**Fixes (in order of effectiveness):**
1. **Specify wall-wart properly**: require EN 55022 Class B on BOTH mains input AND
   DC output CM voltage/current, with output terminated in rated load AND 150 ohm
   CM impedance to ground plane
2. **CM choke at DC input**: impedance must exceed (ZO' + 150) ohms at the problem
   frequency for 6 dB improvement; small SMD CM chokes can give >1 kohm
3. **Add connections to other ports**: each additional port with low CM impedance to
   ground diverts current away from the measured port (6 dB per additional port of
   equal impedance)
4. **Reduce product CM impedance to ground**: if the product has an earth point,
   connect it directly to the ground plane

#### W6.2 The Dipole Problem -- Two-Halved Enclosures

Products where the enclosure is two conductive halves (top and bottom) connected only
by a wire form an effective radiating dipole antenna.

**Symptoms:** Radiated emissions increase dramatically when internal high-speed buses
are active (CF card, USB, etc.).

**Root cause:** Noise on internal cables couples capacitively to the conductive coating on
one half; the wire connecting the halves has enough inductance to let the two halves
radiate as a vertical dipole.

**Fixes:**
- **Remove the top-half conductive coating entirely** (counterintuitive but effective --
  eliminates the dipole radiator and reduces cost)
- **Shield the internal cables instead** (metallized sheath bonded to ground plane at
  one end and to the device case at the other)
- **OR bond the two halves properly** with conductive fasteners and gaskets at multiple
  points (more expensive but preserves shielding)

#### W6.3 LCD Display Emissions

LCD displays radiate emissions dominated by the pixel clock and its harmonics through
two paths: direct radiation from the glass face, and common mode radiation from the
whole assembly.

**Mitigation:**
- Evaluate LCD panels from multiple manufacturers -- face radiation varies significantly
- Choose clock frequencies whose harmonics avoid critical receiver bands
- Minimize separation distance between LCD module and the shielded enclosure
- Ground the LCD case at multiple points to the shield using conductive gasket
- Use flexi cable with ground plane (not wire bundles) for the LCD connection
- Ground the flexi's ground plane to the PCB 0V, which is locally bonded to the shield

#### W6.4 Improving RF Immunity of Analogue Sensors

For products with remote analogue sensors connected by unscreened wires that must
withstand high RF immunity levels (e.g., 20 V/m for marine applications):

**Approach (in order of effort):**
1. Upgrade PCB to 4 layers with ground plane
2. Add filter capacitors to ground plane on ALL terminal pins
3. Add bandwidth limitation capacitors at op-amp inputs
4. Add SMD spring fingers around PCB edge for contact with conductive enclosure coating
5. Spray interior of plastic enclosure with conductive paint (nickel or copper)

**Key finding (Williams case study):** Steps 1-4 alone (PCB-level changes without
conductive coating) were sufficient to achieve 20 V/m immunity. The shielding was
overkill -- good PCB design was the dominant factor.

### W7. EMC Design Checklist (from Williams Appendix A)

Use this checklist as a design review gate before committing to PCB layout or
production tooling.

#### W7.1 System Partitioning
- [ ] Partition into critical (noisy/susceptible) and non-critical sections
- [ ] Lay out noisy and sensitive circuits in separate areas
- [ ] Select interface locations for optimum CM current control

#### W7.2 Component and Circuit Selection
- [ ] Use slowest logic family that meets timing requirements
- [ ] Apply slew rate limiting to data transmission interfaces
- [ ] Series R buffering on ALL high-speed clock and data lines
- [ ] Good power decoupling: small, low-inductance caps adjacent to each IC
- [ ] Series ferrite chips in supplies to create power segments
- [ ] Reduce fan-out on clock circuits with buffers
- [ ] Minimize analogue signal bandwidths
- [ ] Maximize dynamic range of analogue signal paths
- [ ] Check stability in wideband amplifiers (can oscillate under RF)
- [ ] Unused IC input pins: tie to 0V or VCC (NEVER leave floating)
- [ ] Resistive, ferrite, or capacitive filtering at all sensitive analogue inputs
- [ ] Watchdog circuit on every microprocessor
- [ ] Avoid edge-triggered digital inputs; protect if unavoidable

#### W7.3 Cables
- [ ] Segregate signal and power cables; avoid parallel runs
- [ ] Use RF-screened cables when signal cannot be properly filtered
- [ ] Screen connected at BOTH ends (360-degree bond); if single-end only,
      treat as unscreened at RF
- [ ] Twisted pair for balanced or high-di/dt lines
- [ ] Properly designed looms, ribbon, or flexi for internal wiring (no loose bundles)
- [ ] Cables routed away from shielding apertures, tied close to grounded structures
- [ ] Ferrite suppressors to damp resonances and control CM currents
- [ ] Cable screens terminated to connector backshell (no pigtails)
- [ ] Transmission line impedance termination for high-frequency signals

#### W7.4 Grounding
- [ ] Ground system designed at product definition stage (not afterthought)
- [ ] Ground viewed as return current path, not just 0V reference
- [ ] Metal-to-metal bonding of screens, connectors, filters, enclosure panels
- [ ] Bonding methods specified to survive the operational environment
- [ ] Paint masked from contact surfaces; conductive finish applied
- [ ] Earth straps short, geometry defined
- [ ] No common ground impedances between different circuits
- [ ] Interface ground area provided for decoupling and filtering

#### W7.5 Filters
- [ ] Assume a supply filter IS needed -- design it, don't add it later
- [ ] Filter ALL I/O lines (3-terminal caps to interface ground AND/OR CM chokes)
- [ ] Pi filters at DC power input to each board in multi-board designs
- [ ] Defined ground return for each filter
- [ ] Filter interference sources (switches, motors) directly at their terminals
- [ ] Filter components located adjacent to the interface being filtered

#### W7.6 Shielding
- [ ] All metallic structures treated as electrical components (account for stray C and L)
- [ ] Segregated enclosures for particularly noisy or sensitive areas
- [ ] No large or resonant apertures in shields
- [ ] No dipole-like structures in metallic enclosures
- [ ] Panel seams bonded with conductive gaskets
- [ ] Plastic enclosures designed to accept internal conductive coating if needed
- [ ] DC or RF tie points defined between circuit 0V and shield
- [ ] Multiple internal tie-points to minimize box resonances

#### W7.7 Process
- [ ] Test and evaluate for EMC continuously as the design progresses
- [ ] EMC test plan written at project start, revised with test experience

### W8. Ferrite Selection Guide for Compliance Fixes

Ferrites are the most common retro-fit EMC component. Williams provides key
selection and application rules:

#### W8.1 Selection Rules of Thumb
- **Longer is better than fatter** for a given volume of ferrite material
- **Snug fit** on the cable maximizes impedance (minimize air gap)
- **A string of sleeves** increases impedance proportionally to length
- Clip-on ferrites: maximum impedance rarely exceeds 200-300 ohms
- **Expected attenuation on an open cable:** 6-10 dB typical, up to 20 dB at
  frequencies where cable shows low impedance

#### W8.2 Turns and Frequency
- Multiple turns increase low-frequency impedance (proportional to N^2)
- BUT inter-turn capacitance reduces self-resonant frequency
- Net effect: multiple turns shift peak attenuation DOWNWARD in frequency
- Use multiple turns only when targeting lower frequencies; single pass for broadband

#### W8.3 Placement for Maximum Effect
- Place ferrite adjacent to a capacitive filter or ground connection (low source Z
  gives maximum attenuation from the impedance divider)
- For open cables with unknown CM impedance, assume 150 ohm average
- If ferrite is placed flat against a grounded chassis, the ferrite's high permittivity
  creates a distributed capacitor -- forming an L-C filter with better performance
  than free-space mounting

#### W8.4 Attenuation Example

For circuit impedances ZS = ZL = R:

```
Attenuation = 20*log10(2R / (2R + Zferrite))   [dB]
```

| Zferrite | R = 10 ohm | R = 150 ohm |
|----------|-----------|-------------|
| 100 ohm  | -15.6 dB  | -2.5 dB     |
| 300 ohm  | -20.8 dB  | -4.1 dB     |
| 1000 ohm | -27.6 dB  | -7.4 dB     |

**Key insight:** Ferrites are far more effective in low-impedance circuits. In high-impedance
circuits (>150 ohm), ferrites alone give marginal improvement -- combine with capacitive
filtering for useful attenuation.

### W9. Mains Filter Application Rules

#### W9.1 Component Layout (Critical)

Two common faults that destroy filter performance at HF:

1. **Poor ground connection**: ground wire inductance rises with frequency, creating a
   common impedance that couples HF interference around the filter. **Fix:** directly
   bond filter ground terminal to lowest-inductance chassis ground.
2. **Input/output lead coupling**: mutual capacitance or inductance between unfiltered
   input leads and filtered output leads bypasses the filter. **Fix:** keep I/O leads
   physically separated, preferably screened from each other.

**Best practice:** Position the filter so it straddles the equipment shielding -- input
connections on the outside, output connections on the inside.

#### W9.2 Multi-Stage Filter Layout

Within the filter itself:
- Input and output components well separated for minimum coupling capacitance
- All tracks (especially ground) short and substantial
- Lay out components exactly as drawn on the circuit diagram
- Inductive components positioned/oriented to minimize magnetic coupling (use toroids)
- Electric field screens between stages if separation is insufficient

#### W9.3 CM Choke Winding for Minimum Self-Capacitance

Self-capacitance limits HF performance (typical filter limited to 40-50 dB by single-choke
self-resonance).

- Start and finish of winding widely separated
- Multi-section bobbin winding preferred
- Single-layer winding has lowest self-capacitance
- If multiple layers needed: progressive winding (not layer winding) reduces
  end-to-end capacitance
- Use a bobbin on high-permeability cores (don't wind directly on core -- high
  dielectric constant increases capacitance)

### W10. Systems and Installation EMC

#### W10.1 Earthing Hierarchy

Four distinct earth purposes in systems:

| Purpose | Function | Frequency | Conductor Requirement |
|---------|----------|-----------|----------------------|
| Safety | Prevent shock under fault | 50/60 Hz | Sized for fault current |
| Functional | Voltage reference between equipment | DC to kHz | Low resistance |
| Lightning | Return strike current to earth | Wideband pulse | Very low impedance to earth |
| EMC | Minimize interfering voltages | kHz to GHz | Low impedance at all frequencies |

#### W10.2 Meshed Equipotential Bonding Network (MESH-BN)

The preferred earthing method for ANY installation:

- 3D mesh of ALL structural metalwork bonded together (plumbing, cable trays,
  I-beams, conduits, walkways, re-bars -- everything)
- Mesh size <= 3-4 m for general use; smaller for higher frequencies or currents
- Provides safety + functional + EMC earthing simultaneously
- Connection length to bonding network: < 0.5 m
- Avoid regular bonding structures (all elements resonate at same frequencies)
- Surround each segregated area with a bonding ring conductor (BRC)

**Why not single-point earthing?** Single-point (star) earth conductors present high
impedance at MHz frequencies, decoupling the system from earth rather than coupling
to it. Star systems also degenerate over time as systems are modified. The mesh provides
multiple low-impedance paths at all frequencies.

#### W10.3 Earth Conductor Rules

| Conductor Type | Impedance | Use |
|----------------|-----------|-----|
| Long wire | Bad (high L, resonances) | Never for EMC |
| Short wire | Better but limited | Acceptable below ~1 MHz |
| Braided strap (short, wide) | Good (low L, damped Q) | Preferred for EMC bonds |
| Plate / direct metal contact | Best (lowest impedance) | Ideal at all frequencies |

A 10 cm strap (9 mm wide, 2 mm thick) still has substantial impedance in the hundreds
of MHz but its resonances are pushed high enough to be negligible.

#### W10.4 Bonding Practice

- Safety bond (green-yellow wire) is NOT adequate for EMC bonding
- EMC bond requires surface-to-surface conductive contact at frequent intervals
- Insulating layers (paint, anodizing) must be removed
- Conductive finish required (zinc plating, chromate conversion)
- Positive pressure via fasteners; conductive gaskets between fasteners
- Bond must be protected from corrosion (gas-tight or overall coating)
- Bond DC resistance: < 2.5 milliohm for ESD protection

### W11. EMC Management for Product Development

#### W11.1 Integrating EMC into the Design Process

**Design phase actions:**

| Phase | EMC Activity |
|-------|-------------|
| Concept / Specs | Write EMC test plan; identify applicable standards; set immunity/emissions targets |
| Schematic | Apply circuit-level EMC rules (decoupling, filtering, slew rate control) |
| PCB Layout | Follow layout checklist (ground planes, loop area, separation) |
| Prototype | Pre-compliance testing on first prototype; iterate design |
| Pre-production | Full compliance testing at accredited lab; fix remaining issues |
| Production | Sample testing per CISPR 80/80 rule; maintain compliance documentation |
| Modifications | Re-assess EMC impact; re-test if change affects EMC performance |

**Cost of EMC fixes by phase:**
- At schematic/layout stage: minimal (pennies per unit)
- At prototype stage: moderate (PCB respin, component changes)
- At production stage: expensive (add-on ferrites, shielding, redesign)
- After product launch: very expensive (field recalls, redesign under time pressure)

#### W11.2 Common Management Mistakes

- Treating EMC as a test-and-fix problem rather than a design discipline
- Appointing an "EMC specialist" without giving them authority over design decisions
- Purchasing not consulting EMC requirements when sourcing components
- Production changing approved components or assembly methods without EMC review
- Launching a marginally compliant product hoping nobody will notice

#### W11.3 Wall-Wart / External PSU Procurement

Never accept a wall-wart solely because it has a CE mark. The CE mark is essentially
meaningless in a technical sense -- it only confirms the manufacturer's self-declaration.

**Specify explicitly:**
- Conducted emissions limits on BOTH mains input AND DC output
- CM output voltage/current limits at DC port
- Test conditions: rated load AND 150 ohm CM termination
- Require actual test reports, not just the declaration


## TI EMI Guide for DC-DC Converters (from How2Power)

Source: Timothy Hegarty (TI), "The Engineer's Guide To EMI In DC-DC Converters" Parts 12-18,
How2Power Today, 2020-2021. 17-part series covering all aspects of EMI in DC-DC converters.

### DM Conducted Noise Prediction (Part 12)

**Modeling the LISN and test receiver enables early EMI prediction before building hardware.**

#### DM Noise Model of Converter Input Current
- Buck converter: trapezoidal input current; Fourier coefficients are a double sinc function
  - Spectral envelope: 0 dB/dec up to f1 = 1/(pi*t1), then -20 dB/dec, then -40 dB/dec after f2 = 1/(pi*tR)
  - f1 set by pulse width, f2 by rise/fall time; higher di/dt pushes f2 higher
- Boost converter: triangular inductor current; spectral envelope rolls off at -40 dB/dec (1/n^2)
  - Fundamental and 2nd harmonic dominate; higher harmonics decrease rapidly
  - Fourier coefficients: cn = (iL_pp / (d*n*pi^2)) * sin(n*pi*d) * sin(n*pi*d) / (n*pi*d) with Vout/(Lb*fs) scaling

#### LISN Transfer Function
- CISPR 25: 5 uH LISN inductance (automotive, up to 108 MHz)
- CISPR 16: 50 uH LISN inductance (up to 30 MHz)
- Both converge to 47.6 ohm at high frequency (50 ohm || 1 kohm)
- CISPR 25 impedance surprisingly low below 1 MHz: ~4.8 ohm at 150 kHz
- Simplified DM model: two LISN measurement resistors in series = ~100 ohm

#### Test Receiver Model
- Superheterodyne architecture: mixer + IF filter + envelope detector + video filter
- IF filter: near-Gaussian shape, RBW = 9 kHz (Band B: 150 kHz-30 MHz), 120 kHz (Band C: 30-300 MHz)
- Detectors: V_peak >= V_quasi-peak >= V_average; equal when single harmonic in RBW
- Prediction flowchart: time-domain waveform -> FFT -> for each f_IF: IF filter output -> envelope detector -> PK/QPK/AVG

### CM Conducted Noise Prediction (Part 13)

**CM noise driven by dv/dt of switching waveforms, not di/dt like DM noise.**

#### CM Noise Source Model
- Switch-node voltage (trapezoidal) acts as CM voltage source
- Fourier coefficients identical to buck input current but with Vin*d scaling
- Spectral envelope: -20 dB/dec after f1, then -40 dB/dec after f2 = 1/(pi*tR)
- Higher dv/dt (faster edges) pushes more energy to higher frequencies

#### CM Current Propagation Paths
- Parasitic capacitance Csw from switch to GND (heatsink) -- DOMINANT below 20 MHz
- PCB trace-to-GND-plane capacitance Cpcb -- dominates above 20 MHz
- Power-lead-to-GND-plane capacitance Clead -- dominates above 100 MHz
- CM loop area much larger than DM loop area -> major radiated EMI source

#### CM Filter Design
- Impedance mismatch: high source impedance (capacitive), low load impedance
- Use CL (gamma) topology: Y-caps face converter, CM choke faces LISN
- CM choke + DM inductors (in parallel for CM) break against Y-capacitance
- Without chassis GND: Y-caps cannot be used; CM choke alone gives -20 dB/dec

#### DM-to-CM Mode Conversion
- Asymmetry in Y-capacitors or circuit layout causes mode transformation
- Part of CM noise converts to DM noise and vice versa
- Balanced circuit structure and symmetric layout are essential

### Behavioral EMI Modeling (Part 14)

**Frequency-domain behavioral models overcome limitations of time-domain lumped-circuit simulation.**

#### Three Approaches to EMI Modeling
1. **Lumped-element circuit models**: physics-based, replace semiconductors with models
   - Complex, slow, convergence issues, unusable for system-level EMI
2. **Two-terminal (one-port) decoupling models**: Norton equivalent (I1, Z1) for DM or CM separately
   - Simple but limited above 30 MHz due to mode transformation neglected
3. **Three-terminal (two-port) behavioral models**: captures mixed-mode noise
   - Five parameters: two current sources (I1, I2) + three impedances (Z1, Z2, Z3) in delta
   - Accurate DM and CM prediction including mode conversion
   - Works up to 100 MHz

#### Two-Terminal Model Parameter Extraction
- Two distinct terminal voltage measurements (nominal + attenuated with shunt impedance)
- Boundary conditions: A >= 10, |Z1| >= 0.1*|Zsource|
- FFT is critical step: conversion from time-domain measurements to frequency domain

#### Three-Terminal Model Parameter Extraction
- Method 1: Attach shunt attenuator impedances (nominal + 2 attenuated cases)
  - Seven possible attenuation schemes; any two + nominal solve for 5 unknowns
  - Boundary: 20 dB <= A <= 60 dB, source impedance within 10x of model impedance
  - For buck/buck-boost (discontinuous input current): include input capacitor to mask nonlinear behavior
- Method 2: Offline input impedance measurement (converter unpowered)
  - Measure 6 impedances (ZPG, ZNG, ZPN for each switching state)
  - State 1: short high-side switch; State 0: short low-side switch
  - Calculate Z1, Z2, Z3 from measured impedances, then I1, I2 from terminal voltages

#### Key Insight
- Three-terminal models are compact, linear, frequency-domain -> fast, stable simulations
- Enable prediction of EMI for any input-side impedance change (filter optimization)
- Validated: close agreement with bench measurements for 50W buck converter from 100 kHz to 108 MHz

### Active and Hybrid EMI Filters (Part 17)

**Active EMI filters (AEF) can reduce passive filter volume by 50-75% with equivalent attenuation.**

#### AEF Circuit Configurations (6 topologies)
Classified by: sensing (voltage VS / current CS), injection (voltage VI / current CI), control (feedback FB / feedforward FF)

| Topology | Control | Sensing | Injection | Active Element |
|----------|---------|---------|-----------|----------------|
| FB-CSVI  | Feedback | Current | Voltage | Current-controlled voltage source |
| FB-CSCI  | Feedback | Current | Current | Current-controlled current source |
| FB-VSVI  | Feedback | Voltage | Voltage | Voltage-controlled voltage source |
| **FB-VSCI** | **Feedback** | **Voltage** | **Current** | **Voltage-controlled current source** |
| FF-VSVI  | Feedforward | Voltage | Voltage | Voltage-controlled voltage source |
| FF-CSCI  | Feedforward | Current | Current | Current-controlled current source |

**Preferred topology: FB-VSCI** -- uses capacitors only (no additional magnetics), low-voltage active circuits powered from 5V bias.

#### AEF Design Rules
- **Capacitive multiplier principle**: op amp gain Gop-AEF multiplies injection capacitor CINJ
  - Effective impedance: Zeq = Zinj / (1 + Gop-AEF)
- **Decoupling inductance required**: prevents converter input capacitance from loading op amp
  - Use discrete inductor, ferrite bead, or CM choke leakage inductance
- **LF*CIN product** sets ripple current amplitude in decoupling inductor:
  - LF*CIN = D*(1-D)*IOUT / (8*ILpk-pk*Fsw)
  - Ripple current must be within op amp sink/source capability (typ. ~45 mA)
- **Stability**: compensate at both low frequency (LC resonance) and high frequency (op amp parasitic poles)
  - Damping: RDAMP = sqrt(LF/(CSEN*CINJ)), CDAMP = CINJ/2

#### AEF Performance (TI LM25149-Q1 integrated AEF)
- 50 dB reduction at fundamental switching frequency (440 kHz)
- Effective attenuation up to ~5 MHz; passive components handle higher frequencies
- Filter footprint reduced by ~50%, volume reduced by >75%
- Meets CISPR 25 Class 5 (strictest automotive limit)
- Smaller passive inductor has higher SRF -> better high-frequency filtering

### Dual Random Spread Spectrum (Part 18)

**Advanced spread-spectrum combines periodic triangular + pseudo-random modulation for wideband EMI suppression.**

#### Spread Spectrum Fundamentals
- Fixed-frequency switching concentrates energy at harmonics
- Spread spectrum redistributes energy across wider bandwidth, reducing peaks
- Modulation depth: delta_fs / fs (typical +/-7.8% for automotive)
- Modulation index m = delta_fs / fm determines number of sidebands

#### Optimal Modulating Frequency
- Must match EMI receiver RBW for maximum attenuation
- CISPR 25 Band B (150 kHz - 30 MHz): RBW = 9 kHz -> optimal fm ~ 9 kHz
- CISPR 25 Band C (30 MHz - 108 MHz): RBW = 120 kHz -> optimal fm ~ 120 kHz
- Too low fm: frequency stays in RBW passband too long (appears unmodulated)
- Too high fm: low modulation index, too few sidebands, insufficient energy redistribution
- Minimum frequency slew rate: RBW^2 (81 MHz/s for 9 kHz; 14.4 GHz/s for 120 kHz)

#### Dual Random Spread Spectrum (DRSS)
- **Two-rate approach**: solves the inherent conflict between 9 kHz and 120 kHz optimal fm
  - Low-frequency triangular modulation (~9-14 kHz): optimized for Band B (150 kHz - 30 MHz)
  - High-frequency cycle-by-cycle random modulation: optimized for Band C (30 - 108 MHz)
  - Randomized triangular frequency eliminates audible modulation tones
- **Results (TI LM25148-Q1, 2.1 MHz fsw, automotive 13.5V)**:
  - Band B: 8-12 dB improvement
  - Band C: 5-7 dB improvement (where passive filtering is hardest)
  - Modulation index m = 12-18 (high, enabling effective energy spreading)
- Applicable to all topologies (buck, boost, flyback, SEPIC, etc.)
