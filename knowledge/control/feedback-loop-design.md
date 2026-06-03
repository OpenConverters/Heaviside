# Feedback Loop Design Guide

Sources:
- Basso, "Switch-Mode Power Supplies: SPICE Simulations and Practical Designs" (2014), Chapters 2-3, Appendix 2A
- Maniktala, "Switching Power Supplies A-Z" 2nd Ed (2012), Chapter 12

---

## 1. Plant Transfer Functions

### Notation (all sections)

| Symbol | Meaning |
|--------|---------|
| V_peak (V_RAMP) | Sawtooth amplitude for voltage-mode PWM modulator |
| r_Cf (ESR) | Output capacitor ESR |
| r_Lf | Inductor DC resistance |
| R | Load resistance |
| C | Output capacitance |
| L | Inductance (for boost/buck-boost: actual L, not canonical equivalent unless noted) |
| D | Duty ratio |
| D' | 1 - D |
| T_sw, F_sw | Switching period, frequency |
| R_i | Current sense resistance |
| S1 (S_n) | Inductor on-slope |
| S2 (S_f) | Inductor off-slope |
| S_a (S_e) | Artificial compensation ramp slope |
| m_c | Ridley ramp coefficient: m_c = 1 + S_a/S1 |
| N | Transformer turns ratio (N_s/N_p) |

### 1.1 Buck

#### Voltage-Mode CCM

Control-to-output (Basso Ref[1], Maniktala):

```
G(s) = (V_in / V_RAMP) * 1 / [(s/w0)^2 + s/(w0*Q) + 1]
```

where:
- w0 = 1/sqrt(L*C) — LC double-pole resonant frequency
- Q = R / (w0 * L) = R * sqrt(C/L) — quality factor
- f0 = w0/(2*pi)

With ESR zero included:

```
G(s) = (V_in / V_RAMP) * [(s/w_esr) + 1] / [(s/w0)^2 + s/(w0*Q) + 1]
```

where w_esr = 1/(ESR * C)

With inductor DCR (r_Lf), the Q is modified:
- Q = 1 / (w0 * (L/R + r_Lf*C + ESR*C))

Line-to-output:

```
G_line(s) = D / [(s/w0)^2 + s/(w0*Q) + 1]
```

#### Voltage-Mode DCM

Single-pole response (Basso App 2A, Ref[1]):

The DCM buck plant has a single dominant low-frequency pole (no LC double-pole resonance). The transfer function simplifies to a first-order system.

Key pole:
- f_p = 1 / (2*pi*R*C) — output pole, but modified by the DCM operating point

The DC gain is:
- G_0 = (2 * V_out) / (V_RAMP * (1 + M_DCM))

where M_DCM = V_out/V_in is the DCM conversion ratio (depends on K = 2*L/(R*T_sw)).

#### Current-Mode CCM

The CM plant eliminates the LC double-pole and replaces it with a single pole from the output RC, plus a complex pole pair at f_sw/2 (subharmonic pole).

Simplified (Ridley/Vorperian, Basso App 2A Ref[2]):

```
G_cm(s) = G_0 / [(1 + s/w_p) * (1 + s/(w_s*Q_s) + (s/w_s)^2)]
```

where:
- G_0 = R / (R_i * (1 + S_a/S1)) = R / (R_i * m_c) — DC gain
- w_p = 2 / (R*C) — output pole (factor of 2 arises from CM action)
- w_s = pi * F_sw = pi / T_sw — half-switching-frequency pole
- Q_s depends on slope compensation (see Section 5)

Maniktala simplified model (extra-simplified Middlebrook): ignores the f_sw/2 pole entirely when it is well-damped and above crossover:

```
G_cm(s) = G_0 / (1 + s/w_p)
```

where:
- G_0 = V_in / (V_RAMP * m_c) — for Buck (Maniktala Fig 12.25: A/B = V_in*R_i / V_RAMP, but mapped through transfer resistance)
- w_p = 1/(R*C) — simple output pole

#### Current-Mode DCM

Single-pole system (Basso App 2A Ref[2]):
- DC gain depends on operating point
- Dominant pole from output RC
- No subharmonic instability in DCM

#### Forward Converter

For a forward topology in voltage mode: all buck transfer functions apply with V_in replaced by N*V_in where N = N_s/N_p.

In current mode: additionally, the sense resistor must be scaled: R_i' = R_i * N.

For half-bridge variants: V_in may need to be divided by 2 depending on transformer connection.

---

### 1.2 Boost

#### Voltage-Mode CCM

Control-to-output (Maniktala, Basso App 2A):

```
G(s) = [V_in / (V_RAMP * (1-D)^2)] * [1 - s/w_rhpz] / [(s/w0)^2 + s/(w0*Q) + 1]
```

where:
- L_eq = L / (1-D)^2 — equivalent inductance in canonical model
- w0 = 1/sqrt(L_eq * C)
- Q = R * sqrt(C / L_eq)
- w_rhpz = R*(1-D)^2 / L — **right-half-plane zero** (see Section 2)

With ESR zero: add (1 + s/w_esr) factor to numerator, where w_esr = 1/(ESR*C).

Line-to-output:

```
G_line(s) = [1/(1-D)] / [(s/w0)^2 + s/(w0*Q) + 1]
```

#### Voltage-Mode DCM

Single-pole system (no LC double-pole). The RHPZ is still present but typically at higher frequency than in CCM.

#### Current-Mode CCM

```
G_cm(s) = G_0 * [1 - s/w_rhpz] / [(1 + s/w_p) * (1 + s/(w_s*Q_s) + (s/w_s)^2)]
```

where:
- G_0 = R / (R_i * m_c * (1-D)) — DC gain (Basso notation)
- w_p is the output pole
- w_rhpz = R*(1-D)^2 / L — RHPZ unchanged from VM
- The f_sw/2 pole pair with Q_s as in buck CM

Maniktala simplified (current-mode, all topologies — see Fig 12.25):
- Plant DC gain A/B for Boost: V_in / (V_RAMP * (1-D)^2) mapped through transfer resistance
- Output pole: f_p = 1/(2*pi*R*C)

#### Current-Mode DCM

Single-pole system. RHPZ still present but at higher frequency. No subharmonic instability.

---

### 1.3 Buck-Boost / Flyback

#### Voltage-Mode CCM

Control-to-output (Maniktala, Basso App 2A):

```
G(s) = [V_in / (V_RAMP * (1-D)^2)] * [1 - s/w_rhpz] / [(s/w0)^2 + s/(w0*Q) + 1]
```

where:
- L_eq = L / (1-D)^2
- w0 = 1/sqrt(L_eq * C)
- Q = R * sqrt(C / L_eq)
- w_rhpz = R*(1-D)^2 / (L*D) — note the extra D in denominator vs. boost

Line-to-output:

```
G_line(s) = [D/(1-D)] / [(s/w0)^2 + s/(w0*Q) + 1]
```

#### Voltage-Mode DCM

Single-pole system. RHPZ at higher frequency.

#### Current-Mode CCM

Same structure as boost CM, with RHPZ at:

```
w_rhpz = R*(1-D)^2 / (L*D)
```

DC gain and output pole modified for buck-boost topology.

#### Current-Mode DCM

Single-pole system. No subharmonic instability.

#### Flyback Adaptation

The buck-boost equations apply to flyback with adjustments (Basso App 2A p469):

**Method 1** (reflect secondary to primary):
- Keep L_p as the inductance parameter
- Keep R_i on primary side
- Reflect C and R to primary: C' = C*N^2, R' = R*N^2

**Method 2** (reflect primary to secondary):
- Calculate L_s = L_p * N^2, use as L
- Reflect sense resistor: R_i' = N * R_i
- Keep C and R at their original secondary-side values

---

## 2. Right Half-Plane Zero

The RHPZ occurs in boost and buck-boost (including flyback) topologies in CCM, for both voltage-mode and current-mode control. It boosts gain (+20 dB/dec) while lagging phase (-90 deg) — it cannot be compensated by conventional means.

### RHPZ Frequency

```
Boost:       f_rhpz = R*(1-D)^2 / (2*pi*L)
Buck-Boost:  f_rhpz = R*(1-D)^2 / (2*pi*L*D)
```

### Design Implications

1. **Crossover constraint**: f_c < f_rhpz / 3 (conservative: f_c < f_rhpz / 5 to f_rhpz / 10)
   - Basso recommends: f_c < 30% of worst-case lowest RHPZ position
2. **Worst case**: RHPZ moves to lowest frequency at:
   - Maximum D (minimum V_in)
   - Maximum L
   - Minimum R (maximum load)
3. **Cannot be canceled** by a compensator zero — adding an LHPZ at the same frequency only flattens gain, phase still lags
4. **Design mitigation**: Use smaller L (increases current ripple), limit maximum D, or accept lower bandwidth

### Intuitive Explanation (Maniktala)

In boost/buck-boost, energy reaches the output only during the off-time. Increasing duty cycle to raise output voltage actually reduces the off-time interval, momentarily reducing energy delivery to the output. The output initially dips further before the inductor current ramps up enough to compensate — this is the RHPZ in action.

---

## 3. Compensator Types

All compensators use an inverting op-amp configuration. The op-amp inversion adds 180 deg phase shift. Phase margin is measured as distance from the loop phase to 0 deg (or equivalently, to -180 deg depending on convention).

### 3.1 Type 1 -- Pure Integrator

**Circuit**: Capacitor C1 from op-amp output to inverting input; R1 (= R_upper of divider) as input resistor.

**Transfer function**:

```
G(s) = -1 / (s * R1 * C1)
```

- Single pole at origin (integrator)
- Gain crosses 0 dB at: f_0dB = 1/(2*pi*R1*C1)
- Permanent phase contribution: -90 deg (from origin pole)
- **No phase boost capability**
- k-factor = 1 (pole and zero coincident)

**Component equations**:

```
C1 = G / (2*pi*f_c*R1)
```

where G = |G_fc|^(-1) is the needed gain at crossover to compensate the plant attenuation.

**When to use**: Plant phase lag < 45 deg at crossover (e.g., single-pole plants, current-mode with ESR zero below crossover, PFC voltage loops).

---

### 3.2 Type 2 -- Origin Pole + One Zero-Pole Pair

**Circuit** (Basso Fig 3.20): R1 input, C1 in series with R2 from output to inverting input, C2 from output to inverting input (in parallel with R2+C1 series).

**Transfer function**:

```
G(s) = -(1 + s*R2*C1) / [s*C1*R1 * (1 + s*R2*C2)]
```

Assuming C2 << C1:

```
G(s) = -(1 + s/w_z) / [s/(w_p0) * (1 + s/w_p)]
```

**Pole and zero definitions**:

```
f_z  = 1 / (2*pi*R2*C1)         — zero
f_p  = 1 / (2*pi*R2*C2)         — high-frequency pole
f_p0 = 1 / (2*pi*R1*C1)         — 0-dB crossover of integrator
```

**Mid-band gain** (between f_z and f_p):

```
G_mid = R2 / R1
```

**Phase boost**: Maximum phase boost occurs at the geometric mean of f_z and f_p:

```
f_boost_max = sqrt(f_z * f_p)
```

Phase boost = arctan(k) - arctan(1/k), where k = f_p/f_z at the geometric mean.

**When to use**: Plant phase lag up to ~90 deg at crossover. Suitable for:
- Current-mode CCM converters (all topologies)
- Voltage-mode DCM converters
- Plants where ESR zero effect must be rolled off

---

### 3.3 Type 3 -- Origin Pole + Two Zero-Pole Pairs

**Circuit** (Basso Fig 3.26): R1 input, C1 in series with R2 (feedback path), C2 in parallel with that feedback path, R3 from non-inverting input (or from the op-amp input node) with C3 to ground (or to output, depending on topology).

**Transfer function** (Basso, with C2 << C1 and R3 << R1):

```
G(s) = -[(1 + s*R2*C1)(1 + s*(R1+R3)*C3)] / [s*R1*(C1+C2) * (1 + s*R2*(C1*C2/(C1+C2))) * (1 + s*R3*C3)]
```

Simplified:

```
G(s) ~ -[(1 + s/w_z1)(1 + s/w_z2)] / [(s/w_p0) * (1 + s/w_p1)(1 + s/w_p2)]
```

**Pole and zero definitions**:

```
f_z1 = 1 / (2*pi*R2*C1)             — first zero
f_z2 = 1 / (2*pi*(R1+R3)*C3)        — second zero (approx 1/(2*pi*R1*C3) if R3 << R1)
f_p0 = 1 / (2*pi*R1*(C1+C2))        — integrator crossover (approx 1/(2*pi*R1*C1))
f_p1 = 1 / (2*pi*R2*C2)             — first high-frequency pole
f_p2 = 1 / (2*pi*R3*C3)             — second high-frequency pole
```

**Phase boost**: Up to ~180 deg (theoretical). Practical designs achieve 90-150 deg.

**Component calculation from desired poles/zeros** (Maniktala):

```
C2 = 1/(2*pi*R1) * (1/f_z1 - 1/f_p1)
R2 = R1 * f_p0 / f_z2
C3 = 1 / (2*pi * (R2*f_p2 - R1*f_p0))
R3 = R1 * f_z1 / (f_p1 - f_z1)
```

(With C1 already determined from R1*C1 = 1/(2*pi*f_p0).)

**When to use**: Plant phase lag approaching 180 deg at crossover. Required for:
- Voltage-mode CCM buck (LC double-pole)
- Voltage-mode CCM boost/buck-boost (LC double-pole + RHPZ)

---

## 4. k-Factor Design Method

The k-factor method (Dean Venable, 1980s) automates pole-zero placement for Types 1, 2, and 3 compensators. It places the crossover frequency f_c at the geometric mean of pole and zero positions, maximizing phase boost at f_c.

### Step-by-Step Procedure (Basso Ch3)

**Step 1**: Obtain the power stage open-loop Bode plot (from measurement, averaged SPICE model, or analytical expressions from Section 1).

**Step 2**: Select crossover frequency f_c and target phase margin PM.
- f_c should be at least 5x above any LC resonance peaking (for VM CCM)
- f_c < f_rhpz/3 for boost/buck-boost topologies
- f_c < f_sw/5 to f_sw/10 (typical)
- Target PM: 45 deg minimum, 70 deg recommended for robust design

**Step 3**: Read from the Bode plot at f_c:
- G_fc: gain magnitude (in dB) at f_c
- PS: phase shift (negative number) at f_c

**Step 4**: Select compensator type based on phase lag:
- Phase lag < 90 deg at f_c: Type 1 or Type 2
- Phase lag 90-180 deg at f_c: Type 2
- Phase lag approaching 180 deg: Type 3

**Step 5**: Calculate k-factor and components.

### Type 1

k = 1 (always). The gain G needed at f_c:

```
G = 10^(-G_fc_dB / 20)
C1 = G / (2*pi*f_c*R1)
```

### Type 2

**Phase boost calculation**:

```
boost = PM - PS - 90
k = tan(boost/2 + 45 deg)     [angles in degrees]
```

More precisely:

```
k = tan((boost + 90) / 2)     [boost in degrees, convert to radians for computation]
```

Or equivalently from Basso Eq. (3.54):

```
k = tan(pi/4 + boost_rad/2)
```

**Gain**:

```
G = 10^(-G_fc_dB / 20)
```

**Component values** (labels per Basso Fig 3.20):

```
C1 = G / (2*pi*f_c*R1*k)
C2 = G / (2*pi*f_c*R1*k) * 1/k^2  = C1/k^2
    (equivalently: C2 = G*k / (2*pi*f_c*R1) ... check)

Actually, Venable's formulas:
C2 = 1 / (2*pi*R1*G*f_c*k)
C1 = C2 * (k^2 - 1)
R2 = k / (2*pi*f_c*C1)
```

Pole and zero locations:
```
f_z = f_c / k
f_p = f_c * k
```

### Type 3

**Phase boost calculation**:

```
boost = PM - PS - 90
k = tan(boost/4 + 45 deg)     [since double zero-pole, boost is divided by 2 per pair]
```

From Basso Eq. (3.63):

```
k = tan(pi/4 + boost_rad/4)
```

**Gain**:

```
G = 10^(-G_fc_dB / 20)
```

**Component values** (labels per Basso Fig 3.26):

```
C1 = 1 / (2*pi*f_c*G*R1*k)
C2 = C1 / (k^2 - 1)
R2 = k / (2*pi*f_c*C1)
C3 = 1 / (2*pi*f_c*G*R1*k)   (= C1)
R3 = R1 / (k^2 - 1)
```

Pole and zero locations (coincident pairs):
```
f_z1 = f_z2 = f_c / k
f_p1 = f_p2 = f_c * k
```

### k-Factor Trade-offs

- Higher k = greater phase boost but lower DC gain (gain loss penalty)
- k = 1: no boost (Type 1 response)
- Moderate k (2-5): good balance for most Type 2 designs
- Large k (>10): excessive DC gain reduction

### Numerical Example (Basso, Buck VM CCM)

100 kHz CCM buck, V_peak = 2 V, V_in = 10-20 V, I_out = 0.1-2 A:
- f_c = 5 kHz, target PM = 45 deg
- At f_c: PS = -146 deg, G_fc = -9.2 dB
- Type 3 required (phase lag near 180 deg)
- boost = 45 - (-146) - 90 = 101 deg
- k = 7.76
- G = 10^(9.2/20) = 2.88
- Result: C1=7.5nF, C2=1.1nF, C3=7.72nF, R2=11.9k, R3=1.5k
- Zeros at 1.8 kHz, poles at 14 kHz

---

## 5. Current-Mode Control

### 5.1 Subharmonic Instability

**Mechanism**: In CCM with D > 50%, a perturbation in inductor current does not decay between switching cycles. Each cycle, the perturbation is multiplied by:

```
a = -(S2 - S_a) / (S1 + S_a)
```

Without slope compensation (S_a = 0):
```
a = -S2/S1 = -D'/ D = -(1-D)/D
```

- |a| < 1 when D < 50%: perturbation decays (stable)
- |a| = 1 when D = 50%: sustained oscillation at f_sw/2
- |a| > 1 when D > 50%: divergent oscillation

**Symptom**: Alternating wide-narrow pulses, output ripple at f_sw/2, degraded transient response.

**Note**: Subharmonic instability does NOT occur in DCM.

### 5.2 Slope Compensation Design

**Minimum slope for stability at all duty ratios** (Basso Eq. 2.162):

```
S_a >= S2 / 2       (50% of the inductor off-slope)
```

This guarantees stability for D up to 100%.

**Optimal slope for Q = 1** (critically damped, Basso Eq. 2.200):

```
S_a = S2 * (1 - D) / 2     or equivalently solve from Q equation
```

But more practically, to achieve Q_s = 1:

```
S_a = S2*(1 - D')/2 + S1*(D - 0.5)
```

The general expression: use the Q_s formula and solve for S_a to get Q_s = 1 at the worst-case operating point.

**Inductor slopes by topology**:

| Topology | S1 (on-slope) | S2 (off-slope) |
|----------|---------------|----------------|
| Buck | (V_in - V_out) / L | V_out / L |
| Boost | V_in / L | (V_out - V_in) / L |
| Buck-Boost | V_in / L | V_out / L |

**Slope compensation expressed as minimum inductance** (Maniktala):

```
Buck:       L_uH >= (D - 0.34) / S_comp_A/us * V_IN
Boost:      L_uH >= (D - 0.34) / S_comp_A/us * V_O
Buck-Boost: L_uH >= (D - 0.34) / S_comp_A/us * (V_IN + V_O)
```

### 5.3 Small-Signal Models

#### Vorperian CC-PWM Switch Model (Basso Ch2)

The current-controlled PWM switch adds a complex pole pair at f_sw/2 to the voltage-mode PWM switch model. The small-signal model consists of dependent sources whose values incorporate the slope compensation.

Key result — the control-to-output contains a term:

```
1 / [1 + s/(w_s*Q_s) + (s/w_s)^2]
```

where w_s = pi * f_sw and Q_s is the quality factor of the subharmonic pole.

#### Quality Factor Q_s

**Vorperian form** (Basso Eq. 2.198):

```
Q_s = 1 / [pi * (S_a/S1 + D'/(2*D) - 1/2)]
```

Wait — more precisely (Basso):

```
Q_s = 1 / [pi * ((S_a + S1*D')/(S1) - 1)]
```

**Ridley form** (equivalent, Basso Eq. 2.201):

```
Q_s = 1 / [pi * (2*m_c*D' - 1)]
```

where m_c = 1 + S_a/S1 (Ridley's ramp coefficient).

**Stability requirement**: Q_s < 2 (Maniktala), conservative: Q_s <= 1.

- Q_s -> infinity: oscillation (no slope compensation, D > 50%)
- Q_s = 1: critically damped (optimal)
- m_c = 1.5 corresponds to 50% off-slope compensation => Q_s = 1 when D = 50%

#### Middlebrook Simplified Model (Maniktala Ch12)

For practical compensation design, ignore the f_sw/2 pole (if well-damped and above crossover). The plant reduces to:

```
G(s) = G_0 / (1 + s/w_p)
```

Single pole from output RC. This makes CM plants amenable to Type 2 compensation (or even Type 1 + ESR zero).

**DC gain G_0 by topology** (Maniktala Fig 12.25):

| Topology | G_0 (plant DC gain) |
|----------|---------------------|
| Buck | V_in * R_sense_gain / V_RAMP |
| Boost | V_in / (V_RAMP * (1-D)) * R_sense_gain |
| Buck-Boost | V_in / (V_RAMP * (1-D)) * R_sense_gain |

where R_sense_gain accounts for the current sense amplifier gain and sense resistor.

**Output pole** f_p = 1/(2*pi*R*C) for all topologies in CM.

---

## 6. TL431 + Optocoupler Compensation

The TL431 is a programmable shunt regulator (2.5 V reference) used as the error amplifier in isolated converters. It drives an optocoupler LED to transmit the error signal across the isolation barrier.

### Architecture

Two signal paths exist (Basso Ch3, Fig 3.55):
- **Slow lane**: R_upper / R_lower divider feeding the TL431 reference pin (sets DC operating point via internal op-amp)
- **Fast lane**: Direct AC coupling from V_out through the LED series resistor R_LED to the optocoupler (provides high-frequency response)

### Fast Lane Gain

The fast lane creates a minimum gain floor that cannot be reduced:

```
G_fast = CTR * R_pullup / R_LED
```

where CTR is the optocoupler current transfer ratio and R_pullup is the controller-side pull-up resistor.

This gain floor can be a limiting factor when low crossover gain is needed.

### Type 2 with TL431 (Basso Section 3.7.1)

**Transfer function** (Basso Eq. 3.83):

```
G(s) = -CTR * R_pullup / R_LED * [1 + s*R_upper*C_zero] / [s*R_upper*C_zero * (1 + s*R_LED*C_p)]
```

The zero and 0-dB crossover pole are naturally coincident (slope change on 0 dB axis).

**Pole and zero definitions**:

```
f_z  = 1 / (2*pi*R_upper*C_zero)     — zero from slow lane
f_po = 1 / (2*pi*R_upper*C_zero)     — 0-dB crossover (coincident with f_z)
f_p  = 1 / (2*pi*R_LED*C_p)          — high-frequency pole
```

Add a capacitor C_p from TL431 cathode (or FB pin) to ground to create the high-frequency pole.

**Mid-band gain at crossover**:

```
G_mid = CTR * R_pullup / R_LED
```

This is independent of f_z and f_p positions (because they are coincident at the 0-dB point).

**k-factor application**: Same procedure as standard Type 2, but:

```
R_LED = V_out / (G * CTR * I_pullup)    [simplified]
C_zero = k / (2*pi*f_c*R_upper)
C_p = 1 / (2*pi*f_c*k*R_LED)
```

### Type 3 with TL431 (Basso Section 3.7.2)

An RC network (R_pz, C_pz) is placed in parallel with R_LED to create the second zero-pole pair.

**Transfer function** (Basso Eq. 3.92-3.93):

The impedance of R_LED || (R_pz + 1/(s*C_pz)) replaces R_LED, adding a zero and a pole:

```
f_z2 = 1 / (2*pi*(R_LED || R_pz + ...))  [complex expression]
f_p2 = 1 / (2*pi*R_pz*C_pz) approximately
```

**Simplified gain formula when poles/zeros coincident** (Basso Eq. 3.101):

```
R_LED = R_pullup * CTR / G     (from midband gain requirement)
```

**C_pz calculation** (Basso Eq. 3.104):

```
C_pz = k / (2*pi*f_c*R_pz)
```

where R_pz is extracted from the pole/zero frequency requirements.

**Practical limitations**:
- R_LED affects both gain and pole-zero positions in Type 3 — may lead to impossible solutions
- If no valid R_LED exists: eliminate fast lane (add Zener or transistor per Basso Figs 3.63-3.64) and use standard op-amp Type 3 topology
- Alternatively, reduce controller pull-up resistor

### TL431 Biasing (Basso Section 3.7.3)

- Minimum TL431 bias current: 1 mA (for datasheet performance)
- Bias current is NOT set by R_LED; it depends on primary feedback current and CTR
- Worst case: maximum CTR drives minimum LED current

**External bias resistor** (Basso Eq. 3.112):

```
R_bias = (V_out - V_LED - V_ref) / I_bias
```

where V_ref = 2.5 V (TL431), V_LED ~ 1 V, I_bias >= 1 mA target.

**Alternative**: 1 kOhm resistor from LED anode to TL431 cathode uses LED forward drop (~1 V) as constant-current source.

### CTR Variations

Optocoupler CTR varies enormously with:
- Manufacturing batch (2:1 to 5:1 variation)
- Temperature
- Collector current bias point
- Aging

Always verify loop stability at CTR extremes. Typical range for SFH-615A: 40-200% (at different bias currents).

### Voltage Divider (Basso Eq. 3.119)

```
R_upper = (V_out - V_ref) / (I_bridge - I_bias_TL431)
R_lower = V_ref / I_bridge
```

Select I_bridge >> I_bias (TL431 reference pin bias, ~6 uA over temperature) to minimize static error.

---

## 7. Transconductance Amplifier (OTA) Compensation

Many integrated controllers use an OTA (gm amplifier) instead of a voltage-feedback op-amp. The key difference: the OTA converts a differential input voltage to an output current. An external impedance Z_O from the COMP pin to ground converts this current to voltage.

### Key Differences from Voltage Op-Amp (Maniktala Ch12)

1. **Both divider resistors matter for AC**: R_f1 and R_f2 both affect the AC gain (unlike voltage op-amp where only R_upper matters)
2. **The divider acts as a gain block**: H1 = R_f1 / (R_f1 + R_f2)
3. **No local feedback**: The loop is completed externally through the power stage

### Full OTA Compensation (Voltage-Mode, Maniktala)

Three cascaded blocks: H1 (divider), H2 (gm stage), H3 (output impedance).

**Feedback transfer function**:

```
H(s) = [R_f1 / (R_f1 + R_f2)] * gm * Z_O(s)
```

With a feedforward capacitor C_ff across R_f2, an additional zero-pole pair is introduced in H1:

```
f_z2 = 1 / (2*pi * R_f2 * C_ff)
f_p2 = 1 / (2*pi * R_f1*R_f2/(R_f1+R_f2) * C_ff)
```

Note: f_z2 and f_p2 are NOT independent — fixing one determines the other.

**Component equations (full OTA, voltage-mode)**:

```
C_ff = (R_f1 + R_f2) / (2*pi * R_f1 * R_f2 * f_cross)     [sets f_p2 at f_cross]

f_p0 = V_RAMP * (R_f1 + R_f2) / [(2*pi)^2 * f_LC * R_f2^2 * C_ff^2 * V_IN * R_f1]

C1 = 1 / (2*pi * f_p0 * (R_f1/(R_f1+R_f2)) * gm)

R1 = 1 / (2*pi * f_LC * C1)     [sets f_z1 at LC pole]

C2 = C_OUT * ESR / R1            [sets f_p1 at ESR zero]
```

### Simpler OTA Compensation (Voltage-Mode, Maniktala)

Omit C_ff. The divider becomes a simple resistive attenuator: H1 = R_f1/(R_f1 + R_f2).

Z_O consists of R1 in series with C1 (and optionally C2 in parallel).

**Component equations**:

```
f_p0 = V_RAMP * f_cross / (2*pi * f_LC * ESR * C_OUT * V_IN)

C1 = (R_f1/(R_f1+R_f2)) * gm / (2*pi * f_p0)

R1 = 1 / (2*pi * f_LC * C1)     [zero at LC pole]
```

**Requirement**: ESR zero must lie between f_LC and f_cross for this simpler scheme to work with voltage-mode control.

To optimize phase margin closer to 45 deg, reintroduce C2:

```
C2 = 1 / (2*pi * R1 * f_cross)     [pole at crossover]
```

This reduces the crossover frequency by ~20%.

### OTA Compensation for Current-Mode (Maniktala)

With the single-pole plant model, OTA compensation is straightforward:

**For transconductance op-amp** (Maniktala Fig 12.26, left):

```
f_p0 = f_cross / (A/B)         [A/B = plant DC gain from Fig 12.25]
C1 = y * gm / (2*pi * f_p0)    [y = R_f1/(R_f1+R_f2), attenuation ratio]
R1 = 1 / (2*pi * C1 * f_P)     [f_P = output pole = 1/(2*pi*R*C)]
C2 = 1 / (2*pi * R1 * f_esr)   [cancel ESR zero]
```

**For conventional op-amp** (Maniktala Fig 12.26, right):

```
f_p0 = f_cross / (A/B)
C1 = 1 / (2*pi * R1 * f_p0)    [R1 = R_upper, already chosen]
R2 = 1 / (2*pi * C1 * f_P)
C3 = 1 / (2*pi * R2 * f_esr)
```

Same procedure for all topologies — just use the appropriate DC gain A/B from the table.

---

## 8. Design Procedure

### Step-by-Step: Complete Feedback Loop Design

**1. Characterize the plant**

a. Determine topology and control mode (VM/CM, CCM/DCM)
b. Identify operating conditions: V_in range, I_out range, L, C, ESR, R_i, F_sw
c. Calculate or measure the plant transfer function G(s):
   - Use expressions from Section 1, or
   - Use averaged SPICE model, or
   - Measure with network analyzer
d. Identify key frequencies:
   - f_LC = 1/(2*pi*sqrt(L_eq*C)) for VM CCM
   - f_p = 1/(2*pi*R*C) for CM
   - f_esr = 1/(2*pi*ESR*C)
   - f_rhpz (if boost/buck-boost)
   - Plant DC gain at low frequency

**2. Select crossover frequency f_c**

Rules of thumb:
- f_c < F_sw / 5 (voltage-mode), f_c < F_sw / 3 (current-mode)
- f_c > 5 * f_LC (for VM CCM, to be above the resonance peaking)
- f_c < f_rhpz / 3 (if RHPZ present; conservative: f_rhpz / 5 to /10)
- f_c chosen to meet transient response requirements

Approximate undershoot from crossover (Basso Eq. 3.7):
```
V_p = delta_I_out / (2*pi*f_c*C_out)
```
(Valid when ESR < 1/(2*pi*f_c*C_out))

**3. Select target phase margin**

- Minimum: 45 deg
- Recommended: 70 deg (Basso) for critically damped response (Q ~ 0.5)
- 76 deg gives Q = 0.5 exactly for a second-order system
- Gain margin: >= 10-15 dB

**4. Read plant gain and phase at f_c**

From Bode plot (measured or calculated):
- G_fc (dB): gain at f_c
- PS (deg): phase shift at f_c (negative)

**5. Select compensator type**

| Plant characteristic | Compensator type |
|---------------------|------------------|
| Single pole, phase < 45 deg | Type 1 |
| Single pole + ESR zero, phase ~ 90 deg | Type 2 |
| CM CCM (single pole after f_sw/2 damping) | Type 2 |
| VM DCM (single pole) | Type 2 |
| VM CCM (LC double-pole, phase ~ 180 deg) | Type 3 |

**6. Calculate compensator components**

Use k-factor method (Section 4) or direct pole-zero placement:

a. Calculate required phase boost: boost = PM - PS - 90 deg
b. Calculate k from boost
c. Calculate gain G = 10^(-G_fc_dB/20)
d. Calculate component values from k-factor formulas

For TL431 designs: See Section 6.
For OTA designs: See Section 7.

**7. Verify stability**

a. Plot compensated loop gain T(s) = G(s)*H(s)
b. Check PM at crossover (target met?)
c. Check GM (>= 10-15 dB?)
d. Verify at ALL operating corners:
   - Min/max V_in
   - Min/max I_out (R_load)
   - CCM/DCM transition
e. Check conditional stability: ensure phase does not approach 0 deg (or -180 deg) at any frequency where gain > 0 dB

**8. Verify robustness**

a. Vary output capacitor ESR (aging, temperature):
   - ESR zero moves; check PM still adequate
b. Vary CTR for optocoupler designs (full range)
c. Check component tolerances (+/-20% on R, C)
d. Temperature effects on poles/zeros

**9. Transient verification**

a. Step-load test: apply delta_I_out at both V_in extremes
b. Check undershoot/overshoot < specification
c. Check recovery time (linked to PM and f_c)
d. Verify DCM-to-CCM and CCM-to-DCM transitions
e. Check for conditional stability under large-signal transients

### Quick Reference: Compensator Selection

```
VM CCM Buck/Forward         -> Type 3    (LC double-pole, ~180 deg lag)
VM CCM Boost/Buck-Boost     -> Type 3    (LC double-pole + RHPZ)
VM DCM (any topology)       -> Type 2    (single-pole plant)
CM CCM (any topology)       -> Type 2    (single-pole after damping f_sw/2)
CM DCM (any topology)       -> Type 1/2  (single-pole, simplest plant)
PFC voltage loop            -> Type 1    (very low bandwidth, 5-20 Hz)
```

### Crossover Frequency Targets

```
VM converters:      f_c ~ F_sw/6 to F_sw/5
CM converters:      f_c ~ F_sw/4 to F_sw/3
With RHPZ:          f_c < min(f_rhpz)/3   (worst-case RHPZ at max D, max L, min R)
PFC voltage loop:   f_c ~ 5-20 Hz (well below 2x line frequency)
```

---

## Canonical Model (from Erickson Ch7-8)

Sources: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed (2020), Chapters 7-9 (pp.227-416).

This section covers Erickson's canonical small-signal model and controller design methodology. While Basso and Maniktala (Sections 1-8 above) focus on practical compensator design with specific component equations, Erickson provides the *unified theoretical framework* showing why all CCM PWM converters share the same transfer function structure. This is essential for understanding converter dynamics at a deep level.

### 9.1 The Canonical Small-Signal Circuit Model (Ch7, pp.257-263)

Erickson shows that **every CCM PWM dc-dc converter** can be represented by a single canonical equivalent circuit (Fig 7.33, p.258), regardless of topology. This model has three functional blocks:

1. **Ideal DC transformer** with turns ratio 1:M(D) -- models the voltage/current conversion at the quiescent operating point
2. **Effective low-pass filter** He(s) -- models the reactive elements (inductor, capacitor). The element values in this filter are NOT necessarily the physical component values; they are transformed by the operating point.
3. **Duty-cycle-dependent sources** e(s)*d_hat(s) and j(s)*d_hat(s) -- model how duty cycle perturbations affect the output. These sources are generally frequency-dependent (containing s-domain terms).

#### Key Transfer Functions from the Canonical Model

**Line-to-output** (Eq. 7.86, p.259):
```
Gvg(s) = M(D) * He(s)
```

**Control-to-output** (Eq. 7.87, p.259):
```
Gvd(s) = e(s) * M(D) * He(s)
```

**Output impedance** (Eq. 7.88, p.259):
```
Zout(s) = Zeo(s) || R
```

where He(s) is the effective filter transfer function and Zeo(s) is the effective filter output impedance.

#### Canonical Model Parameters for Basic Converters (Table 7.1, p.263)

For converters with a single inductor and capacitor, the effective filter is a single-section LC:

| Converter | M(D) | Le (effective L) | e(s) | j(s) |
|---|---|---|---|---|
| **Buck** | D | L | V/D^2 | V/R |
| **Boost** | 1/D' | L/D'^2 | V * (1 - sL/(D'^2 * R)) | V/(D'^2 * R) |
| **Buck-Boost** | -D/D' | L/D'^2 | -V/D^2 * (1 - sDL/(D'^2 * R)) | -V/(D'^2 * R) |

**Critical insight**: The effective inductance Le of the boost and buck-boost is L/D'^2, not L. This means the effective filter corner frequency shifts with the quiescent operating point D. At high duty cycle (D -> 1, D' -> 0), Le -> infinity and the resonant frequency drops toward zero. This is why boost and buck-boost converters become harder to control at high duty cycle.

**The e(s) generator for boost and buck-boost contains a right-half-plane zero** at:
- Boost: wz = D'^2 * R / L
- Buck-boost: wz = D'^2 * R / (D*L)

This is the origin of the RHPZ: it arises from the frequency-dependent e(s) source in the canonical model. The buck converter has a frequency-independent e(s), and hence no RHPZ.

### 9.2 Transfer Function Tables (Ch8, Table 8.2, p.326)

All CCM converters share a common transfer function structure. The control-to-output is always:

```
Gvd(s) = Gd0 * (1 - s/wz) / (1 + s/(Q*w0) + (s/w0)^2)
```

and the line-to-output is always:

```
Gvg(s) = Gg0 / (1 + s/(Q*w0) + (s/w0)^2)
```

| Parameter | Buck | Boost | Buck-Boost |
|---|---|---|---|
| **Gd0** (control-to-output DC gain) | V/D | V/D' | V/(D*D') |
| **Gg0** (line-to-output DC gain) | D | 1/D' | -D/D' |
| **w0** (LC resonant frequency) | 1/sqrt(LC) | D'/sqrt(LC) | D'/sqrt(LC) |
| **Q** (quality factor) | R*sqrt(C/L) | D'*R*sqrt(C/L) | D'*R*sqrt(C/L) |
| **wz** (RHP zero, rad/s) | infinity (none) | D'^2*R/L | D'^2*R/(D*L) |

**For transformer-isolated versions** (forward, full-bridge, flyback, etc.): multiply Gvg by the transformer turns ratio; the transfer functions and table parameters otherwise apply directly (p.326). The transformer magnetizing inductance contributes negligible dynamics when it is reset by the input voltage.

**Physical origin of the RHPZ** (p.327-328): In boost and buck-boost converters, the average diode current is id = d' * iL. When duty cycle increases (to raise output voltage), d' initially decreases, which *reduces* energy delivery to the output. The output capacitor initially discharges. Only after the inductor current ramps up sufficiently does the output voltage begin to increase. This "wrong-way" initial response is the time-domain manifestation of the RHPZ.

### 9.3 Graphical Transfer Function Construction (Ch8, pp.328-347)

Erickson develops a powerful "algebra-on-the-graph" method for constructing impedances and transfer functions by inspection:

1. **Series impedances**: add asymptotes graphically (the larger magnitude dominates)
2. **Parallel impedances**: use inverse addition (the smaller magnitude dominates)
3. **Voltage dividers**: divide asymptotes of the numerator impedance by the denominator

This method allows rapid sketching of converter transfer functions without extensive algebra. Key applications:
- Identifying which components dominate at each frequency
- Finding approximate corner frequencies and asymptote gains
- Understanding how parameter changes affect the frequency response

### 9.4 Feedback System Architecture (Ch9, pp.358-366)

The closed-loop output voltage is (Eq. 9.4, p.362):

```
v_hat = vref_hat * (1/H) * T/(1+T) + vg_hat * Gvg/(1+T) - iload_hat * Zout/(1+T)
```

where:
- T(s) = H(s) * Gc(s) * Gvd(s) / VM is the **loop gain**
- H(s) is the sensor gain (voltage divider)
- Gc(s) is the compensator transfer function
- VM is the PWM modulator gain (peak sawtooth voltage)

**Three key results**:

1. **Disturbance rejection**: Line-to-output and output impedance are divided by (1+T). At frequencies where |T| >> 1, disturbances are attenuated by |T|.
2. **Reference tracking**: v/vref -> 1/H when |T| >> 1. The output depends only on the sensor gain, not on the forward path gains.
3. **Bandwidth**: The crossover frequency fc (where |T| = 1) defines the feedback bandwidth. Above fc, the loop has no effect on disturbances.

**Graphical construction of 1/(1+T) and T/(1+T)** (pp.364-368): These can be sketched directly from the loop gain Bode plot:
- T/(1+T) ~ 1 for f << fc, ~ T for f >> fc
- 1/(1+T) ~ 1/T for f << fc, ~ 1 for f >> fc

### 9.5 Stability: Phase Margin and Nyquist Criterion (Ch9, pp.369-386)

#### Phase Margin Test (p.370)

```
phi_m = 180 + angle(T(j*2*pi*fc))
```

If phi_m > 0 and there is exactly one crossover frequency and T(s) has no RHP poles, the system is stable. This is the standard test used in Sections 1-8 above.

#### Nyquist Stability Criterion (pp.371-380)

When the phase margin test is ambiguous (multiple crossover frequencies) or the open-loop system contains RHP poles, the Nyquist criterion must be used. The Nyquist contour encloses the entire right half of the s-plane. The number of unstable closed-loop poles equals:

```
N_RHP_closed_loop = N_RHP_open_loop + (encirclements of -1 point by T(Gamma))
```

where encirclements are counted clockwise. For stability, N_RHP_closed_loop must equal zero.

**Practical relevance**: The Nyquist criterion becomes important for converters with input filters (Ch17), where the loop gain T(s) may have multiple crossover frequencies or conditional stability (gain > 0 dB at frequencies where phase is near -180 degrees).

#### Phase Margin vs. Closed-Loop Damping (pp.381-386)

Erickson derives the precise relationship between phase margin and closed-loop Q-factor for a second-order system (Eq. 9.42, p.382):

```
Q_cl = 1 / (2 * cos(phi_m) * sqrt(1 + tan^2(phi_m)/4))
```

Simplified for practical design:

| Phase Margin | Closed-Loop Q | Overshoot | Character |
|---|---|---|---|
| 76 deg | 0.5 | 0% | Critically damped (no overshoot) |
| 60 deg | 0.87 | 8.8% | Slightly underdamped |
| 52 deg | 1.0 | 16.3% | Moderate ringing |
| 45 deg | 1.2 | 23% | Acceptable minimum |
| 30 deg | 2.0 | 44% | Excessive ringing |

**Design recommendation**: Target 52-76 degrees phase margin. At phi_m = 76 deg, the closed-loop has Q = 0.5 (critically damped, no overshoot). The common target of 45 deg gives Q ~ 1.2 with 23% overshoot, which may be acceptable but is not optimal.

#### Load Step Response (pp.384-386)

For a step change in load current delta_Iout, the output voltage initially changes by:

```
delta_V_peak ~ delta_Iout / (2*pi*fc*C)    [when ESR is small]
```

or

```
delta_V_peak ~ delta_Iout * ESR              [when ESR dominates]
```

The recovery time is inversely proportional to fc and depends on the phase margin (lower phase margin = more ringing during recovery).

### 9.6 Compensator Design: Lead, Lag, and PID (Ch9, pp.387-403)

Erickson's compensator design approach differs from the k-factor method (Section 4 above) by using classical control terminology (PD, PI, PID) and deriving compensator parameters from geometric phase margin requirements.

#### Lead (PD) Compensator (pp.388-390)

Transfer function:
```
Gc(s) = Gc0 * (1 + s/wz) / (1 + s/wp)
```

Used to improve phase margin by adding a zero below crossover. The maximum phase boost occurs at f_phi_max = sqrt(fz * fp). To obtain a desired phase lead theta at crossover:

```
fz = fc * sqrt((1 - sin(theta)) / (1 + sin(theta)))
fp = fc * sqrt((1 + sin(theta)) / (1 - sin(theta)))
Gc0 = sqrt(fz/fp)    [to maintain crossover frequency unchanged]
```

The ratio fp/fz determines the maximum achievable phase lead (Eq. 9.55-9.57):
- fp/fz = 3: max lead = 30 deg
- fp/fz = 10: max lead = 55 deg
- fp/fz = 100: max lead = 78 deg

**Side effect**: The high-frequency pole is essential for both noise attenuation and practical amplifier bandwidth limits. Crossover should be < ~10% of switching frequency.

#### Lag (PI) Compensator (pp.391-393)

Transfer function:
```
Gc(s) = Gc_inf * (1 + wL/s)
```

Adds an inverted zero (integrator + zero) at low frequency fL. This gives infinite DC gain (zero steady-state error) without affecting phase margin, provided fL << fc.

**Design equations** (Eq. 9.62-9.63):
```
Gc_inf = fc / (Tu0 * f0)     [set crossover frequency]
fL << fc                      [preserve phase margin]
```

Best for single-pole plants (current-mode converters, DCM converters). The PI compensator alone is often sufficient for current-mode control.

#### Combined PID Compensator (pp.393-403)

Transfer function (Eq. 9.64):
```
Gc(s) = Gcm * (1 + wL/s) * (1 + s/wz) / ((1 + s/wp1) * (1 + s/wp2))
```

Combines PI (low-frequency integration) with PD (phase lead at crossover). Two high-frequency poles (fp1, fp2) roll off gain above crossover. This is the general-purpose compensator for voltage-mode CCM converters (equivalent to the Type 3 compensator of Section 3.3).

#### Design Example: Buck Converter PID (pp.394-403)

Erickson designs a PID compensator for a 28V-to-15V buck converter at 100 kHz:

- Plant: Gvd has f0 = 1 kHz, Q0 = 9.5, Gd0 = 28V
- Target: fc = 5 kHz (= fsw/20), phase margin = 52 deg
- Uncompensated loop gain at 5 kHz: -20.6 dB

**PD compensator design** (first):
- Required phase lead: 52 deg
- From Eq. 9.57: fz = 1.7 kHz, fp = 14.5 kHz
- Gc0 = 3.7 (11.3 dB)
- Result: loop gain T0 = 18.7 dB (8.6x) at DC

**Adding PI (lag)** to get PID:
- Inverted zero at fL = 12 Hz
- This boosts DC loop gain to 79 dB without affecting crossover
- Line-to-output attenuation at 120 Hz improves from 18.7 dB to ~60 dB

**Key result**: The PID compensator achieves both large DC loop gain (for tight regulation and disturbance rejection) and adequate phase margin at 5 kHz crossover (for fast transient response).

### 9.7 Loop Gain Measurement (Ch9, pp.403-409)

Erickson describes three injection methods for experimental measurement of loop gains:

1. **Voltage injection** (pp.405-407): Inject a small AC voltage in series with the feedback loop at a point where the impedance looking in one direction is much lower than the other. The ratio of the signals on either side of the injection point gives T(s). Valid when Zs << Zl at the injection point.

2. **Current injection** (pp.407-408): Inject a small AC current into a node of the feedback loop. Useful when the impedance conditions for voltage injection are not met.

3. **Measurement of unstable systems** (p.408): Both voltage and current injection can be used to measure the loop gain of an unstable system, provided the system is stabilized by an auxiliary feedback path during measurement.

**Practical note**: Erickson emphasizes that the injection point matters. The most common injection point is between the compensator output and the PWM modulator input, where the source impedance (compensator output) is typically much lower than the load impedance (modulator input).

### 9.8 Relating Erickson to Basso/Maniktala Terminology

| Erickson Term | Basso/Maniktala Equivalent | Notes |
|---|---|---|
| Lead (PD) compensator | Type 2 (single zero-pole pair) | Same structure, different derivation approach |
| Lag (PI) compensator | Origin pole + zero | Adds integration for zero steady-state error |
| Combined (PID) compensator | Type 3 (two zero-pole pairs) | Full PID with two HF poles |
| e(s) in canonical model | Not explicitly used | Erickson derives Gvd from canonical model; Basso uses averaged switch models |
| Phase margin test | Same | Identical definition: phi_m = 180 + angle(T(fc)) |
| Nyquist criterion | Rarely discussed in Basso | Erickson provides full treatment; needed for input filter stability analysis |
| Crossover frequency fc | Same | All sources agree on fc < fsw/5 (VM), fc < fsw/3 (CM) |
| Output impedance reduction | Same: Zout/(1+T) | All sources agree |

**When to use which approach**: Use the k-factor method (Basso, Section 4) for rapid compensator design with explicit component values. Use Erickson's canonical model for understanding *why* a given topology has certain dynamic properties, for comparing topologies, and for analyzing systems with input filters or other complications where the Nyquist criterion is needed.

---

## Current-Programmed Control -- Erickson Treatment (Ch18)

Source: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapter 18, pp.727-806.

### 10.1 Overview and Advantages

Current-programmed control (CPM), also called peak current mode (PCM) control, replaces direct duty-cycle control with control of the peak switch current. A comparator turns off the transistor when the sensed switch current i_s(t) reaches the control signal i_c(t). The duty cycle is no longer the control input; instead, it becomes an internal variable that depends on i_c, the inductor current, and the converter voltages.

**Key advantages over voltage-mode control:**
- Simpler dynamics: the control-to-output transfer function G_vc(s) = v_hat/i_c_hat contains one fewer pole than G_vd(s). The inductor pole is effectively removed (moved to high frequency).
- Simpler compensator: typically Type 2 (PI + pole) is sufficient, no need for Type 3.
- Inherent cycle-by-cycle overcurrent protection.
- Reduced transformer saturation in push-pull and full-bridge converters (current balancing).
- Natural current sharing in parallel converters.
- Input voltage feedforward is inherent (changes in V_in change the inductor current slope, which automatically adjusts the duty cycle).

**Disadvantages:**
- Susceptibility to noise on the current sense signal (premature comparator triggering).
- Subharmonic instability for D > 0.5 without slope compensation.
- More complex analysis due to multiple feedback paths.

### 10.2 Simple First-Order Model (Averaged Switch Approach)

The key approximation: the current-programmed controller forces the average inductor current to equal the control signal:

```
<i_L(t)>_Ts ~ i_c(t)
```

This is valid when the inductor current ripple and artificial ramp are small compared to i_c.

**Physical model (averaged switch):** The switch network output port behaves as a current source of value i_c. The switch network input port follows a power sink characteristic (constant power), drawing power from V_g equal to the power supplied by the i_c current source.

The input port has a **negative incremental resistance** at DC:

```
r_1 = -V_1 / I_1 = -V_g^2 / P_load
```

This negative resistance arises because increasing V_g decreases I_g to maintain constant power.

**Two-port equivalent circuit parameters (simple model):**

| Converter | g_1 | f_1 | r_1 | g_2 | f_2 | r_2 |
|-----------|-----|-----|-----|-----|-----|-----|
| Buck | D/R | D(1 + sL/R) | -R/D^2 | 0 | 1 | inf |
| Boost | 0 | 1 | inf | 1/(D'R) | D'(1 - sL/(D'^2 R)) | R |
| Buck-boost | -D/R | D(1 + sL/(D'R)) | -D'R/D^2 | -D^2/(D'R) | -D'(1 - sDL/(D'^2 R)) | R/D |

### 10.3 Control-to-Output Transfer Function (Simple Model)

From the two-port model:

```
G_vc(s) = v_hat / i_c_hat |_{vg=0} = f_2 * (r_2 || R || 1/(sC))
```

**Results for basic converters:**

**Buck:**
```
G_vc(s) = R / (1 + sRC)
```
Single pole at f_p = 1/(2*pi*R*C). No RHPZ. DC gain = R. The inductor pole is completely gone.

**Boost:**
```
G_vc(s) = D'*R / (1 + D) * (1 - sL/(D'^2*R)) / (1 + sRC/(1+D))
```
Single pole. RHP zero preserved at f_z = D'^2*R / (2*pi*L) -- same as voltage mode.

**Buck-boost:**
```
G_vc(s) = -R*D' / (1+D) * (1 - sDL/(D'^2*R)) / (1 + sRC/(1+D))
```
Single pole. RHP zero preserved at f_z = D'^2*R / (2*pi*D*L).

**Key insight:** Current programming removes the inductor pole from G_vc but does NOT remove the RHP zero from boost and buck-boost topologies. The RHP zero is a fundamental property of the power stage topology, not the control mode.

### 10.4 Line-to-Output Transfer Function (Simple Model)

```
G_vg(s) = v_hat / v_g_hat |_{ic=0} = g_2 * (r_2 || R || 1/(sC))
```

**For the buck converter**, the simple model predicts G_vg = 0. The buck converter with ideal current programming has perfect line rejection. In practice, G_vg is small but nonzero (see the more accurate model below).

**For boost and buck-boost**, G_vg is nonzero but typically smaller than in voltage-mode control.

### 10.5 Subharmonic Instability and Slope Compensation

#### The D > 0.5 instability

Consider a perturbation i_L(0) in the inductor current at the start of a switching cycle. After one complete cycle, the perturbation becomes:

```
i_L(T_s) - I_L = -(m_2/m_1) * (i_L(0) - I_L)
```

The perturbation is multiplied by the factor alpha = -m_2/m_1 each cycle. For stability, |alpha| < 1, which requires m_2 < m_1, i.e.:

- Buck: m_2/m_1 = V/(V_g - V) = D/(1-D). Stable when D < 0.5.
- Boost: m_2/m_1 = (V - V_g)/V_g = D/(1-D). Same condition.
- Buck-boost: m_2/m_1 = V/V_g = D/(1-D). Same condition.

**For D > 0.5, the perturbation grows each cycle**, leading to a period-doubling subharmonic oscillation at f_sw/2.

#### Slope compensation (artificial ramp)

Adding a ramp with slope -m_a to the control signal (or equivalently, adding slope m_a to the sensed current):

```
i_c(t) -> i_c - m_a * t    (during each switching period)
```

The stability condition becomes:

```
alpha = -(m_2 - m_a) / (m_1 + m_a)
```

For |alpha| < 1, we need m_a > m_2 - m_1 (when D > 0.5).

**Optimal slope compensation:** Setting m_a = m_2 gives alpha = 0, meaning any perturbation is corrected in exactly one cycle (deadbeat response). This is the optimal choice.

**Minimum slope compensation for stability:**

```
m_a > (m_2 - m_1) / 2    (for all D)

Equivalently: m_a >= m_2 / 2    (sufficient for all D, including D = 1)
```

**Practical guideline:** Use m_a = m_2/2 to m_2 for robust stability across the full duty cycle range. Many designers use m_a = m_2 (optimal).

#### Effect of slope compensation on dynamics

Adding slope compensation modifies the effective gain of the current loop:

```
F_m = 1 / ((m_a + m_1) * T_s)    (modulator gain)
```

The more slope compensation added, the lower the effective current loop gain. Excessive slope compensation makes the converter behave more like voltage-mode control, gradually reintroducing the inductor pole into G_vc.

### 10.6 More Accurate Model (with current ripple and slope compensation)

The simple model assumes i_L = i_c exactly. A more accurate model accounts for the inductor current ripple and slope compensation.

The relationship between average inductor current and the control inputs is:

```
<i_L>_Ts = i_c - (m_1*d - m_2*d')*T_s/2 - m_a*d*T_s
```

After perturbation and linearization, the current-programmed controller is modeled by:

```
i_L_hat = F_m * (i_c_hat - (d*T_s/2)*m_1_hat - (d'*T_s/2)*m_2_hat - m_a*T_s*d_hat)
    (approximately)
```

where the m_1_hat and m_2_hat terms account for how changes in converter voltages affect the inductor current slopes, and hence the duty cycle.

The modulator gain F_m:

```
F_m = 1 / ((m_a + m_1) * T_s)
```

This more accurate model:
- Predicts a nonzero G_vg for the buck converter
- Shows how slope compensation affects all transfer functions
- Accounts for the high-frequency pole at approximately f_sw/(2*pi) (see sampled-data model)

### 10.7 CPM Transfer Functions (More Accurate Model)

The more accurate model gives transfer functions of the form:

```
G_vc(s) = G_vc0 * (1 + s/w_z) / ((1 + s/w_p1)(1 + s/w_p2))
```

**For the buck converter (CCM, with slope compensation):**

```
G_vc0 = R / (1 + R/r_2)    where r_2 depends on slope compensation

w_p1 = (1 + R/r_2) / (RC)     (dominant pole, near 1/(RC))

w_p2 ~ f_sw * pi * (m_a + m_1) / (m_1 + m_2)    (high-frequency pole from current sampling)
```

When m_a = 0 and D < 0.5: r_2 -> infinity, and G_vc simplifies to the simple model result R/(1+sRC).

When m_a = m_2 (optimal slope comp): the high-frequency pole is at approximately f_sw/pi.

### 10.8 High-Frequency Dynamics: Sampled-Data Model

The averaged models predict a high-frequency pole but cannot capture the discrete-time nature of the current sampling. A sampled-data analysis reveals:

The current loop has an effective gain:

```
G_ic(z) = 1 / (1 - alpha * z^(-1))
```

where alpha = -(m_2 - m_a)/(m_1 + m_a).

At the Nyquist frequency (f_sw/2), the sampled-data model predicts a phase lag of:

```
Phase lag at f_sw/2 = -180 * alpha / (1 + |alpha|)    degrees (approximately)
```

When alpha = -1 (no slope compensation, D = 0.5): the phase lag is -180 degrees at f_sw/2, confirming instability.

**First-order approximation** (valid for moderate frequencies):

The high-frequency current loop pole can be approximated as:

```
f_hf = (M_1 + M_2) / (2*M_a + M_1 - M_2) * f_sw / pi

With optimal slope comp (M_a = M_2): f_hf = f_sw / pi
With M_a = M_2/2: f_hf = f_sw / (2*pi)
```

This pole should be well above the voltage loop crossover for the simple first-order CPM model to be valid.

**Second-order approximation:** Adds a pair of complex poles at f_sw/2 (the Nyquist frequency), modeling the resonant peaking that occurs near f_sw/2. The Q-factor of these poles depends on the slope compensation amount.

### 10.9 CPM in Discontinuous Conduction Mode

In DCM, the switch current is a triangular pulse starting from zero each cycle. The peak current is controlled by i_c.

**Key differences from CCM:**
- No subharmonic instability (the inductor current resets to zero each cycle, so perturbations do not propagate)
- No slope compensation needed
- The control-to-output transfer function has a single pole (like CCM CPM, but for a different reason)
- The DC gains become strongly load-dependent

### 10.10 Average Current Mode (ACM) Control

Average current mode control uses a feedback loop to regulate the **average** inductor current (not the peak) to follow a reference.

**Architecture:**
1. Sense inductor current (or switch current reconstructed with slope)
2. Low-pass filter or integrate to obtain average current
3. Compare to reference i_ref
4. Error amplifier (current compensator) drives the duty cycle

**Advantages over peak current mode:**
- No subharmonic instability (slope compensation generally not needed for D < 1)
- Better noise immunity (averaging filters noise)
- More accurate current regulation (controls average, not peak)
- Essential for PFC applications (where the average input current must follow a sinusoidal reference)

**Transfer function (control-to-inductor-current):**

```
G_id(s) = v_g / (sL)    (simplified, single integrator)
```

The current loop compensator is typically a Type 2 (PI + pole):

```
G_ci(s) = K * (1 + s/w_z) / (s * (1 + s/w_p))
```

- Zero at or below the power stage LC resonant frequency
- Pole at f_sw/2 to f_sw/5 for noise attenuation
- Crossover: f_c_current = f_sw/10 to f_sw/5

**With the inner current loop closed**, the outer voltage loop sees a controlled current source with approximately single-pole response. The voltage loop compensator is then a simple PI (Type 2).

### 10.11 Design of Voltage Loop Around CPM Converter

**System model:** The voltage loop controls i_c, which controls the output voltage through G_vc(s).

**Design procedure:**
1. Characterize G_vc(s) -- single dominant pole, possible RHPZ
2. Choose crossover frequency: f_c < f_sw/3 (CPM allows higher crossover than VM)
3. If boost/buck-boost: f_c < f_rhpz/3
4. Design Type 2 (PI + pole) compensator:
   - Integrator for zero steady-state error
   - Zero at or near the G_vc pole frequency
   - High-frequency pole at f_sw/2 for noise filtering
5. Verify phase margin >= 45 degrees at all operating points

**Design example (Erickson Section 18.6.2):** Buck converter, 28V input, 15V output, 100 kHz switching, current-mode control.

- G_vc has a single pole at f_p = 1/(2*pi*RC) ~ 1.6 kHz
- Target crossover: f_c = 10 kHz (f_sw/10)
- Compensator: PI with zero at f_p, gaining 20 dB/decade below f_c
- Result: Phase margin ~ 65 degrees, good transient response

### 10.12 Input Filter Interaction with CPM

Current programming modifies the converter input impedance. The CPM converter input impedance Z_N and Z_D (from Chapter 17) are altered:
- The negative input resistance magnitude increases (becomes less negative) with current programming
- Input filter design is generally easier with CPM than with voltage-mode control
- The impedance inequalities ||Z_o|| << ||Z_N|| and ||Z_o|| << ||Z_D|| still apply

### 10.13 Quick Reference: CPM Design

```
Slope compensation:    m_a >= m_2/2 (minimum), m_a = m_2 (optimal deadbeat)
Control-to-output:     Single pole at ~1/(2*pi*RC), possible RHPZ
Compensator:           Type 2 (PI + HF pole)
Crossover:             f_c < f_sw/3, f_c < f_rhpz/3 (if applicable)
Phase margin target:   >= 52 degrees
Current sense gain:    R_sense = V_sense_max / I_peak_max
HF pole location:      ~f_sw/pi (with optimal slope comp)
```

---

## Dixon SEPIC Preregulator Design (from SLUP103)

Source: L.H. Dixon, "High Power Factor SEPIC Preregulator," Unitrode Seminar SEM900, Topic 6 (TI SLUP103).

### 11.1 SEPIC Topology Overview

The SEPIC (Single-Ended Primary Inductance Converter) combines boost-like continuous input current with flyback-like output voltage flexibility. Uses two inductors (L1, L2) and a coupling capacitor Cc.

**Duty cycle** (identical to buck-boost/flyback):

```
D = Vo / (Vin + Vo)
```

**Key relationships:**
- Io = IL2 (steady-state average)
- Iin = IL1
- Vcc(avg) = Vin (coupling capacitor voltage equals input)
- Switch and rectifier block voltage: Vin + Vo
- Switch and rectifier peak current: Iin + Io

### 11.2 Coupled Inductor and Ripple Current Steering

When L1 and L2 are wound on the same core with identical voltages applied to both windings, the leakage inductance LL (located in series with L1 at the input) steers HF ripple current away from the input and into L2 through Cc.

**Design result (Dixon example):** With 10% leakage (LL = 0.2 mH out of 2 mH mutual inductance), input ripple reduced from 250 mApp to 50 mApp (5x reduction), while L2 ripple increased to 500 mApp.

**Mechanism:** The high impedance of LL opposes AC ripple in L1, while the low impedance of Cc provides an easy path for HF ripple through L2. Total current (IL1 + IL2) through the switch remains unchanged.

**Design constraints:**
- A large LL-Cc product gives low input ripple but impairs frequency response
- Prefer small Cc (reduces inrush surge and cost) with correspondingly large LL
- LL should be integrated into the coupled inductor design to eliminate additional size/cost

### 11.3 LL-Cc Resonance and Damping

The leakage inductance LL and coupling capacitor Cc form a resonant circuit (in Dixon's example: 0.2 mH and 0.5 uF resonate at 16 kHz with high Q). This resonance is shock-excited and must be damped.

**Damping network:** RD and CD in shunt with Cc.

```
Critical damping: RD = 0.5 * sqrt(LL / Cc)    (resonant impedance / 2)
Practical compromise (Q ~ 1): RD chosen for underdamped but adequate response
```

Dixon example: RD = 10 ohm, CD = 2.5 uF (Q = 1). CD reduces the self-resonant frequency but at the switching frequency the coupling network remains effectively 0.5 uF. Normal operating loss in the damping network is less than 1 W.

**Series damping is impractical** because the high AC current through Cc causes excessive loss.

### 11.4 Control Loop Approach: Average Current Mode

The SEPIC has three active poles above the LL-Cc resonance (one load-dependent), making it impractical to achieve crossover above resonance with conventional voltage or peak current mode control.

**Solution:** Use average current mode control (CMC) of the switch current.

- Average switch current = average input current (at frequencies below LL-Cc resonance)
- The control-to-switch-current characteristic lacks the two resonant poles present in the control-to-input-current characteristic
- The LL-Cc resonant circuit is placed outside the control loop (damping network prevents ringing)

**Advantages of average CMC for SEPIC and other HPF topologies:**
1. Input voltage feedforward -- adapts rapidly to instantaneous line voltage changes
2. Eliminates peak-to-average error that contributes to distortion with peak CMC
3. Sufficient gain and bandwidth in discontinuous mode to track the current reversal at the cusp of the rectified voltage waveform

**Current sensing:** The switch current is discontinuous, enabling use of a current transformer (CT) for sensing -- not possible with continuous input current. CT provides high sense voltage, reducing required amplifier bandwidth and eliminating sense resistor power loss.

### 11.5 Voltage Loop Design

Once the current loop is optimized and closed, the specific power topology is buried within the current loop. The voltage loop functions identically for boost, flyback, or SEPIC -- it programs the desired input current level, while the current loop controls the specific topology.

Any HPF control IC with a current amplifier (e.g., UC3854/UC3854A) can be used for the SEPIC by following the same procedure as for a boost preregulator.

### 11.6 Overcurrent Limiting (Foldback Characteristic)

An absolute peak switch current limit provides foldback input current limiting:

```
Peak switch current = Iin + Io = Iin * (Vin + Vo) / Vo
```

At reduced Vo (overload or startup), the same peak current limit yields reduced input power because Io = peak_current * D / (1-D) and D is small when Vo is small.

**Setting the limit:** Must be high enough for full power at low line. Dixon example (200 W, 80 Vrms):
- Peak current at full power: Iin * (Vin + Vo)/Vo = 3.55 * (113+200)/200 = 5.55 A
- With HF ripple margin: set limit at ~7 A
- The inductor must be designed not to saturate at this current limit

**Constant-power loads** (downstream converters) must use undervoltage lockout on Vo to prevent latch-up during startup with foldback limiting.

### 11.7 Bulk Capacitor and Output Voltage Selection

**Output voltage choice trade-offs:**
- Lower Vo (100-150 V for 120 V input): lower switch/rectifier voltage stress (300-350 V peak)
- Higher Vo: smaller, cheaper bulk capacitor (C*V tends constant for a given case size; energy storage E = 0.5*C*V^2 favors higher voltage)
- For 120/220 V input: Vo = 200 V is a practical choice

**Bulk capacitor sizing:** For a given case size, C*V is approximately constant. A 200 V rated cap has 2x the capacitance of a 400 V cap in the same case, but at 200 V you need 4x the capacitance for the same energy storage as at 400 V. Net: 2x more case volume at 200 V vs 400 V for the same stored energy.

## Unified Three-Terminal Switch Model for CPM (from Yan VT 2012)

Source: Y. Yan, "Unified Three-terminal Switch Model for Current Mode Controls," MS thesis, Virginia Tech, 2012 (2010 defense). Advisor: Fred C. Lee.

### Background: Limitations of Prior Models

Yan provides a concise and critical review of all major CPM models, identifying what each can and cannot do:

| Model | Strengths | Weaknesses |
|-------|-----------|------------|
| Current-source model | Simplest, gives physical intuition | Cannot predict subharmonic oscillation or audio susceptibility |
| Average model (Middlebrook, Vorperian) | Good low-frequency prediction | Cannot predict subharmonic oscillation; no high-frequency accuracy |
| Discrete-time model (Packard, Brown) | Accurately predicts subharmonic instability | Hard to use in continuous-domain design; limited to constant-frequency |
| Ridley modified average model (1991) | Accurate to f_sw/2; simple He(s) insertion | Only validated for constant-frequency (PCM/VCM). Extension to variable-frequency is inaccurate |
| Tan/Middlebrook model | Alternative pole placement | Same limitations as Ridley for variable frequency |
| Li/Lee continuous-time model (describing function) | Accurate for both constant and variable frequency modulation; valid beyond f_sw | Mathematically very complex; existing equivalent circuit only for Buck, not complete (no input current) |

**Key gap filled by Yan:** The Li/Lee describing-function model (2009) was accurate but too complex for practical use, and only had an equivalent circuit for the Buck converter that was incomplete (missing input current terminal). No equivalent circuit existed for Boost, Buck-boost, or other topologies.

### The Unified Three-Terminal Switch Model

**Invariant sub-circuit observation:** In all current-mode-controlled converters (Buck, Boost, Buck-boost, Flyback, Forward, etc.), the same sub-circuit appears: active switch + passive switch + inductor + closed current loop. The three terminals are designated a (active), p (passive), c (common, where the inductor connects). This is the same three-terminal switch from the Vorperian PWM switch model, but with the current feedback loop absorbed into it.

**Key relationship preserved:** The average relationship between terminal currents:

```
i_a_hat = D * i_c_hat + I_c * d_hat
```

This average-model relationship is shown (via SIMPLIS verification) to remain valid up to f_sw/2 for both constant-frequency and variable-frequency current mode control. This is the foundation: if the three-terminal model correctly captures i_c (inductor current) and i_a (active switch current), then i_p (passive switch current) is automatically correct by KCL.

**Three-terminal equivalent circuit construction:**
1. Start from the Li/Lee equivalent circuit for the Buck (which correctly models i_c under v_c and v_in perturbation, but lacks i_a information).
2. Add a DC transformer with turns ratio D between terminals a-p and the internal circuit.
3. Derive the missing I_c * d_hat term using the describing-function result for d_hat as a function of v_c, v_ap, and v_cp.
4. Express I_c * d_hat in terms of circuit voltages v_L, v_ap, v_cp through algebraic rearrangement.
5. Add three branches between terminals a and p: a conductance G_L * v_L, a conductance G_cp * v_cp, and a resistor R_ap.

**Result:** A three-terminal equivalent circuit that:
- Is topology-independent (works for Buck, Boost, Buck-boost, Flyback, Forward, and any topology using the same three-terminal switch structure).
- Is modulation-independent (works for peak current mode, valley current mode, charge control, constant on-time, and constant off-time).
- Correctly represents control-to-output, line-to-output, output impedance, AND input current properties.
- Is accurate up to f_sw/2.

### Equivalent Circuit Parameters

The three-terminal model parameters for different modulations:

**Common elements:**
- DC transformer ratio: D (steady-state duty cycle)
- L_s: power stage inductor (physical component, appears in the model)
- C_e: equivalent capacitor creating the high-frequency double pole with L_s
- R_e: equivalent resistance providing damping of the double pole
- K_in: input voltage feedforward coefficient
- G_L, G_cp, R_ap: three branches for switch current reconstruction

**Peak current mode control:**

```
C_e = L_s * T_sw^2 / (pi^2)    [equivalent capacitance for double poles at f_sw/2]
R_e = L_s / (T_sw * (s_n - 0.5*(s_f + s_e))/(s_e + s_f))   [damping resistance]
K_ap = K_in * R_e / D - 1      [voltage source coefficient]
```

**Constant on-time control:**

```
C_e = T_on^2 / (pi^2 * L_s)
R_e = 2 * L_s / T_on
K_in = T_on / (L_s * D^2)
```

**Constant off-time control:**

```
C_e = T_off^2 / (pi^2 * L_s)
R_e = 2 * L_s / T_off
K_in = 0    [no input voltage feedforward -- inherent property of COT-off]
```

### Physical Interpretation

- **R_e splits the power-stage double poles:** The inductor pole (from L_s and C_o) moves to lower frequency (making the system first-order at low frequencies), and another pole moves to high frequency to pair with the C_e pole, forming double poles near f_sw/2.
- **When R_e goes negative (D > 0.5 without sufficient slope compensation):** The high-frequency double poles move to the right half plane, predicting subharmonic oscillation.
- **With large external ramp (s_e >> s_n, s_f):** R_e approaches zero, C_e becomes negligible, and the model reduces to the standard average model -- the current loop effect disappears and the power-stage filter double poles recover. This correctly captures the transition from current-mode to voltage-mode behavior as external ramp dominates.

### Comparison of CPM Implementations (from Yan Ch4)

Using the unified model, Yan compares constant-frequency (PCM) vs. variable-frequency (COT, COFF) implementations:

**Bandwidth extension:**
- **Constant on-time (COT):** Double poles are at 1/(2*T_on), not at f_sw/2. For D < 0.5, this is higher than f_sw/2, allowing wider control bandwidth than PCM at the same switching frequency.
- **Constant off-time (COFF):** Double poles are at 1/(2*T_off). For D > 0.5, this gives higher bandwidth than PCM.
- **Design rule:** Use COT for D < 0.5 applications (e.g., 12V-to-1V VR). Use COFF for D > 0.5 applications (e.g., PFC). Use PCM when D is near 0.5 or varies widely.

**Audio susceptibility:**
- PCM: finite audio susceptibility (nonzero G_vg), improved by slope compensation.
- COT: K_in is nonzero, providing inherent input voltage feedforward. Audio susceptibility is lower than PCM.
- COFF: K_in = 0, meaning zero inherent input feedforward. Audio susceptibility is the same as voltage mode at DC.

**Output impedance and AVP:**
- COT produces an output impedance that is purely resistive at low frequency (R_e in series with the inductor appears as a constant impedance). This naturally implements adaptive voltage positioning (AVP) without additional compensator design. The output impedance is:

```
Z_out(s) ~ R_i * L_s / (V_in * T_on)    [at low frequency, COT with no outer loop]
```

This is the reason COT current mode control is the dominant architecture in modern laptop/server VR applications.

### Extension to Multiphase (from Yan Ch5)

For N-phase interleaved current-mode control:
- Each phase has its own three-terminal switch model.
- Phase currents share through the common output capacitor.
- The per-phase current loop bandwidth sets the current-sharing bandwidth.
- With matched R_i (current sense gain) and matched slopes, current sharing is inherent.
- The multiphase model predicts that at frequencies above the current-sharing bandwidth, phases act independently, and the effective output impedance is 1/N of a single phase.

---

## Comprehensive Control Loop Design (from Basso, Artech House 2012)

Source: Christophe Basso, "Designing Control Loops for Linear and Switching Power Supplies: A Tutorial Guide," Artech House, 2012, 613 pages.

This dedicated control loop book significantly expands the treatment from Basso's SPICE book (Sections 1-8 above). Content below covers material NOT already present in the existing sections.

### 12.1 Advanced Stability Criteria (Ch3)

#### Modulus Margin

Phase margin and gain margin alone are insufficient to qualify system robustness. The **modulus margin** measures the shortest distance from the Nyquist curve to the critical point (-1, j0) at any frequency:

```
|h(w)| = sqrt([1 + Re(T(w))]^2 + [Im(T(w))]^2) = |1 + T(jw)|
```

This is the magnitude of the sensitivity function S(s) = 1/(1+T(s)). The minimum value of |h| over all frequencies is the modulus margin.

**Why it matters**: A system can have acceptable gain margin and phase margin yet still have the Nyquist curve passing dangerously close to the -1 point at some intermediate frequency. When |1+T| becomes small, the closed-loop gain 1/(1+T) peaks, amplifying disturbances rather than rejecting them. This situation is called **conditional stability**.

**Design target**: Modulus margin >= 0.5 (-6 dB), meaning the sensitivity function peak |S|_max <= 2 (6 dB). A common value in power electronics is |S|_max <= 3 (9.5 dB) as an acceptable limit. If the sensitivity function peaks above 6 dB, investigate and redesign compensation.

**Relationship to phase and gain margins**: The modulus margin combines phase and gain margin information into a single number. A poor modulus margin can occur even with adequate individual GM and PM values when the Nyquist curve "bulges" toward -1 between the gain and phase crossover frequencies.

#### Delay Margin

Phase margin can be expressed as an equivalent time delay tolerance:

```
t_delay = PM / (360 * f_c)
```

where PM is in degrees and f_c is the crossover frequency.

This **delay margin** represents the maximum additional delay that can be inserted in the loop before instability occurs. It is particularly relevant for digital control implementations where ADC conversion time, computation time, and PWM update latency introduce real delays.

**Delay in the Laplace domain**: A pure delay t_d maps to:

```
H_delay(s) = exp(-s * t_d)
```

On a Bode plot, the delay contributes zero magnitude change but adds a phase lag that increases linearly with frequency:

```
Phase_delay(f) = -360 * f * t_d    [degrees]
```

This linear phase drain is devastating at high frequencies. A 1 us delay at 100 kHz crossover adds -36 degrees of phase lag, potentially destroying the phase margin.

**Design rule**: For a switching converter with crossover frequency f_c, the total loop delay (sampling, computation, PWM update) must satisfy:

```
t_delay < PM_target / (360 * f_c)
```

For PM = 60 degrees at f_c = 50 kHz: t_delay < 3.3 us.

#### Conditional Stability

A system is **conditionally stable** when the open-loop gain |T(f)| > 0 dB at frequencies where the phase approaches -180 degrees, even though the final crossover has adequate phase margin. The k-factor method can inadvertently create this condition in CCM converters because it optimizes compensation only at the crossover frequency without considering the full phase profile.

**Detection**: Check the complete Bode plot -- not just the crossover point. If |T(f)| > 0 dB at any frequency where the phase is within 10-15 degrees of -180 degrees, the design has conditional stability risk.

**Practical consequence**: Under component tolerance variation (ESR aging, CTR degradation), the gain curve can shift, causing the phase to reach -180 degrees at a frequency where |T| > 0 dB. The system then oscillates even though nominal design had adequate phase margin.

### 12.2 Crossover Frequency Selection and Output Impedance (Ch3, Ch4)

#### Output Undershoot Formula

Basso derives a direct link between crossover frequency and load step response. For a step change delta_I in load current, the capacitive output voltage deviation (ignoring ESR) is approximately:

```
V_undershoot ~ delta_I / (2*pi*f_c*C_out)
```

This is valid when the ESR contribution (delta_I * ESR) is smaller than the capacitive term. If the ESR dominates, the undershoot is simply delta_I * ESR regardless of crossover frequency.

**Optimal crossover frequency** (from Chapter 3): To make the capacitive undershoot negligible compared to the unavoidable ESR-limited undershoot:

```
f_c >= 0.24 / (C_out * r_C)
```

where r_C is the output capacitor ESR. At this crossover, the total undershoot approaches the ESR floor: V_undershoot ~ delta_I * r_C.

**Example**: C_out = 1 mF, ESR = 20 mOhm: f_c >= 0.24/(1m * 20m) = 12 kHz.

#### Crossover Frequency Bounds for CCM Boost/Buck-Boost

Two constraints bound f_c for converters with RHPZ and resonant double pole:

**Upper bound** (RHPZ limit):
```
f_c < 0.3 * f_rhpz_min
```
The worst-case (lowest) RHPZ occurs at minimum input voltage and maximum load: f_rhpz = D'^2 * R / (2*pi*L).

**Lower bound** (resonant peak):
```
f_c > 3 * f_0_max
```
The resonant frequency f_0 must be below crossover so the loop gain provides damping of the LC resonance. The worst-case (highest) f_0 occurs at the input voltage that minimizes D, which is typically V_in_max.

If these two bounds conflict (upper < lower), increase C_out to push f_0 down.

#### Output Impedance Shaping (Ch4)

For applications requiring no overshoot/undershoot (e.g., VRM for CPUs), the compensator can be designed to make the closed-loop output impedance purely resistive equal to the ESR:

```
Z_out_CL(s) = Z_out_OL(s) / (1 + T(s)) = r_C    [target]
```

**Required compensator** (derived in Ch4):

```
G(s) = K_0 * (1 + s/w_Gz) / (1 + s/w_Gp)
```

where:
- K_0 = (r_L - r_C) / (H_0 * r_C) ~ 1/H_0 for r_L >> r_C
- w_Gz = compensator zero (at the effective filter corner)
- w_Gp = compensator pole (at the ESR zero frequency)

**Key difference from Type 1/2/3**: This compensator has NO origin pole (no integrator). The dc gain is finite. Consequence: the output voltage has a static error that depends on operating conditions. The target voltage is deliberately shifted by an offset to center the output within the tolerance band.

This technique is called **Adaptive Voltage Positioning (AVP)**. The output voltage drops by exactly delta_I * r_C under a load step, with zero ringing. The response is a perfect square wave.

**Implementation** (Ch4, Fig 4.64): Op amp with R1 input, R2 feedback, R3 + C1 network:

```
R2 = R1 / K_0
R3 = R1 * w_Gz / (K_0 * (w_Gp - w_Gz))
C1 = K_0 * (w_Gp - w_Gz) / (R1 * w_Gp * w_Gz)
```

**Limitation**: The low dc loop gain means poor line regulation. Only practical when the input supply is well regulated (e.g., 12V rail to CPU VR). Current-mode control with constant on-time (COT) provides inherent AVP without these limitations (see Section on Yan model above).

### 12.3 PID Compensator Implementation (Ch4)

Basso shows that a **filtered PID** is algebraically identical to a Type 3 compensator:

```
PID(s) = k_p + k_i/s + k_d*s/(1 + s/w_f)

= (k_i / s) * (1 + s/w_z1) * (1 + s/w_z2) / (1 + s/w_p)
```

where:
- w_z1, w_z2 = zeros from the PID numerator
- w_p = filter pole w_f on the derivative term
- k_i = integral gain (sets the origin pole/0-dB crossover)

**Mapping PID to Type 3 components**: Given an op-amp Type 3 circuit with (R1, R2, R3, C1, C2, C3):

```
k_p = R2/R1 + C1/C3 + R2*C1/(R3*C3) - 1
k_i = 1/(R1*C1) + 1/(R3*C3)
k_d = R2*C1/C3 * [1/(R1*C1) + 1/(R2*C1) + 1/(R3*C3)]^(-1)
```

**Practical PID tuning for power converters** (Basso method):
1. Select f_c and PM target
2. Set w_p = 2*pi*f_sw/2 (derivative filter at half switching frequency)
3. Place both zeros at the LC resonant frequency: w_z1 = w_z2 = w_0
4. Adjust k_i to set the crossover frequency
5. Verify PM; adjust zero positions if needed

**Ziegler-Nichols tuning is NOT recommended** for power converters -- it was designed for process control with very different dynamics. Use pole-zero placement or k-factor methods instead.

### 12.4 Op-Amp Compensator Configurations with Optocoupler (Ch5)

Chapter 5 provides exhaustive coverage of every practical op-amp + optocoupler configuration. The key configurations not already covered in Section 6 above:

#### Optocoupler Connections

Three connection methods for the optocoupler to the op-amp output:

1. **Direct connection, common emitter**: Optocoupler collector to controller pullup, emitter to ground. Op-amp output drives LED through R_LED. The CTR multiplied by R_pullup/R_LED sets the mid-band gain.

2. **Direct connection, common collector**: Optocoupler emitter to controller, collector to Vdd. Op-amp output drives LED through R_LED. The gain is CTR times R_pulldown/R_LED. Generally preferred when the controller has a pulldown resistor on FB.

3. **Pull-down with fast lane**: The optocoupler collector connects to the controller FB through a pullup, while R_LED connects directly from V_out to the LED anode (fast lane). This creates a minimum gain floor.

#### Fast Lane Problem and Solutions

The **fast lane** occurs when R_LED connects directly from V_out to the optocoupler LED anode. It creates a direct AC path that bypasses the compensator, imposing a minimum mid-band gain:

```
G_fast_lane = CTR * R_pullup / R_LED
```

This gain floor cannot be reduced by the compensator network and can prevent achieving desired crossover/phase margin targets.

**Solution 1: Disable the fast lane** by interposing a Zener diode or transistor between V_out and R_LED, blocking the AC signal from reaching the LED directly.

**Solution 2: Pull-down without fast lane** configuration -- R_LED connects from the op-amp output to the LED, not from V_out. This eliminates the direct AC path entirely.

**Solution 3: Accept the fast lane** and design around it. Use equations that account for G_fast in the mid-band gain calculation.

#### Dual-Loop CC-CV Applications (Ch5, Sec 5.5.10)

For constant-current / constant-voltage charger applications, two loops share a single optocoupler:

**Architecture**: Two op-amps on the secondary side:
- Op-amp 1: voltage error amplifier (compares V_out to voltage reference)
- Op-amp 2: current error amplifier (compares I_sense * R_sense to current reference)

Both outputs connect through diodes to a common node driving R_LED. Whichever loop demands the lower duty cycle dominates.

**Design procedure**:
1. Design the CV loop compensation first (Type 2 or Type 3 depending on topology)
2. Design the CC loop compensation independently (typically Type 2 for current-mode plants)
3. Cross-regulation occurs at the boundary -- verify both loops remain stable during the transition

**Component calculation (Basso design example, 12V/1A charger)**:
- CV loop: Type 2, f_c = 1 kHz, PM = 70 degrees
- CC loop: Type 2, f_c = 500 Hz, PM = 65 degrees
- The slower loop dominates at the crossover between modes

### 12.5 OTA-Based Compensators (Ch6)

Chapter 6 provides complete OTA compensator design, complementing the Maniktala treatment in Section 7 above. Key additions:

#### OTA Fundamentals

The OTA is a voltage-controlled current source:

```
I_out = g_m * (V+ - V-)
```

where g_m is the transconductance (units: Siemens or A/V). Typical g_m values: 100-800 uS.

The output voltage is set by the output current flowing through an external impedance Z_out:

```
V_out = I_out * Z_out = g_m * epsilon * Z_out
```

**Key difference from op amp**: No virtual ground. The inverting pin voltage can be used for additional functions (e.g., overvoltage detection in PFC controllers). Also, OTAs take much less die area, making them preferred for integration.

#### OTA Type 1

```
G(s) = -g_m / (s * C_1)
```

Single capacitor C_1 from COMP pin to ground. Pure integrator.

**Design**: 
```
C_1 = g_m / (2*pi*f_c * G_fc)
```
where G_fc is the required gain at crossover (= 1/|H(f_c)| to make |T(f_c)| = 1).

#### OTA Type 2

Impedance from COMP pin: R_1 in series with C_1 (zero), paralleled with C_2 (pole).

```
G(s) = -g_m * Z_out(s)
     = -g_m * (1 + s*R_1*C_1) / (s*C_1*(1 + s*R_1*C_1*C_2/(C_1+C_2)))
```

For C_2 << C_1 (typical):
```
f_z = 1 / (2*pi*R_1*C_1)
f_p = 1 / (2*pi*R_1*C_2)    [approximately, when C_2 << C_1]
```

**Design equations** (Basso method):
```
C_1 = g_m / (2*pi*f_c*G_fc*k)
R_1 = k / (2*pi*f_c*C_1)
C_2 = C_1 / (k^2 - 1)
```
where k = tan(boost/2 + 45 degrees) and boost = PM - phase_plant(f_c) - 90 degrees.

#### OTA Type 3

Achieved by adding a feedforward capacitor C_ff from V_out divider midpoint to the COMP pin. This introduces a second zero-pole pair through the voltage divider network.

Transfer function (simplified):
```
G(s) = -g_m * [Z_out(s)] * [1 + s*C_ff*R_lower*R_upper/(R_lower+R_upper)] / [1 + s*C_ff*R_lower]
```

This creates an additional zero at 1/(2*pi*C_ff*(R_lower||R_upper)) and pole at 1/(2*pi*C_ff*R_lower).

**Limitation**: The zero and pole from C_ff are linked through the divider ratio -- they cannot be positioned independently. This constrains the achievable phase boost compared to an op-amp Type 3.

#### OTA with Optocoupler (Ch6, Sec 6.3)

For isolated converters using an OTA on the controller IC, a buffered connection through the optocoupler is necessary:

```
G_total = g_m * Z_out * CTR * R_pullup / R_LED
```

The optocoupler parasitic capacitance C_opto adds in parallel with C_2 (the high-frequency pole capacitor). Subtract C_opto from the calculated C_2 value:

```
C_2_installed = C_2_calculated - C_opto
```

If C_opto > C_2_calculated, reduce f_c or reduce R_pullup to push the optocoupler pole higher.

### 12.6 TL431 Advanced Techniques (Ch7)

Chapter 7 provides the most comprehensive TL431 compensator treatment available, significantly expanding Section 6 above.

#### TL431 Internal Structure and Biasing

The TL431 is an open-collector op-amp with a 2.5V bandgap reference. Key operating requirements:

- **Minimum cathode current (I_ka)**: 1 mA for guaranteed performance. Below this, the internal op-amp gain degrades and the reference voltage drifts.
- **Minimum V_ka**: Typically 2.5V (the reference voltage itself). The device cannot regulate if V_ka drops below V_ref.
- **Internal gain**: The TL431 has finite open-loop gain (typically 55-70 dB) that decreases with frequency. At high frequencies, the TL431 output impedance increases, reducing the compensator gain.

#### Biasing Impact on Gain (Ch7, Sec 7.2-7.4)

The TL431 cathode bias current directly affects the loop gain. The bias current is set by:

```
I_ka = I_pullup - I_LED/CTR
```

where I_pullup = V_dd/R_pullup is the primary-side current and I_LED is the optocoupler LED current.

**Worst case**: Maximum CTR drives minimum LED current, which in turn minimizes I_ka. If I_ka drops below 1 mA, the TL431 gain degrades and the compensator transfer function departs from the designed values.

**Practical solution**: Add a bias resistor R_bias from V_out to TL431 cathode (through R_LED path) to guarantee minimum I_ka:

```
R_bias = V_LED / I_bias_target    (where V_LED ~ 1V, I_bias >= 1 mA)
```

This steals current from the AC signal path, reducing the effective CTR by the ratio R_bias/(R_bias + R_LED).

#### Disabling the Fast Lane (Ch7, Sec 7.5-7.6)

The fast lane creates a gain floor that limits compensator freedom. Three methods to disable it:

**Method 1: Zener diode** in series with R_LED between V_out and the LED anode. The Zener blocks AC variations on V_out from reaching the LED. Only the op-amp-driven current through the TL431 modulates the LED.

**Method 2: Transistor clamp** (NPN) with base connected to a stable reference. The transistor replaces R_LED and provides constant current to the LED regardless of V_out variations.

**Method 3: Separate bias supply** -- power the LED from a regulated auxiliary supply instead of V_out.

With the fast lane disabled, the compensator gain is no longer bounded by G_fast = CTR * R_pullup / R_LED, and you have full freedom in pole-zero placement.

#### TL431 Type 2: All Configurations

**Common emitter (standard)**: Collector to R_pullup to V_dd. Transfer function:

```
G(s) = -CTR * R_pullup * (1 + s*R_upper*C_zero) / (R_LED * s*R_upper*C_zero * (1 + s*R_pullup*(C_pole + C_opto)))
```

Component design:
```
C_zero = k / (2*pi*f_c*R_upper)
C_pole = 1/(2*pi*f_c*k*R_pullup) - C_opto
R_LED = CTR * R_pullup / G_mid    [where G_mid = desired mid-band gain]
```

**Common collector**: Emitter to R_pulldown to ground. Similar transfer function but with R_pulldown replacing R_pullup.

**With UC384X controller**: The controller has an internal pull-up (typically 5 kOhm to 5V). The optocoupler connects to the COMP pin. The internal pull-up sets R_pullup. Add external capacitor to set the pole frequency, accounting for the internal compensation capacitor already present.

**Without fast lane**: When the fast lane is disabled (Zener, transistor, or separate bias):

```
G(s) = -g_TL431 * R_upper * (1 + s*R_upper*C_zero) / (s*R_upper*C_zero)
       * CTR * R_pullup / (R_pullup + 1/g_TL431)
       * 1 / (1 + s*R_pullup*C_pole)
```

where g_TL431 is the TL431 transconductance (internal op-amp gain * 1/r_dynamic).

#### TL431 Type 3: Complete Treatment

**With fast lane** (standard configuration): Add R_pz + C_pz network in parallel with R_LED:

```
Z_LED(s) = R_LED || (R_pz + 1/(s*C_pz))
```

This creates a second zero-pole pair. The full transfer function:

```
G(s) = -CTR * R_pullup / Z_LED(s) * (1 + s*R_upper*C_zero) / (s*R_upper*C_zero)
       * 1 / (1 + s*R_pullup*C_pole)
```

**Design procedure** (Basso step-by-step):
1. Calculate required mid-band gain: G_mid = 10^(G_dB/20) where G_dB compensates plant gain at f_c
2. R_LED = CTR * R_pullup / G_mid
3. From k-factor: k = tan(boost/4 + 45 degrees)^2 [Type 3 uses k^2]
4. C_zero = 1/(2*pi*f_z1*R_upper), where f_z1 = f_c/k
5. C_pole = 1/(2*pi*f_p1*R_pullup) - C_opto, where f_p1 = f_c*k
6. For the second zero-pole pair through R_pz/C_pz:
   - f_z2 = f_c/k (same as f_z1 for coincident zeros)
   - f_p2 = f_c*k (same as f_p1)
   - C_pz = k/(2*pi*f_c*R_pz)
   - R_pz is extracted from the constraint that Z_LED provides the correct zero/pole positions

**Without fast lane**: When the fast lane is disabled, the second zero-pole pair must be created differently. The R_pz/C_pz network is placed in the TL431 feedback network (R_upper side) rather than across R_LED. The equations simplify considerably.

**Design example values** (19V/4A flyback, Basso Ch7):
- f_c = 5 kHz, PM = 70 degrees, boost = 70 - (-86) - 90 = 66 degrees
- k = 2.44, f_z = 2.05 kHz, f_p = 12.2 kHz
- R_upper = 66 kOhm (for 250 uA divider current)
- R_pullup = 4.7 kOhm, CTR = 1.0
- C_zero = 1.18 nF, C_pole = 2.7 nF
- R_LED calculated from gain requirement

#### TL431 Bench Testing (Ch7, Sec 7.15)

To verify TL431 compensator AC response on a prototype:

1. Use a network analyzer (AP300, Bode 100, etc.)
2. Inject the AC signal at the optocoupler collector (between the collector and R_pullup)
3. Probe V_out for the input signal and V_collector for the output signal
4. The ratio gives the compensator G(s) transfer function
5. Compare measured G(s) with theoretical -- discrepancies reveal incorrect optocoupler parameters or unmodeled parasitics

### 12.7 Shunt Regulator Compensators (Ch8)

This is entirely new material not covered elsewhere in this file.

#### Architecture (TOPSwitch and similar)

Some integrated switchers (Power Integrations TOPSwitch, etc.) combine the Vcc pin and feedback pin into a single input. The duty ratio is controlled by the current injected into the FB pin:

- Near-zero injected current: maximum duty ratio (~67%)
- 6 mA or more: minimum duty ratio (~1.8%)
- Internal dynamic resistance R_d ~ 15 ohm on the FB pin

An internal filter capacitor C_s (typically a few nF) is already present on the FB pin.

#### Shunt Regulator Type 2

Transfer function from V_out to the FB pin current (and hence duty ratio):

```
G(s) = CTR * R_d / R_LED * (1 + s*R_upper*C_zero) / (s*R_upper*C_zero * (1 + s*R_d*(C_s + C_pole)))
```

**Design equations**:
```
R_LED = CTR * R_d / G_mid    [mid-band gain]
C_zero = k / (2*pi*f_c*R_upper)    [integrator zero]
C_pole = 1/(2*pi*f_c*k*R_d) - C_s    [HF pole, subtract internal cap]
```

Note: R_d is very small (~15 ohm), so the HF pole is at high frequency. The internal C_s may already provide sufficient HF rolloff.

#### Shunt Regulator Type 3

Same architecture as Type 2 plus R_pz/C_pz network across R_LED for the second zero-pole pair. The transfer function and design procedure mirror the TL431 Type 3, with R_d replacing R_pullup.

#### Isolated Zener-Based Shunt Compensator

For the lowest-cost designs, the TL431 is replaced with a simple Zener diode:

```
G(s) = CTR * R_d / R_LED * (1 + s*R_upper*C_zero) / (s*R_upper*C_zero * (1 + s*R_d*C_pole))
```

The Zener has no internal op-amp -- no slow lane exists. The AC signal passes entirely through the fast lane (R_LED to LED). This limits compensation to Type 1 or Type 2 only.

**Component selection**:
- Zener voltage: V_z = V_out - V_LED - V_CE,sat (for series NPN) or V_z ~ V_out - V_LED (direct connection)
- R_LED sets the LED bias current and the AC gain simultaneously
- Add R_bias across the Zener to set a minimum operating current for temperature stability

### 12.8 Practical Loop Gain Measurement (Ch9)

#### Opening the Loop Without Bias Point Loss

The standard method: insert a large inductor L_oL and large capacitor C_oL at the loop-breaking point:

- L_oL (e.g., 1 GH): shorts in DC (maintains bias), open in AC (breaks loop)
- C_oL (e.g., 1 GF): open in DC, short in AC (passes the AC injection signal)

**Injection point selection**: Break the loop at a point where:
1. Source impedance (looking back) is LOW
2. Load impedance (looking forward) is HIGH

The most common point: between the compensator output and the PWM modulator input (or after the optocoupler collector, before the FB pin).

**Incorrect injection point**: If source and load impedances are comparable, the measured loop gain is corrupted. Middlebrook showed that the true loop gain is:

```
T(s) = T_measured(s) * (1 + Z_s/Z_l)
```

where Z_s and Z_l are the source and load impedances at the injection point. The error is negligible only when Z_s << Z_l.

#### Voltage Variations at Injection Points (Ch9, Sec 9.1.4)

When measuring loop gain by voltage injection, the AC test signal amplitude at the injection point matters:

- **Too large**: drives the converter into nonlinear operation (clipping, saturation, current limiting). The measured transfer function is invalid.
- **Too small**: signal-to-noise ratio is poor, especially at frequencies where the loop gain is large.

**Practical guidelines**:
- Use 20-50 mVpp injection amplitude at frequencies where |T| >> 1
- Increase to 100-200 mVpp at frequencies near and above crossover where |T| ~ 1
- Many network analyzers can use swept amplitude (higher amplitude at HF)
- Verify linearity by repeating the measurement at half amplitude -- results should be identical

#### Impedance at Injection Points

At the injection point, the impedances looking in both directions determine measurement accuracy:

```
V_A = V_inject * Z_l / (Z_s + Z_l)    [signal at load side]
V_B = V_inject * Z_s / (Z_s + Z_l)    [signal at source side]
```

The loop gain is T = V_A/V_B = Z_l/Z_s (ideally).

If Z_s is not negligible: use a buffer (op-amp voltage follower) between the compensator and injection point to ensure low source impedance.

### 12.9 Design Examples (Ch9)

#### Linear Regulator Compensation

**NPN series-pass (non-LDO)**:
- Plant: common-collector output stage. The power stage has gain < 1 (loss, not gain). Phase lag is small (~34 degrees at 12 kHz).
- Very little phase boost needed. A Type 1 (pure integrator) or Type 2 with coincident pole-zero is sufficient.
- Design formula: f_c = 0.24/(C_out * ESR) for ESR-limited undershoot.

**P-channel MOSFET LDO**:
- Plant: common-source output stage. The power stage has gain > 1 AND introduces more phase lag (~100 degrees at 12 kHz).
- Requires Type 2 compensation with significant phase boost (70 degrees in the example).
- The LDO output pole depends on the load: f_p = 1/(2*pi*R_load*C_out). At light load, R_load increases, and the pole moves to lower frequency. Verify stability at both full load and light load extremes.

**Key differences from switching converter compensation**:
- No switching frequency limit on crossover
- No subharmonic instability
- No RHPZ
- The pass element acts as a variable resistor, not a switch -- no averaged model needed

#### CCM Voltage-Mode Boost Converter (Ch9, Sec 9.4)

Complete worked example: 12V battery to 19V, 3A, 100 kHz.

**Plant parameters at V_in = 11.5V (worst case)**:
- D = 0.395
- f_0 = 430.8 Hz (LC resonance)
- Q = 7.57 (17.6 dB -- strong peaking)
- f_z1 = 7.9 kHz (ESR zero)
- f_z2 = 7.4 kHz (RHPZ)
- H_0 = 23.9 dB (DC gain)

**Crossover constraints**:
- f_c < 0.3 * 7.4k = 2.2 kHz (RHPZ limit)
- f_c > 3 * 562 = 1.68 kHz (resonance limit at V_in_max = 15V where f_0 is highest)
- Selected: f_c = 2 kHz

**Two compensation strategies compared**:

| Parameter | Strategy 1 | Strategy 2 |
|-----------|-----------|-----------|
| Double zero position | At f_0 (430 Hz) | Below f_0 (300 Hz) |
| First pole | At ESR zero (7.9 kHz) | Calculated for target PM (9.9 kHz) |
| Second pole | f_sw/2 (50 kHz) | f_sw/2 (50 kHz) |
| Phase margin (11.5V) | 50 degrees | 60 degrees |
| Low-frequency gain | Higher | Lower |
| PM vs temperature | Fails at high temp | Passes at high temp |

**Strategy 2 is preferred** because it maintains adequate PM over ESR temperature variations:

| Temp (C) | ESR (mOhm) | PM Strategy 1 | PM Strategy 2 |
|----------|-----------|--------------|--------------|
| 0 | 40 | 62 degrees | 72 degrees |
| 25 | 20 | 50 degrees | 60 degrees |
| 70 | 10 | 43 degrees | 53 degrees |

Strategy 1 fails the 45-degree minimum at 70C.

**Pole position calculation (Strategy 2)**:

```
f_p1 = f_c / tan[boost - 2*atan(f_c/f_z) - atan(f_c/f_p2)]
```

This is derived by inverting the Type 3 phase equation at crossover.

**CCM-to-DCM transition**: At light load, the converter enters DCM. The transfer function changes to a single-pole system:

```
H_DCM(s) = H_0_DCM * (1 + s/w_z1) / (1 + s/w_p1)
```

The compensator designed for CCM must also provide adequate PM in DCM. Verify by combining the CCM compensator with the DCM plant transfer function and checking PM at both V_in extremes.

#### Primary-Regulated Flyback Without Optocoupler (Ch9, Sec 9.5)

For low-power (<10W) applications where optocouplers are eliminated for cost/reliability:

**Architecture**: Auxiliary winding voltage Vaux is monitored. An NPN transistor (Q1) with base driven through a Zener diode from Vaux pulls down the controller feedback pin.

**Compensator transfer function** (derived using fast analytical techniques):

```
G(s) = -beta * R_FB / (r_d + R_1 + r_pi) * (1 - s/(beta/(C_b*r_pi))) / (1 + s*tau_p)
```

where:
- beta = transistor current gain (~70-100)
- r_pi = transistor base-emitter dynamic resistance (~21 kOhm)
- r_d = Zener dynamic resistance (~10 kOhm)
- R_1 = Zener series resistor (~1 kOhm)
- R_FB = pull-up resistor (~20 kOhm)
- C_b = base-collector capacitor (the only compensation element)

**Key features**:
- Contains a **RHP zero** at f_z = beta/(2*pi*C_b*r_pi). Typically at very high frequency (>500 kHz) -- negligible influence below 10 kHz crossover.
- Only ONE adjustable element (C_b) to set the pole position.
- DC gain G_0 = beta * R_FB / (r_d + R_1 + r_pi). Example: G_0 = 45.6 (33 dB).
- No origin pole: this is a first-order roll-off compensator, not an integrator-based design.

**Design calculation**:
```
f_p = f_c^2 / sqrt(G_0^2 - (f_c/G_0)^2)    [approximate]
C_b = 1 / (2*pi*f_p*R_eq)
```

where R_eq is the equivalent resistance driving C_b (from the impedance analysis at the capacitor terminals).

Example: f_p = 230 Hz, R_eq = 595 kOhm, C_b = 1.2 nF, achieving f_c ~ 2.3 kHz with PM ~ 50 degrees.

**Adding R_B across base-emitter**: Increases Zener operating current for better temperature stability. Modifies G_0:

```
G_0 = R_FB / (R_B || r_pi + (r_d + R_1)||R_B / beta)
```

#### Input Filter Interaction and Damping (Ch9, Sec 9.6)

**Negative incremental input resistance**: A closed-loop converter delivering constant power has:

```
R_in,inc = -V_in^2 / P_out
```

This negative resistance can destabilize an LC input filter.

**Instability mechanism**: The undamped input filter has a quality factor Q. If the negative resistance cancels the positive filter damping, the quality factor goes to infinity and oscillations are sustained. If the negative resistance exceeds the positive damping, the quality factor becomes imaginary (poles move to RHP) and the output diverges.

**Stability criterion** (Middlebrook):

```
|Z_out_filter(f)| << |Z_in_converter(f)|    for all f up to crossover
```

In practice: the damped filter output impedance must stay well below the negative incremental resistance magnitude at all frequencies.

**Damping design** (RC parallel damping network across filter capacitor):

Quality factor with damping:
```
Q = R_in * R_damp / (R_in + R_damp) * sqrt(C/L)
```

Solving for R_damp with Q = 1:
```
R_damp = |R_in| * sqrt(L/C) / (|R_in| - sqrt(L/C))
```

The damping capacitor C_damp (in series with R_damp, for DC blocking) should be >= 10x the filter capacitor C for effective damping.

**Example** (5V/30A, 100V DC input, 95% efficiency):
- R_in = -V_in^2*eta/P_out = -63 ohm
- L = 150 uH, C = 10 uF
- R_damp = 3.65 ohm
- C_damp = 100 uF (10x C)
- Verify: plot Z_out_filter with damping and confirm it stays below 36 dBohm (= 63 ohm) at all frequencies.

### 12.10 Second-Stage LC Filter Effects on Compensation (App 7B)

When an LC post-filter is added at the output (common in flyback converters for spike reduction), it affects the TL431 compensator transfer function:

**Modified transfer function** (TL431 Type 2 with post LC filter):

```
G_with_filter(s) = G_compensator(s) * H_filter(s)
```

where:
```
H_filter(s) = 1 / (1 + s/(Q_f*w_f) + (s/w_f)^2)
```

and w_f = resonant frequency of the post filter, Q_f depends on ESR and load.

**Design rule**: Keep the post-filter resonant frequency at least 10x above the compensator zero frequency (the integrator zero in the Type 2 or Type 3).

If the resonance approaches the zero region, the compensator transfer function distorts and the phase becomes uncontrollable. Instability results.

**Practical guidelines**:
- Inductor L_filter: keep below 4.7 uH (small value ensures minimal impedance addition and low voltage undershoot from di/dt)
- Capacitor C_filter: chosen for desired resonant frequency (f_filter >> f_zero)
- The TL431 fast lane (R_LED) must connect BEFORE the LC filter (on the power stage side), not after. If R_LED connects after the filter, the LED current sees the filter's phase/amplitude distortion.
- Example: With f_zero = 800 Hz, filter at 10 kHz causes no loop distortion. Filter at 1 kHz causes instability.
- Loop gain measurement with the LC filter requires probing at the optocoupler collector to naturally combine both signal paths

### 12.11 Optocoupler Characterization and Parasitics (App 5C)

#### Optocoupler Pole Extraction

The optocoupler parasitic collector-emitter capacitance C_opto creates a pole:

```
f_opto = 1 / (2*pi*R_pullup*C_opto)
```

This pole directly affects the compensator. With a 4.7 kOhm pullup, a typical C_opto of 3.4 nF gives f_opto = 10 kHz. Increasing R_pullup to 15 kOhm drops the pole to 3 kHz.

**Bench measurement of f_opto**:
1. Bias the optocoupler in a common-emitter configuration
2. Set V_CE ~ V_dd/2 for maximum linear range
3. AC sweep the LED current using a network analyzer
4. Find the -3 dB point from the low-frequency gain plateau
5. The -3 dB frequency is f_opto

**Without a network analyzer**: Use an oscilloscope and function generator:
1. Set up a 100 Hz sine on the LED, observe collector voltage
2. Note the peak-to-peak amplitude (reference)
3. Increase frequency until amplitude drops to 70.7% of reference (= -3 dB)
4. That frequency is f_opto

#### LED Dynamic Resistance Impact

The LED forward voltage V_f creates a dynamic resistance R_d = dV_f/dI_f that varies with operating current:

- At I_F = 1 mA: R_d ~ 40 ohm (typical)
- At I_F = 300 uA: R_d ~ 160 ohm (typical)

In the gain equation, R_d appears in series with R_LED:

```
G_actual = CTR * R_pullup * R_bias / ((R_LED + R_d) * (R_bias + R_d) + R_LED * R_d)
```

For R_d = 0 (ideal): G = CTR * R_pullup / R_LED.
For R_d = 160 ohm (low bias): gain drops by up to 7 dB compared to the R_d=0 calculation.

A 7 dB gain error shifts the crossover frequency by a factor of 2.2: a 1 kHz target becomes ~450 Hz.

**Bias resistor R_bias effect**: R_bias (typically 1 kOhm, across LED for TL431 minimum current) diverts AC current from the LED, further reducing gain:

```
G_chain = CTR * R_pullup / (R_LED + R_d) * R_bias / (R_bias + R_d)
```

#### Design Guidelines for Optocouplers

| Goal | Recommendation |
|------|---------------|
| High bandwidth | Low R_pullup (1 kOhm), low CTR device (smaller transistor area = smaller C_opto) |
| Low standby power | High R_pullup (10-20 kOhm), accept lower bandwidth |
| Production robustness | Design for minimum CTR (worst case), verify at maximum CTR |
| Noise immunity | Always install >= 100 pF capacitor on FB pin close to controller |
| Long lifetime | Operate LED at low current (< 5 mA); LED photon output degrades with age |
| Minimum C_opto devices | PC817, SFH615 series; avoid high-CTR devices (large transistor area = large C_opto) |

### 12.12 Quality Factor Extraction from Group Delay (App 4B)

When the resonant peak is too flat to measure Q from the magnitude plot, the **group delay** provides an alternative:

```
tau_g = -d(phase)/d(omega)    [seconds]
```

At the resonant frequency w_0, the group delay peaks. For a second-order system:

```
tau_g(w_0) = 2*Q / w_0
```

Therefore:
```
Q = tau_g(w_0) * w_0 / 2 = pi * tau_g(w_0) * f_0
```

**Procedure**:
1. Run AC sweep and capture the phase data
2. Compute the group delay as the numerical derivative of phase vs. frequency
3. Find the peak group delay value and the frequency at which it occurs
4. Apply Q = pi * tau_g_peak * f_peak

This is especially useful for measuring the quality factor of converter plants from SPICE simulations where the magnitude peak is barely visible but the phase transition is sharp.

### 12.13 Phase Display Correction in Simulators (App 4C)

SPICE simulators and mathematical tools can display confusing phase plots due to arctangent branch cuts and wrapping. Key issues:

1. **Phase wrapping at +/-180 degrees**: Many tools display phase in the range [-180, +180] or [0, -360], causing apparent discontinuities at -180 degrees. This can mask the true continuous phase rotation.

2. **Multiple-pole systems**: A system with many poles may accumulate phase lag well beyond -360 degrees. Simulators that wrap the phase make it impossible to assess total phase rotation.

3. **Correction**: Most modern tools (LTspice, PSIM, Mathcad) can be configured for unwrapped phase display. In SPICE: use the `.MEAS` directive to extract phase at specific frequencies, or post-process the raw data.

4. **Phase inversion ambiguity**: The op-amp in an inverting configuration contributes 180 degrees of phase shift. Some textbooks absorb this into the plant; others show it explicitly. Be consistent.

### 12.14 Open-Loop Gain and Origin Pole Effects in Op-Amp Compensators (App 4D)

Real op-amps have finite open-loop gain A_OL and an internal dominant pole f_p_OL:

```
A_OL(s) = A_0 / (1 + s/w_p_OL)
```

Typical values: A_0 = 100 dB (100,000), f_p_OL = 10 Hz, unity-gain bandwidth f_t = A_0 * f_p_OL ~ 1 MHz.

**Impact on compensator transfer function**: The compensator gain cannot exceed A_OL at any frequency. Near the unity-gain bandwidth f_t, the actual gain rolls off and departs from the ideal RC-derived transfer function.

**Practical consequence**: If the compensator requires high gain at frequencies approaching f_t (e.g., a Type 3 with gain > 40 dB at 100 kHz for a 1 MHz GBW op-amp), the actual response will deviate. Select an op-amp with GBW at least 10x above the highest frequency where significant compensator gain is needed.

**The origin pole interaction**: When the compensator has an origin pole (integrator), and the op-amp has its own internal pole, two cascaded poles exist at low frequency. If they are too close, the effective pole is not where you designed it. In practice, the op-amp internal pole is so low (< 100 Hz for general-purpose types) that it only affects the very-low-frequency gain, not the crossover behavior.

### 12.15 Summary of All Compensator Configurations (from App 4E, 5A, 6A, 7A, 8A)

Basso provides comprehensive summary tables of every compensator configuration. The key architectures and their applicable situations:

| Architecture | Compensator Types | Best For |
|---|---|---|
| Op-amp (non-isolated) | Type 1, 2, 2a, 2b, 3 | Non-isolated DC-DC converters, linear regulators |
| Op-amp + optocoupler (common emitter) | Type 1, 2, 3 | Isolated flyback, forward converters (standard) |
| Op-amp + optocoupler (common collector) | Type 1, 2, 3 | When controller has pulldown on FB |
| Op-amp + optocoupler + UC384X | Type 2, 3 | With UC384X family controllers |
| Op-amp + optocoupler (no fast lane) | Type 2, 3 | When fast lane gain is too high |
| OTA (non-isolated) | Type 1, 2, 3 | PFC controllers, integrated solutions |
| OTA + optocoupler | Type 1, 2, 3 | Isolated converters with OTA controller |
| TL431 + optocoupler (common emitter) | Type 1, 2, 3 | Consumer AC-DC adapters (most common) |
| TL431 + optocoupler (common collector) | Type 1, 2, 3 | Alternative to common emitter |
| TL431 + optocoupler (no fast lane) | Type 2, 3 | When fast lane limits gain adjustment |
| TL431 + optocoupler + UC384X | Type 2, 3 | UC384X-based designs |
| Shunt regulator (TOPSwitch) | Type 2, 3 | Power Integrations switcher ICs |
| Zener + optocoupler | Type 1, 2 | Lowest cost, limited compensation |
| Transistor + Zener (no optocoupler) | First-order | Primary-regulated flyback (<10W) |

**Selection guideline**: Start with TL431 + optocoupler for any isolated design. Use op-amp only when TL431 configuration cannot achieve the required pole-zero placement. Use OTA when the controller IC dictates it. Use shunt regulator configurations for TOPSwitch and similar ICs.

## Ridley Practical Loop Design

Sources:
- Ridley, "A New Small-Signal Model for Current-Mode Control" (PhD dissertation, VPI, 1990; updated 2018)
- Ridley, "Current-Mode Control Modeling" (Switching Power Magazine, 2006)
- Ridley, "Designing with the TL431" (Switching Power Magazine, Designer Series XV, 2005)
- Ridley, "Flyback Snubber Design" (Switching Power Magazine, Designer Series XII, 2005)
- Ridley, "Six Common Reasons for Power Supply Instability" (Switching Power Magazine, 2006)

---

### 13.1 Ridley Current-Mode Model (Enhanced Single-Pole with Double-Pole Extension)

The standard single-pole current-source model (inductor as controlled current source feeding RC) works for most designs but cannot predict the subharmonic oscillation phenomenon. Ridley's model adds a high-frequency correction term -- a pair of complex poles at half the switching frequency -- that unifies oscillation prediction, ramp selection, and control transfer function accuracy into one model.

#### Control-to-output: enhanced model

For all converter types, the standard dominant-pole transfer function is multiplied by a high-frequency correction term:

```
G_cpm(s) = G_low(s) * 1 / [(s/w_n)^2 + s/(w_n * Qp) + 1]
```

where:
- w_n = pi * f_sw (double pole at half the switching frequency)
- Qp = damping quality factor (see below)

#### Double-pole quality factor Qp

```
Qp = 1 / [pi * (mc * D' - 0.5)]
```

where:
- mc = 1 + Se/Sn (ramp compensation factor)
- Se = slope of external compensation ramp
- Sn = Ri * Von / L (slope of sensed inductor current during on-time)
- D' = 1 - D

**Critical insight**: Without external ramp (mc = 1), the Qp expression shows that oscillation can begin at duty cycles well below 50%. At D = 0.44, Qp can reach 5.6 or higher. When the outer voltage loop is closed, the loop gain can cross 0 dB again near f_sw/2 with zero phase margin, causing instability -- even though the current loop alone is stable. This is why the high-frequency extension is essential.

#### Ramp selection rule (Ridley criterion)

Set Qp <= 1 to ensure adequate damping of the double pole:

```
Se >= Sf * (1/(pi * D') - 1/2 - Sn/(2*Sf))    [exact]
```

Practical simplified rule: begin adding ramp at D > 0.36. The common textbook advice that "no ramp is needed below 50% duty" is incorrect and can lead to instability when the voltage loop is closed.

**Comparison with other ramp recommendations:**
- Se = Sf (full downslope): overdamped, more ramp than needed
- Se = Sf/2 (half downslope): theoretically nulls input-to-output perturbation for buck, but impractical due to component tolerances
- Ridley criterion (Qp = 1): optimal -- provides adequate damping without excessive overdamping

#### Magnetizing current as free compensation ramp

In isolated buck-derived topologies (forward, half-bridge, full-bridge) with primary-side current sensing, the magnetizing current provides a built-in compensation ramp:

```
Se_mag = Ri * Vin / (N * Lm)
```

This often provides more than enough damping. Check Se_mag against the required Se -- excessive magnetizing ramp overdamps the double pole, adding unnecessary phase delay.

#### Constant off-time control

Ridley showed that constant off-time control eliminates the duty-cycle dependence of the current loop gain entirely. The current loop gain is invariant with duty cycle, and the double-pole Qp is fixed at 2/pi regardless of operating point. This makes constant off-time naturally stable without any external ramp, equivalent to constant-frequency control with Se = Sf.

### 13.2 TL431 + Optocoupler: Ridley Practical Design (from "Designing with the TL431")

The TL431 + optocoupler circuit is the most widely used compensation scheme for isolated converters, but it has two distinct feedback paths that create a non-obvious Type II compensator.

#### The two feedback paths

1. **Low-frequency path (integrator)**: Through the TL431 amplifier with C1 and R1 forming an integrator. Gain = classic integrator response, multiplied by optocoupler CTR and R4/R5 ratio.

2. **Mid/high-frequency path (direct)**: Through R5 directly from the output voltage to the optocoupler diode. At frequencies above the integrator unity-gain point, this path dominates. The midband gain is set entirely by R4, R5, and the optocoupler CTR -- the TL431 amplifier is not part of this path.

#### Design sequence (Ridley recommended order)

1. **Set midband gain first** (determines crossover frequency): Choose R4, R5, and optocoupler bias point. The crossover frequency is determined by these resistors and the CTR, not by the compensation capacitor.

2. **Set compensation zero**: Place the integrator zero (from R1, C1) at approximately 1/3 of the desired crossover frequency.

3. **Maximize optocoupler bandwidth**: Use low-value bias resistors to keep the optocoupler operating near its rated current. Higher current = higher bandwidth. Warning: many integrated controllers have built-in pull-up resistors that force low-current optocoupler operation, reducing bandwidth.

4. **High-frequency pole**: Determined by the optocoupler parasitic capacitance and bias point. With a good optocoupler at adequate current, this can exceed 10 kHz.

#### Loop measurement with TL431

**You MUST measure the loop gain.** The TL431 + optocoupler system's stability depends on quantities that vary significantly: optocoupler CTR changes part-to-part and with temperature/aging.

**Injection point**: Inject at the output, breaking BOTH feedback paths simultaneously. Injecting at point A or B alone gives misleading results. An alternative valid injection point is on the primary side of the optocoupler.

#### TL431 with second-stage LC filter

When a second-stage LC filter is needed for low-noise output: the direct feedback path (R5) connects from before the second inductor, while the integrator feedback connects from after it (the actual output). This works because the second-stage filter's poles only appear in the integrator path, which has < unity gain above its zero frequency. The filter resonance must be properly damped and placed above the integrator zero.

### 13.3 Six Common Reasons for Power Supply Instability (Ridley Diagnostic Guide)

Ridley identifies six distinct instability mechanisms that are frequently confused with each other. Only the last two are classic control-loop problems -- the first four must be fixed before attempting loop compensation.

1. **Amplifier noise pickup**: High-GBW error amplifiers pick up switching-frequency RF noise, causing pulse-skipping. Fix: RC filter at error amp output, time constant at ~f_sw/2. Also: cut the optocoupler base lead to prevent EMI pickup.

2. **Control chip layout**: Timing capacitor must be placed as close to IC pins as physically possible. A 1/4" trace without ground plane caused a 100 kHz converter to briefly run at 1 MHz, destroying the FET.

3. **Operation near maximum duty cycle**: At low line, residual clock noise can prematurely trip the clock comparator. The turn-off noise causes alternate early clock termination.

4. **Light load pulse-skipping**: With current-mode control at light load/high line, the control signal drops below the minimum turn-on threshold, causing complete pulse skipping. Audible noise results.

5. **Current-loop subharmonic oscillation**: Classic alternating long/short pulses near 50% duty cycle. Solution: add compensation ramp (see Section 13.1). Important: do NOT use the clock ramp for this -- generate an independent ramp from the gate drive signal.

6. **Voltage-loop instability**: The only classic feedback problem. Characterized by sinusoidal oscillation (1-10 kHz audible tone), smooth duty cycle transitions. This is the one solved by proper compensator design.

**Diagnostic key**: Subharmonic oscillation sounds like modem noise (broadband, hashy). Voltage-loop instability sounds like a clean tone. Fix problems 1-4 first, then address 5, then 6.

### 13.4 Flyback Snubber Design (Ridley Practical Method)

Two snubber types are needed for flyback converters: RC snubber (damps ringing) and RCD clamp (limits peak voltage). Often both are required.

#### Primary RC snubber design procedure

**Step 1 -- Measure leakage inductance**: Use a frequency response analyzer with secondary shorted. Measure across a wide frequency range including the ringing frequency. Do NOT guess or use the "1% of Lm" rule -- actual leakage can differ by more than 10x from this estimate. Use the value at the ringing frequency.

**Step 2 -- Measure ringing frequency** (fr): From the unsnubbed drain waveform. The ringing is asymmetric (sharp peaks, wider valleys) due to nonlinear MOSFET Coss. Ringing frequency should be at least 100x the switching frequency, or dissipation will be excessive.

**Step 3 -- Calculate R and C**:
```
Z = 2 * pi * fr * L_leak        (characteristic impedance)
R_snub = Z                       (critically damps the ringing)
C_snub = 1 / (2 * pi * fr * R)  (impedance = R at ringing frequency)
```

**Step 4 -- Calculate dissipation**:
```
P_snub = C_snub * V^2 * f_sw     (V = Vin + V_reflected)
```
Note: no factor of 1/2 because the resistor dissipates on both charge and discharge.

**Step 5 -- Verify experimentally.** Do not skip this step.

#### Primary RCD clamp design procedure

Used when RC snubber alone cannot limit peak drain voltage to safe levels.

**Step 1 -- Measure leakage inductance** at the switching frequency (not ringing frequency -- use the energy-storage value).

**Step 2 -- Determine allowable clamp voltage rise vx**:
```
P_clamp = (1/2) * L_leak * Ip^2 * f_sw * (1 + vf/vx)
```
where vf = reflected flyback voltage. Typical design: vx = vf/2, giving P_clamp = 3 * E_leak.

**Step 3 -- Select clamp resistor**: Higher R = higher vx (lower dissipation but more FET stress). Lower R = lower vx (more dissipation but better FET protection).

**Step 4 -- Verify experimentally.** RCD clamp diode reverse recovery causes post-clamp ringing. Use the fastest diode possible. If post-clamp ringing is excessive, add an RC snubber in addition to the RCD clamp.

#### Secondary snubber

Often overlooked but equally critical. The secondary diode sees severe ringing from leakage inductance (referred to secondary: L_leak_pri / n^2) resonating with diode junction capacitance. Secondary ringing frequency is typically much higher than primary (e.g., 24 MHz vs 12 MHz), making it easier to snub with low dissipation. Design procedure is identical to primary RC snubber.

### 13.5 Ridley Loop Design Process (Seven-Step Method)

From the "Designing and Measuring Control Loops" webinar:

1. **Build power stage** (open loop)
2. **Close loop slowly** with very low bandwidth (< 10 Hz) using a large resistor (e.g., 100k) as the feedback element
3. **Measure the power stage** transfer function (control-to-output) using a frequency response analyzer
4. **Compare measurement with theory** -- if they don't match, find out why before proceeding
5. **Design the compensator** based on measured (not theoretical) plant
6. **Measure the loop gain** -- inject at the proper point, verify gain margin, phase margin, crossover frequency
7. **Compare loop measurement with theory** -- discrepancies indicate parasitics, layout issues, or modeling errors

**Ridley's cardinal rule**: Never ship a power supply without measuring the loop gain. Simulation is not validation -- bench data is validation.

---

## 14. Active Clamp Flyback (ACF) Control Loop

Sources:
- Basso, "Switch-Mode Power Supplies" Ch7 (flyback small-signal basis)
- TI Application Report SLUA535 — "Active Clamp and Reset Technique"
- Christophe Basso, "Designing Control Loops for Linear and Switching Power Supplies" (2012), Ch6
- ON Semiconductor NCP1568 datasheet (complementary gate drive, adaptive dead time)

### 14.1 Topology Overview for Control

The Active Clamp Flyback (ACF) replaces the RCD clamp with an active switch (Qa) and clamp capacitor (Cclamp). The main switch (Q1) and clamp switch (Qa) operate with complementary gate drives and a configurable dead time. Key control differences vs. standard flyback:

1. **Magnetizing current reverses** during dead time — this is intentional and required for ZVS
2. **Clamp capacitor reflects a secondary pole** (usually above 100 kHz — ignorable)
3. **RHPZ shifts upward** in ACF because effective Lm is reduced (ZVS sizing), increasing bandwidth headroom
4. **Burst mode / valley switching** is used at light load for efficiency
5. **Dead time is fixed** (set by Llk and Coss) — separate from the duty cycle control variable

### 14.2 Small-Signal Model

The ACF flyback small-signal model is identical to the standard flyback (buck-boost referred to secondary) with three modifications:

#### 14.2.1 Modified RHPZ

Because ACF requires a smaller Lm for ZVS (typically `Lm_ACF = α × Lm_std` where `α = 0.4–0.6`), the RHPZ shifts proportionally higher:

```
f_rhpz_ACF = f_rhpz_std / α

where:
  f_rhpz_std = R_load * (1-D)^2 / (2*pi * Lm_std * D)   [standard flyback CCM RHPZ]
  α           = Lm_ACF / Lm_std  (typically 0.4–0.6)
  f_rhpz_ACF  = R_load * (1-D)^2 / (2*pi * Lm_ACF * D)
```

**Implication:** ACF can achieve ~1.7–2.5× higher crossover frequency vs. an equivalent standard flyback. This improves load-step transient response.

#### 14.2.2 Clamp Capacitor Secondary Pole

The clamp capacitor Cclamp introduces an additional pole:

```
f_p_clamp = 1 / (2*pi * Rclamp_eq * Cclamp)
```

where `Rclamp_eq ≈ (1-D) * Vbus / Imag_rev` is the effective impedance of the clamp network. In practice:
- For Cclamp = 10–47 nF and Rclamp_eq = 50–200 Ω: f_p_clamp > 150 kHz
- This pole is above f_sw/3 for most designs — it can be ignored in the compensator design
- If Cclamp is large (>100 nF), verify f_p_clamp is above 3× f_c

#### 14.2.3 CCM Transfer Function

Same as standard flyback CCM (Basso App 2A p469), with Lm_ACF substituted:

```
G(s) = [Vin / (Vramp * (1-D)^2)] * [1 - s/w_rhpz_ACF] / [(s/w0)^2 + s/(w0*Q) + 1]

where:
  w_rhpz_ACF = R*(1-D)^2 / (Lm_ACF * D)
  w0         = 1/sqrt(Lm_ACF_eq * C_out)   with Lm_ACF_eq = Lm_ACF / (1-D)^2
  Q          = R * sqrt(C_out / Lm_ACF_eq)
```

#### 14.2.4 DCM Transfer Function

If ACF operates in DCM at light load (common below 20% load):

```
G(s) ≈ G0 / (1 + s/w_p1)

where:
  G0   = Vout / (Vcs_peak * D * T_sw * Lm_ACF * f_sw / Vout)   [simplified]
  w_p1 = 1 / (R_load * C_out)
```

Single-pole system — no RHPZ, no LC double pole. Standard Type 2 compensator is sufficient.

### 14.3 Compensator Selection

| Operating mode | Compensator | Notes |
|----------------|-------------|-------|
| CCM (full load) | Type 2 (CM) or Type 3 (VM) | f_c < f_rhpz_ACF / 3; PM ≥ 45° |
| DCM (light load) | Type 2 | Single-pole plant; simpler |
| CCM→DCM transition | Type 2 with gain limiting | Must be stable at both extremes |

**Practical recommendation:**
- Current-mode control (peak CM or average CM): Type 2 compensator is almost always sufficient, with f_c = 0.5–2 kHz typical for isolated ACF at 65–300 kHz switching.
- Voltage-mode control: Type 3 required for CCM; crossover at f_rhpz_ACF / 5 is conservative but safe.

### 14.4 Burst Mode at Light Load

ACF controllers (NCP1568, UCC28780, LT8304) implement burst mode or valley switching at light load to maintain efficiency:

**Burst mode thresholds:**
- Enter burst: typically at 5–15% of full load (Iout < I_burst_threshold)
- Exit burst: hysteresis prevents rapid on/off cycling
- Burst frequency: 50–200 Hz envelope visible as audio-band noise; use spread spectrum or increase Cclamp to mitigate

**Control loop behavior in burst mode:**
- The voltage loop integrator charges up during burst-off periods
- Ensure the error amplifier does not saturate or latch — use an anti-windup scheme or a clamp on the compensator output
- The small-signal model is invalid during burst mode; stability is empirically verified

**Recommended burst-mode design steps:**
1. Set I_burst threshold at ~10% I_out_max
2. Verify TL431 / error amplifier does not rail during burst-off
3. Measure output ripple during burst — typical 50–200 mVpp is acceptable
4. Verify no burst frequency falls in audible band under load steps (use scope with FFT)

### 14.5 Dead Time and Loop Interaction

The dead time `t_dead = (π/2) × sqrt(Llk × Ceq)` is fixed by the hardware (Llk and Coss). It does NOT interact with the voltage control loop. However:

- **Adaptive dead time controllers** (e.g., UCC28780 ClampZero) measure the ZVS valley and adjust t_dead cycle-by-cycle. This adjusts only the clamp FET timing, not the main duty cycle.
- **Dead time must be < 5% of T_sw** to avoid significant duty cycle loss. At f_sw = 100 kHz, t_dead < 500 ns.
- Dead time creates a small effective duty cycle reduction: `D_eff = D − t_dead × f_sw`. This is a static offset, not a dynamic term.

### 14.6 Optocoupler and TL431 Loop (Secondary-Regulated ACF)

For isolated output regulation, ACF uses the same optocoupler + TL431 loop as standard flyback. All design procedures from Section 12 apply directly. ACF-specific notes:

1. **Optocoupler pole**: f_p_opto = 1/(2π × CTR × R_pullup × C_opto). Same formula, same CTR degradation concerns.
2. **TL431 minimum cathode current**: 1 mA minimum — same constraint. Do not increase R_upper/R_lower to save power at the cost of TL431 dropout.
3. **Compensation bandwidth**: For ACF CCM, f_c = 0.5–1 kHz is typical with Type 2 + TL431. The higher RHPZ allows f_c up to 2–3 kHz if the optocoupler bandwidth supports it.
4. **ClampZero / no-load efficiency**: At no-load, the TL431 can reduce the duty cycle to near zero. Ensure the complementary gate driver handles extremely low duty cycles without false triggering on Qa.

### 14.7 ACF Loop Design Procedure

1. **Run design_engine.py** with topology "Active Clamp Flyback" — it returns `f_rhpz_acf`, `lm_acf`, `duty_nom`
2. **Calculate RHPZ bandwidth limit**: `f_c_max = f_rhpz_acf / 3` (conservative) or `/ 5` (robust)
3. **Choose compensator**: Type 2 for CM, Type 3 for VM (see Section 14.3)
4. **Design Type 2 compensator** (most common ACF case):
   - Place compensator zero at output pole: `f_z = 1/(2π × R_out × C_out)`
   - Set gain crossover at f_c (from step 2)
   - Verify PM ≥ 45° including optocoupler pole
5. **Check burst mode thresholds** — ensure control output does not saturate in burst-off interval
6. **Verify dead time duty-cycle budget**: `D_loss = t_dead × f_sw < 0.05`
7. **Bench verify**: Inject at the standard TL431/optocoupler injection point (Section 12). Measure PM, GM, crossover.

### 14.8 Key ACF Control Equations Summary

```
RHPZ:         f_rhpz_ACF = R*(1-D)^2 / (2*pi * Lm_ACF * D)
              [higher than std flyback by factor 1/alpha, where alpha = Lm_ACF/Lm_std]

Bandwidth:    f_c < f_rhpz_ACF / 3   (CM)
              f_c < f_rhpz_ACF / 5   (VM, conservative)

Dead time:    t_dead = (pi/2) * sqrt(Llk * Ceq)   [Ceq = Coss_Q1 + Coss_Qa]
D loss:       D_loss = t_dead * f_sw   (must be < 0.05)

Clamp pole:   f_p_clamp = 1/(2*pi * Rclamp_eq * Cclamp)   [usually > 150 kHz, ignore]

Burst entry:  I_out < ~10% I_out_max → burst mode
```
