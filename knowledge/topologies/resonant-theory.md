# Resonant Converter Theory (from Erickson Ch22-23)

Reference: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapters 22-23, pp.931-1033.

## Sinusoidal Approximation (First Harmonic Analysis)

Resonant converters contain L-C tank networks driven by square-wave voltage or current sources. The tank is tuned near the switching frequency, so it responds primarily to the fundamental component of the square wave.

### Method overview

1. **Model the switch network** as producing a square wave voltage v_s(t) with fundamental:

```
v_s1(t) = (4*V_g / pi) * sin(omega_s * t)     (full-bridge)
v_s1(t) = (2*V_g / pi) * sin(omega_s * t)     (half-bridge)
```

The amplitude of the fundamental of a square wave of amplitude V_g is 4*V_g/pi (full-bridge) or 2*V_g/pi (half-bridge).

2. **Model the rectifier and filter** as presenting an effective AC resistance to the tank. For a full-bridge rectifier with capacitive filter driving load R:

```
R_e = (8 / pi^2) * n^2 * R    (reflected through transformer turns ratio n)
```

This is because the rectifier converts a sinusoidal current into a DC current, and the effective resistance seen by the tank is the "AC equivalent" of the DC load.

3. **Solve the tank network** at the switching frequency using standard AC circuit analysis (phasors at frequency omega_s).

4. **Compute the voltage conversion ratio** M = V/V_g from the tank transfer function evaluated at omega_s.

### Tank transfer function

For a general resonant tank with transfer function H(s):

```
M = V/V_g = (n * pi^2 / 8) * |H(j*omega_s)| * (4/pi)    (full-bridge switch + full-bridge rectifier)

Simplified: M = (2*n / pi) * |H(j*omega_s)|               (half-bridge)
```

The conversion ratio is controlled by varying the switching frequency f_s relative to the tank resonant frequency f_0.

### Normalized quantities

```
F = f_s / f_0              (normalized switching frequency)
f_0 = 1 / (2*pi*sqrt(L*C)) (tank resonant frequency)
R_0 = sqrt(L/C)            (tank characteristic impedance)
Q = R_0 / R_e  or  Q = R_e / R_0   (quality factor, depends on topology)
```

## Series Resonant Converter

The series resonant converter uses an L-C tank in series between the switch network and the rectifier/load.

### Circuit and transfer function

Tank: L_s in series with C_s, load R_e in series.

```
H(s) = R_e / (R_e + sL_s + 1/(s*C_s))

|H(j*omega_s)| = 1 / sqrt(1 + Q_s^2 * (F - 1/F)^2)
```

where Q_s = R_0 / R_e = sqrt(L_s/C_s) / R_e (for series resonant, Q increases with load).

### Voltage conversion ratio

```
M = (n * 2/pi) / sqrt(1 + Q_s^2 * (F - 1/F)^2)     (half-bridge)
```

**Properties:**
- At resonance (F = 1): M = n*2/pi (independent of load, acts as ideal transformer)
- Below resonance (F < 1): M decreases, ZCS operation of switches
- Above resonance (F > 1): M decreases, ZVS operation of switches
- At no load (Q_s -> 0): M -> n*2/pi for all F (no voltage regulation possible at no load)
- Cannot boost (M <= n*2/pi always)
- **No-load regulation problem**: At no load, the output voltage is uncontrolled. Must use burst mode or other techniques.

### Subharmonic modes

The series resonant converter can also operate in subharmonic modes where f_s is near f_0/k (k = 3, 5, 7...). These modes are generally undesirable and should be avoided by proper frequency range selection.

## Parallel Resonant Converter

The parallel resonant converter places the load (through the rectifier) in parallel with the tank capacitor.

### Transfer function

```
|H(j*omega_s)| = 1 / sqrt((1 - F^2)^2 + F^2/Q_p^2)
```

where Q_p = R_e / R_0 = R_e / sqrt(L_p/C_p) (for parallel resonant, Q decreases with increasing load).

### Voltage conversion ratio

```
M = (n * 2/pi) / sqrt((1 - F^2)^2 + F^2/Q_p^2)     (half-bridge)
```

**Properties:**
- At resonance (F = 1): M = n*2/pi * Q_p (load-dependent)
- Can boost (M > 1 possible near resonance with high Q)
- No-load: output voltage can be regulated (unlike series resonant)
- Below resonance: ZCS operation
- Above resonance: ZVS operation
- Output behaves more like a voltage source (stiff output)

### Comparison: Series vs Parallel

| Property | Series Resonant | Parallel Resonant |
|----------|-----------------|-------------------|
| No-load regulation | Cannot regulate | Can regulate |
| Short-circuit protection | Inherent (current limited) | Not inherent |
| Boost capability | No (M <= 1) | Yes (M > 1 near resonance) |
| Q definition | Q = R_0/R_e (increases with load) | Q = R_e/R_0 (decreases with load) |
| Efficiency at light load | Good (currents reduce) | Poor (circulating currents) |
| Output characteristic | Current-source-like | Voltage-source-like |

## LLC / Series-Parallel Resonant

The LLC converter uses a series-parallel tank: series inductor L_r, series capacitor C_r, and parallel inductor L_m (the transformer magnetizing inductance).

### Tank network

The LLC tank has two resonant frequencies:

```
f_r = 1 / (2*pi*sqrt(L_r * C_r))           (series resonant frequency)
f_m = 1 / (2*pi*sqrt((L_r + L_m) * C_r))   (lower resonant frequency including L_m)
```

The ratio m = L_m/L_r (or equivalently L_p/L_r = 1 + L_m/L_r) is a key design parameter, typically m = 3 to 10.

### Voltage conversion ratio (first harmonic approximation)

```
M = 1 / sqrt((1 + 1/m - 1/(m*F^2))^2 + Q^2*(F - 1/F)^2)
```

where:
- F = f_s / f_r (normalized to series resonant frequency)
- Q = sqrt(L_r/C_r) / R_e
- m = L_m / L_r
- R_e = (8*n^2*R_load) / pi^2

### Key properties of LLC

1. **At F = 1 (f_s = f_r)**: M = 1 regardless of load. The converter behaves as an ideal transformer. This is the optimal operating point for efficiency.

2. **For F > 1 (above series resonance)**: M < 1. ZVS is achieved. The converter operates in "buck" mode.

3. **For f_m/f_r < F < 1 (between the two resonant frequencies)**: M > 1 possible. ZVS is still achievable. The converter operates in "boost" mode.

4. **For F < f_m/f_r**: Capacitive (leading) behavior, ZCS region. Should be avoided for MOSFET-based designs (body diode conduction, loss of ZVS).

5. **No-load regulation**: Unlike the pure series resonant converter, the LLC can regulate at no load because the magnetizing inductance L_m provides a minimum load to the tank.

### LLC gain curves

The gain curves M vs F are plotted for different Q values:
- Q = 0 (no load): M has a peak at f_m, monotonically decreasing above f_r
- Increasing Q: the peak reduces and shifts, the curves flatten
- At high Q: gain approaches 1 for all F > 1

The ZVS boundary coincides approximately with the inductive region (above the gain curve peak for each Q).

## Gain Curves and Operating Regions

### Operating regions of resonant converters

1. **Continuous conduction above resonance (f_s > f_0)**: Tank current lags tank voltage. ZVS natural for MOSFETs. Preferred operating mode.

2. **Continuous conduction below resonance (f_s < f_0)**: Tank current leads tank voltage. ZCS natural. Preferred for IGBTs (which have tail current at turn-off).

3. **Discontinuous conduction modes**: Tank current reaches zero and remains zero for part of the switching period. Multiple DCM sub-modes exist depending on topology and operating point.

### Inverter output characteristics

The resonter converter can be characterized by its output characteristics: normalized output voltage (M) vs. normalized output current (J), for various switching frequencies F.

For the series resonant converter, the output characteristic is elliptical:

```
M^2 + J^2 = 1    (at resonance, F = 1)
```

This means the series resonant converter at resonance behaves as an ideal voltage source in series with an ideal current source -- it naturally limits both output voltage and output current.

### Load-dependent properties

Key questions for any resonant converter design:
1. How do transistor currents vary with load?
2. Over what load range is ZVS (or ZCS) maintained?
3. What is the minimum load required for soft switching?

For the series resonant converter above resonance:
- Transistor peak current equals tank peak current ~ V_g / (R_0 * sqrt(F^2 - 1)) at light load
- ZVS is maintained for all loads when F > 1
- Transistor currents increase with decreasing F (approaching resonance)

For the LLC:
- ZVS is maintained for all loads when f_s > f_m (above the lower resonant frequency)
- At very light load, the magnetizing current provides sufficient energy for ZVS transitions

## ZVS and ZCS Conditions

### Zero-Voltage Switching (ZVS)

ZVS occurs when the switch turns on while its drain-source voltage is zero (or at the body diode voltage). Requirements:
- The tank current must be **lagging** the tank voltage (inductive impedance)
- Sufficient energy stored in the tank inductor to charge/discharge the switch node capacitance
- This occurs naturally **above resonance** in most tank topologies

ZVS eliminates:
- Turn-on switching loss (C_oss discharge loss eliminated)
- Diode reverse recovery (body diode conducts before MOSFET turns on)
- Turn-on current spike
- dv/dt noise at turn-on

ZVS condition for full-bridge:

```
Energy condition: (1/2) * L * I_tank^2 > 2 * (1/2) * C_oss * V_g^2

Minimum tank current for ZVS: I_min > V_g * sqrt(2*C_oss/L)
```

The dead time t_dead must be:

```
t_dead > pi * sqrt(L * C_oss / 2)   (quarter resonant period of L and 2*C_oss)
```

but also:

```
t_dead < time for tank current to reverse   (to avoid losing ZVS)
```

### Zero-Current Switching (ZCS)

ZCS occurs when the switch turns off while its current is zero. Requirements:
- The tank current must be **leading** the tank voltage (capacitive impedance)
- This occurs naturally **below resonance**
- Preferred for IGBTs (eliminates tail current losses)

ZCS eliminates:
- Turn-off switching loss
- IGBT tail current loss

**Note**: ZCS is generally NOT preferred for MOSFETs because the body diode conducts during the dead time, and the diode reverse recovery at turn-on of the opposite switch causes significant loss.

## Soft Switching Techniques (Ch23)

### Switching loss mechanisms in hard switching

1. **Diode reverse recovery**: When a diode turns off, it conducts reverse current while stored charge is removed. This current flows through the turning-on transistor, causing high instantaneous power dissipation. For Si diodes, recovery charge Q_rr can be substantial.

2. **MOSFET turn-on**: The MOSFET discharges its own C_oss and the opposite switch C_oss during turn-on. Energy (1/2)*C_oss*V_ds^2 is dissipated per switch per cycle. For high-voltage MOSFETs, C_oss losses can be significant.

3. **MOSFET turn-off**: Overlap of falling current and rising voltage causes turn-off loss. Usually smaller than turn-on loss due to fast current fall time.

4. **IGBT tail current**: At turn-off, IGBTs exhibit a slowly decaying tail current due to stored minority carriers. This causes significant turn-off loss proportional to tail current duration.

### ZVS Turn-On

The MOSFET body diode conducts before the MOSFET channel turns on, clamping V_DS to approximately -0.7V. The gate drive then turns on the MOSFET channel at near-zero voltage.

**Energy balance for ZVS**: The inductor (or tank) current must charge/discharge the parasitic capacitances at the switch node during the dead time:

```
(1/2) * L * i_L^2(t_off) >= C_node * V_bus^2

where C_node = C_oss_Q1 + C_oss_Q2 + C_parasitic
```

If insufficient energy is stored, the transition is incomplete (partial ZVS or hard switching).

### ZCS Turn-Off

In ZCS, the switch current naturally falls to zero before the switch is turned off. The gate signal is removed while no current flows, so there is no overlap loss.

Most useful for IGBTs and thyristors. For MOSFETs, the body diode issue makes ZCS less attractive.

### Zero-Voltage Transition (ZVT)

The ZVT full-bridge converter achieves ZVS in a PWM converter by using the transformer leakage inductance and/or an external inductor to achieve ZVS transitions.

**Phase-shifted full-bridge ZVT:**
- The full-bridge switches are driven with phase-shifted PWM
- During the transition intervals, the leakage inductance current charges/discharges the switch capacitances
- Leading leg transitions: easy ZVS (full load current available)
- Lagging leg transitions: harder ZVS (only leakage inductance current, may need additional inductance)

The duty cycle loss due to ZVT transitions:

```
D_loss = 4 * f_sw * L_leak * I_load / V_in
```

### Auxiliary Resonant Commutated Pole

An auxiliary circuit is added to each switch leg to provide the energy needed for ZVS transitions, independent of load current. The auxiliary circuit consists of a small auxiliary switch and a resonant inductor.

**Operating principle:**
1. Before the main switch transition, the auxiliary switch turns on
2. The auxiliary inductor current builds up, providing energy for ZVS
3. The main switch node voltage transitions to zero
4. The main switch turns on at ZVS
5. The auxiliary switch turns off (at ZCS)

**Advantages:**
- ZVS maintained from no load to full load
- Works with standard PWM control
- No duty cycle loss

**Disadvantages:**
- Additional components (auxiliary switches, inductors)
- Increased complexity
- Auxiliary switch timing must be precisely controlled

## Design Methodology for Resonant Converters

### Step 1: Choose topology and tank network

- **Series resonant**: Good for applications with narrow load range, inherent short-circuit protection
- **Parallel resonant**: Good for wide load range, can boost voltage, but poor light-load efficiency
- **LLC (series-parallel)**: Best all-around choice for most DC-DC applications. Combines advantages of series (good efficiency) with parallel (no-load regulation)

### Step 2: Determine specifications

- Input voltage range (V_in_min to V_in_max)
- Output voltage V_out
- Load range (P_min to P_max)
- Transformer turns ratio n

### Step 3: Select turns ratio n

For LLC, design for M = 1 at nominal input voltage:

```
n = V_in_nom / (2 * V_out)    (half-bridge)
n = V_in_nom / (4 * V_out)    (full-bridge, accounting for diode drops)
```

### Step 4: Determine required gain range

```
M_max = n * V_out / V_in_min    (maximum gain at minimum input)
M_min = n * V_out / V_in_max    (minimum gain at maximum input)
```

### Step 5: Select Q and m (for LLC)

Using the gain equation, find Q and m that satisfy:
- M_max is achievable at some frequency F < 1
- M_min is achievable at some frequency F > 1
- ZVS is maintained over the entire operating range
- Frequency range (f_min to f_max) is practical

**Design trade-offs:**
- Lower m: wider gain range but higher magnetizing current, more circulating current loss
- Higher m: narrower gain range but lower circulating current, better efficiency at nominal
- Lower Q: easier to achieve wide gain range but larger tank components
- Higher Q: more selective tank, sharper gain curves

### Step 6: Calculate tank component values

```
f_r = chosen resonant frequency (typically 80-150 kHz)
R_e = 8*n^2*R_load / pi^2

For LLC:
C_r = 1 / (2*pi*f_r*R_e*Q)
L_r = 1 / ((2*pi*f_r)^2 * C_r)
L_m = m * L_r
```

### Step 7: Verify with exact analysis or simulation

The first harmonic approximation is accurate for F near 1 and moderate Q. For operation far from resonance or at very light/heavy loads, exact analysis (state-plane methods) or circuit simulation is needed.

### Step 8: Design the magnetic components

- Transformer: must provide turns ratio n, magnetizing inductance L_m, and handle the RMS currents
- Series inductor L_r: can be partially or fully realized as transformer leakage inductance
- If L_r > leakage inductance, an external inductor is needed

### Quasi-resonant switch cells

An alternative to full resonant converters: add small L-C resonant elements to a PWM switch cell to achieve soft switching while maintaining PWM-like voltage conversion characteristics.

**ZCS quasi-resonant switch**: A small inductor and capacitor are added to the switch. During turn-on, the switch current rises sinusoidally through the resonant inductor. The switch turns off at zero current.

- Half-wave ZCS: switch current is unidirectional (one half of resonant cycle)
- Full-wave ZCS: switch current is bidirectional (full resonant cycle)

Conversion ratio for ZCS quasi-resonant buck:

```
M = 1 - (f_s / (2*f_0))    (half-wave ZCS)
```

The output voltage is controlled by varying the switching frequency.

**ZVS quasi-resonant switch**: Dual of ZCS. A resonant capacitor is placed across the switch, and a resonant inductor is in series. The switch voltage rings sinusoidally, and the switch turns on at zero voltage.

**ZVS multi-resonant switch**: Uses two resonant capacitors (across each switch element -- transistor and diode) plus a resonant inductor. Achieves ZVS for both transistor and diode, eliminating diode reverse recovery loss.

**Quasi-square-wave (QSW) resonant switches**: The resonant elements are the same as in the PWM converter (output filter inductor and switch capacitances). By operating at the boundary between CCM and DCM, the inductor current naturally reaches zero, enabling ZVS. This requires a very large inductor current ripple (100% of DC), leading to high peak currents.

## Rigorous Resonant Tank Analysis (from Kazimierczuk & Czarkowski, 2nd ed. 2011)

Reference: Kazimierczuk & Czarkowski, "Resonant Power Converters" 2nd ed., Wiley-IEEE 2011, Part III (Chapters 15-23).

Kazimierczuk's approach differs from Erickson's: each converter is decomposed into a resonant inverter (Part II) cascaded with a high-frequency rectifier (Part I). The DC voltage transfer function Mv is the product of the inverter transfer function Mvi and the rectifier transfer function |Mvr|:

```
Mv = Vo/Vi = Mvi * |Mvr|
```

Similarly, the overall efficiency is the product of inverter and rectifier efficiencies: eta = eta_I * eta_R.

### Series Resonant Converter (Ch15)

The SRC consists of a Class D series-resonant inverter (Ch6) cascaded with a Class D current-driven rectifier (Ch2). The inverter output acts as a sinusoidal current source when QL is high, making it compatible with current-driven rectifiers.

#### DC Voltage Transfer Function (lossless, high-QL)

For a half-bridge SRC, the lossless transfer function depends on the rectifier type:

| Rectifier | Half-Bridge Mv | Full-Bridge Mv |
|-----------|----------------|----------------|
| Half-wave | 1 / (n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) | 2 / (n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) |
| Center-tapped | 1 / (2n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) | 1 / (n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) |
| Bridge | 1 / (2n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) | 1 / (n * sqrt(1 + QL^2*(f/f0 - f0/f)^2)) |

Where:
- f0 = 1/(2*pi*sqrt(L*C)) is the resonant frequency
- QL = loaded quality factor of the resonant circuit
- n = transformer turns ratio
- f = switching frequency

#### Full Efficiency Expressions Including Parasitics

For the half-bridge SRC with bridge rectifier (most common):

```
eta = eta_I * eta_R * eta_tr

eta_R = 1 / (1 + 2*VF/Vo + 4*RF/RL + pi^2*rc/(8*RL))

Mv = eta_tr / (2n * sqrt(1 + QL^2*(f/f0 - f0/f)^2) * (1 + 2*VF/Vo + 4*RF/RL + pi^2*rc/(8*RL)))
```

Where r = rDS + rL + rc (total parasitic resistance of inverter), VF = diode threshold voltage, RF = diode forward resistance, rc = ESR of filter capacitor.

#### Component Stresses

```
Peak resonant current: Im = 2*Vi*QL / (pi*Z0)  (at resonance)
Resonant capacitor voltage stress: VCm = Z0*Im = 2*Vi*QL/pi  (at resonance)
Resonant inductor voltage stress: VLm = Z0*Im = 2*Vi*QL/pi  (at resonance)
```

These stresses are worst-case at f = f0 (resonance). At frequencies away from resonance, stresses decrease.

#### SRC Key Properties (Summary from Ch15)

- Transformerless SRC is a step-down converter (except full-bridge with half-wave rectifier)
- Safe at open circuit but CANNOT regulate Vo at no load and light loads (preload required)
- Inherently short-circuit protected at frequencies sufficiently far from f0
- DANGEROUS at f = f0 with short circuit (impedance = parasitic resistance only)
- Efficiency increases with load resistance (better at light loads)
- Wide frequency range needed to regulate against load variations
- High ripple current through output filter capacitor

#### Design Procedure (from Example 15.2)

1. Calculate DC voltage transfer function Mv = Vo/Vi
2. Find rectifier input resistance Ri from rectifier equations
3. Determine |Mvr| (magnitude of resonant circuit transfer function) from Mv/(Mvs*|MvR|)
4. Solve for QL from the transfer function equation at the chosen f/f0
5. Calculate L and C from QL and Ri:
```
L = QL*Ri / (2*pi*f0)
C = 1 / (2*pi*f0*QL*Ri)
Z0 = sqrt(L/C) = Ri/QL
```
6. Verify voltage stresses at corner frequency f0

### Parallel Resonant Converter (Ch16)

The PRC consists of a Class D parallel-resonant inverter (Ch7) cascaded with a Class D voltage-driven rectifier (Ch3). The load is connected in parallel with the resonant capacitor. The inverter output acts as a sinusoidal voltage source when QL > 2.5, making it compatible with voltage-driven rectifiers.

#### DC Voltage Transfer Function (lossless, high-QL)

For a half-bridge PRC with bridge rectifier:

```
Mv = 4 / (n*pi^2 * sqrt([1 - (f/f0)^2]^2 + [1/QL * (f/f0)]^2))
```

| Rectifier | Half-Bridge Mv | Full-Bridge Mv |
|-----------|----------------|----------------|
| Half-wave | 2/(n*pi^2 * ...) | 4/(n*pi^2 * ...) |
| Center-tapped | 4/(n*pi^2 * ...) | 8/(n*pi^2 * ...) |
| Bridge | 4/(n*pi^2 * ...) | 8/(n*pi^2 * ...) |

where ... = sqrt([1 - (f/f0)^2]^2 + [1/QL * (f/f0)]^2)

#### Rectifier Input Resistance

For the three rectifier types:
```
Ri = pi^2*RL / (2*eta_R)     (half-wave)
Ri = 2*n^2*pi^2*RL / (8*eta_R*eta_tr)  (center-tapped)
Ri = n^2*pi^2*RL / (8*eta_R*eta_tr)    (bridge)
```

#### PRC Key Properties (Summary from Ch16)

- Can be used as both step-down AND step-up converter (even with n=1)
- Can regulate output voltage from full load to no load with narrow frequency range
- Inherently short-circuit protected
- NOT open-circuit safe near f0 (control circuit must prevent this)
- Efficiency DECREASES with increasing RL (worse at light loads -- opposite of SRC)
- Contains inductive output filter: low filter capacitor ripple current, suitable for low-V/high-I
- Output filter corner frequency is load-independent (wide bandwidth at all loads)
- The boundary between inductive and capacitive load depends on RL for QL > 1
- At QL < 1, load is inductive for all RL (always safe for MOSFETs)
- Rectifiers transition from Class D behavior (low RL) to Class E behavior (high RL)

#### Comparison: SRC vs PRC vs SPRC

| Property | SRC (Ch15) | PRC (Ch16) | SPRC (Ch17) |
|----------|-----------|-----------|-------------|
| No-load regulation | Cannot | Can | Can |
| Short-circuit safe | Yes (away from f0) | Yes | No (near fr) |
| Open-circuit safe | Yes | No (near f0) | No (near f0) |
| Light-load efficiency | Good (Im decreases) | Poor (Im constant) | Good if Ri << Xc2 |
| Frequency range for regulation | Wide | Narrow | Narrow |
| Output filter | Capacitive (high ripple) | Inductive (low ripple) | LC (low ripple both) |
| Boost capability | No | Yes | Yes |

### Series-Parallel (LCC) Converter (Ch17)

The SPRC combines the advantages of both SRC and PRC. It uses a resonant circuit with L, C1 (series), and C2 (parallel with load). In Kazimierczuk's nomenclature, this is the LCC topology. The LLC topology (Ch18) uses L1-L2-C instead.

**Important nomenclature note**: Kazimierczuk's Ch17 "Series-Parallel" is the LCC converter (two capacitors, one inductor). The LLC converter (two inductors, one capacitor) that is standard in modern power supplies is covered in Ch18 as the "CLL" converter (or briefly in Section 18.6 as the LLC variant).

#### Circuit Parameters

```
A = C1/C2                    (capacitance ratio, key design parameter)
f0 = 1/(2*pi*sqrt(L*Ceq))   (corner frequency, Ceq = C1*Cs/(C1+Cs))
QL = Ceq/(omega0*Ri)         (loaded quality factor)
Z0 = Ri/QL                   (characteristic impedance)
```

#### DC Voltage Transfer Function (lossless)

For a half-bridge SPRC with bridge rectifier:

```
Mv = 4 / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2))
```

The SPRC has TWO characteristic frequencies:
- f0 = 1/(2*pi*sqrt(L*Ceq)) -- corner frequency
- fr = 1/(2*pi*sqrt(L*Cs)) -- resonant frequency, where Cs = C1*C2/(C1+C2)

#### Load-Dependent Behavior

The SPRC exhibits a critical transition in part-load efficiency:
- When Ri << Xc2 = 1/(omega*C2): most current flows through load. Im is inversely proportional to RL, giving HIGH part-load efficiency (like SRC)
- When Ri >> Xc2: most current flows through C2. Im is constant, giving POOR part-load efficiency (like PRC)

Design rule: choose C2 such that Xc2 >> Ri_max for best part-load efficiency.

#### SPRC Key Properties (from Ch17)

- Mv is independent of load at f = frs = 1/(2*pi*sqrt(L*C1)) -- the series resonant frequency of L-C1
- At frs, the resonant circuit is CAPACITIVE (not inductive), which is undesirable for MOSFETs
- The sensitivity of Mv to load decreases with increasing A = C1/C2
- For regulating Vo, the normalized frequency range decreases with increasing C1/C2
- Neither filter capacitor nor filter inductor carries high ripple current

#### Design Procedure (from Example 17.1)

1. Set specs: Vi, Vo, Io range
2. Calculate Ri from rectifier equations
3. Determine required |Mvr| from Mv/(Mvs*|MvR|)
4. Choose A (typically 1), QL (typically 0.2-0.5), f0 (100-200 kHz)
5. Solve numerically for f/f0 from the gain equation
6. Calculate components:
```
L = Ri / (omega0 * QL)
C = QL / (omega0 * Ri)     (total equivalent capacitance)
C1 = C * (1 + 1/A)
C2 = C * (1 + A)
```

### CLL Resonant Converter (Ch18) -- Basis for LLC and CLLC

The CLL converter uses a resonant circuit with C, L1, and L2 (tapped inductor). The resonant capacitor C is in series with the tapped inductor L1-L2, and the load is connected in parallel with L2. This is the dual of the LCC (SPRC) topology -- inductors and capacitors swap roles.

**Critical connection to modern LLC**: Section 18.6 notes that the LLC resonant converter is obtained when L2's leakage inductance is absorbed into L1. The CLL topology is the foundation for CLLC bidirectional converters.

#### Circuit Parameters

```
A = L1/L2                    (inductance ratio)
f0 = 1/(2*pi*sqrt(C*L))     (corner frequency, L = L1 + L2)
QL = Ri / (omega0*L)         (loaded quality factor -- note: inverse of SRC definition)
Z0 = Ri/QL                   (characteristic impedance)
```

#### DC Voltage Transfer Function (lossless)

For a half-bridge CLL with bridge rectifier:

```
Mv = 4 / (n*pi^2 * sqrt((1+A)^2*[1-(f/f0)^2]^2 + [1/QL*(f/f0 - A*f0/((1+A)*f))]^2))
```

Note: This has the SAME mathematical form as the SPRC, with inductance ratio A replacing capacitance ratio.

#### Two Characteristic Frequencies

```
f0 = 1/(2*pi*sqrt(C*(L1+L2)))    (corner frequency -- with full inductance)
frs = 1/(2*pi*sqrt(C*L1))         (series resonant frequency of C-L1)
```

The gain is INDEPENDENT of load at f/f0 = sqrt(1 + L2/L1) = sqrt(1 + 1/A). At this frequency, the load to the switches is INDUCTIVE (desirable for MOSFETs).

#### CLL Key Properties (from Ch18)

- Mv is independent of load at f/f0 = sqrt(1 + 1/A), which occurs in the INDUCTIVE region (advantage over SPRC where independence occurs in capacitive region)
- Part-load efficiency depends on the same Ri vs XL2 criterion as SPRC
- NOT safe at short circuit near fr (excessive current through C and switches)
- NOT safe at open circuit near f0
- Sensitivity of Mv to load decreases with increasing L1/L2

#### Component Stresses (CLL, from Example 18.1)

```
Peak switch current: ISM = Im = (2*Vi)/(pi*Ri) * sqrt(1 + [QL*f/f0*(1+A)]^2) / sqrt(...)
Peak switch voltage: VSM = Vi  (half-bridge)
Peak inductor L1 voltage: VL1m = omega*L1*Im
Peak inductor L2 voltage: VL2m from gain equation
Peak capacitor voltage: VCm = Im / (omega*C)
```

#### Design Procedure (from Example 18.1)

1. Calculate Ri from rectifier parameters
2. Choose A = L1/L2 (typically 0.5-2), QL (typically 0.2-0.5), f0 (100 kHz)
3. Solve numerically for f/f0 from gain equation
4. Calculate components:
```
C = QL / (omega0 * Ri)
L = Ri / (omega0 * QL)       (total inductance L1+L2)
L1 = L*A/(1+A)  or  L/(1+1/A)
L2 = L/(1+A)
```

### Modeling and Control of Resonant Converters (Ch23)

#### Small-Signal Modeling via Extended Describing Functions

Kazimierczuk uses the extended describing function (EDF) method for small-signal modeling of resonant converters. The approach decomposes sinusoidal tank variables into d-q (direct-quadrature) components, converting the AC resonant tank equations into DC-like differential equations.

**Steps:**
1. Decompose the AC input (square wave from inverter) into fundamental: va = Vad*sin(wt) + Vaq*cos(wt)
2. Decompose all tank variables (currents, voltages) into d-q components: i = Id*sin(wt) + Iq*cos(wt)
3. Approximate the rectifier input current as the fundamental of its square wave
4. Write differential equations for all d-q components plus DC output filter variables
5. Linearize around the operating point to get a state-space small-signal model: dX/dt = A*X + B*U

#### Model Order

For a phase-controlled SPRC (PC SPRC):
- 5 reactive components in resonant tank + 2 in output filter = 7 energy storage elements
- d-q decomposition doubles the 5 AC states to 10, plus 2 DC states = 12 states total
- Symmetry in the double-tank reduces to 8th-order model
- Balanced model reduction technique can reduce to 3rd-order for controller design

#### Control Methods

**Frequency control:**
- Most common for half-bridge resonant converters
- Varying fs changes the impedance of the resonant tank relative to the load
- Pros: simple implementation, single control variable
- Cons: variable frequency complicates EMI filter design, magnetics design

**Phase control:**
- Requires full-bridge configuration
- Phase shift between two legs controls effective input voltage to tank
- Pros: CONSTANT frequency operation, simpler EMI filtering
- Cons: more complex drive, requires full-bridge

**Self-sustained oscillation control:**
- Converter oscillates naturally at the resonant frequency
- Gate signals derived from tank current/voltage zero crossings
- Pros: inherently tracks resonant frequency, simple
- Cons: limited control range

**Transfer function example** (PC SPRC, phase control to output):

```
G(s) = (-1.08e5*s^2 - 3.78e8*s + 2.75e13) / (s^3 + 6.4e3*s^2 + 2.44e8*s + 6.79e11)
```

This reduced 3rd-order model captures the dominant dynamics for controller design. A PI or Type 2 compensator is typically sufficient.

#### Practical Control Guidelines

- Series resonant converter: use frequency control, PI compensator sufficient near resonance
- Parallel resonant converter: resonant-tank control or two-loop control provides suboptimal trajectory control
- Series-parallel/LLC: frequency control with PI/Type 2 compensator, bandwidth limited to 1/10 to 1/5 of fs
- Advanced methods: sliding-mode control, neural controllers, fuzzy controllers have been demonstrated

## Quasi-Resonant Converters (from Kazimierczuk Ch22)

Reference: Kazimierczuk & Czarkowski, "Resonant Power Converters" 2nd ed., Ch22, pp.485-564.

Quasi-resonant (QR) converters add small resonant elements (Lr, Cr) to conventional PWM switch cells. The resonant elements shape the switch waveforms to achieve soft switching while maintaining PWM-like voltage conversion. Output voltage is controlled by varying the switching frequency (not duty cycle).

### Switching Loss Mechanisms in Hard-Switching PWM Converters

```
Turn-on capacitive loss:   Psw_on = fs * (1/2) * Coss * Voff^2
Diode reverse recovery:    Causes current spikes at turn-on, EMI
Turn-off inductive spike:  VL = Llk * di/dt (leakage inductance)
```

### Taxonomy of Soft-Switching Converters

1. **ZVS quasi-resonant converters (ZVS-QRC)**: transistor turns ON at zero voltage
2. **ZCS quasi-resonant converters (ZCS-QRC)**: transistor turns OFF at zero current
3. **ZVS multi-resonant converters (ZVS-MRC)**: both transistor and diode achieve ZVS
4. **ZCS multi-resonant converters (ZCS-MRC)**: both transistor and diode achieve ZCS
5. **ZVT-PWM**: zero-voltage transition with auxiliary circuit
6. **ZCT-PWM**: zero-current transition with auxiliary circuit

All use frequency modulation (VCO-based) control as shown in Ch22 Fig 22.2.

### ZVS Quasi-Resonant Switch Cell

A ZVS-QRC is formed by adding:
- Resonant capacitor Cr in PARALLEL with the switch (absorbs Coss)
- Resonant inductor Lr in SERIES with the parallel combination of switch + Cr

The diode lead inductance is absorbed into Lr. However, the diode junction capacitance Cj is NOT absorbed -- it forms a parasitic resonant circuit with Lr that causes ringing.

```
Resonant frequency: f0 = 1/(2*pi*sqrt(Lr*Cr))
Characteristic impedance: Z0 = sqrt(Lr/Cr) = omega0*Lr = 1/(omega0*Cr)
Normalized frequency: A = fs/f0
Quality factor: Q = RL/Z0 = omega0*Cr*RL
```

**Two switch types:**
- Half-wave ZVS-QRC: MOSFET + antiparallel body diode (unidirectional voltage, bidirectional current). Switch voltage is always positive. h < 0.
- Full-wave ZVS-QRC: MOSFET + series blocking diode (bidirectional voltage, unidirectional current). Switch voltage can be negative. h > 0. Higher conduction loss due to series diode.

**Four operating intervals per switching cycle:**
1. Inductor charging (switch ON, diode ON): iLr ramps up linearly
2. Idle time (switch ON, diode OFF): is = Io, constant
3. Capacitor charging (switch OFF, diode OFF): vs ramps up linearly as Io charges Cr
4. Resonant interval (switch OFF, diode ON): Lr-Cr resonate, vs rings sinusoidally back to zero

### Buck ZVS Quasi-Resonant Converter

#### DC Voltage Transfer Function

For h = 0 (optimal, both ZVS and ZDS conditions satisfied), n = 1:

```
Mvdc = Vo/Vi = 1 - (3*pi + 2)/(4*pi) * (fs/f0) = 1 - 0.9092*(fs/f0)
```

General expression (implicit, requires numerical solution):

```
Mvdc = 1 - (fs/(2*pi*f0)) * [2*pi*n + sqrt(1-h^2) - arccos(h) + A*(1-h)^2 / (2*sqrt(1-h^2))]
```

where h = sqrt(1 - (Q/Mvdc)^2), Q = RL/Z0, n = mode number (1 for fundamental).

**Load range constraint for ZVS**: 0 < RL < RL_max where RL_max = Z0*Mvdc. This means ZVS-QRC buck requires LOW load resistance. For typical applications where RL_min < RL < infinity, an impedance inverter is needed.

#### Component Stresses (Buck ZVS-QRC)

```
Switch peak current:  ISM = Io = Vo/RL
Switch peak voltage:  VSM = Vi + Vo*(Q/Mvdc + 1)*Vi = (1 + Q/Mvdc)*Vi
                      VSM = 2*Vi  (for h=0, Q=Mvdc)
Diode peak current:   IDM = 2*Io
Diode peak voltage:   VDM = Vi = Vo/Mvdc
```

#### Design Example (Buck ZVS-QRC, from Example 22.1)

Specs: Vi = 20V, Vo = 10V, Po = 10W

```
RL = Vo^2/Po = 10 ohm
Mvdc = 0.5
Q = Mvdc = 0.5  (for h=0, optimal)
Z0 = RL/Q = 20 ohm
fs = 1 MHz
f0 = 0.9092*fs / (1 - Mvdc) = 1.818 MHz
Lr = Z0/(omega0*2*pi*f0) = 1.75 uH   --> pick 1.8 uH
Cr = 1/(omega0*Z0) = 4.376 nF         --> pick 3.9 nF
ISM = 1A, VSM = 40V, IDM = 2A, VDM = 20V
```

### Boost ZVS Quasi-Resonant Converter

#### DC Voltage Transfer Function

For h = 0, n = 1:

```
Mvdc = Vo/Vi = 1 / (1 - (3*pi+2)/(4*pi) * (fs/f0)) = 1 / (1 - 0.9092*(fs/f0))
```

Same load range constraint: 0 < RL < Z0*Mvdc.

#### Component Stresses (Boost ZVS-QRC)

```
Switch peak current:   ISM = Ii = Mvdc*Io
Switch peak voltage:   VSM = Vi + Vo*(Q/Mvdc + 1) = (1 + Q/Mvdc)*Vo
                       VSM = 2*Vo  (for h=0)
Diode peak current:    IDM = 2*Ii = 2*Mvdc*Io
Diode peak voltage:    VDM = Vo
```

### ZCS Quasi-Resonant Switch Cell

A ZCS-QRC is formed by adding:
- Resonant inductor Lr in SERIES with the switch (absorbs lead inductance)
- Resonant capacitor Cr in PARALLEL with the series combination of switch + Lr

**Key advantage over ZVS-QRC**: The load range is RL > RL_min = Z0*Mvdc, which is compatible with typical applications where RL_min < RL < infinity (no impedance inverter needed).

**Two switch types:**
- Half-wave ZCS-QRC (h < 0): switch current is unidirectional
- Full-wave ZCS-QRC (h > 0): switch current is bidirectional (MOSFET + antiparallel diode). At h = Mvdc, the converter gain is nearly independent of Q, providing good load regulation.

### Buck ZCS Quasi-Resonant Converter

#### DC Voltage Transfer Function

For h = 0, n = 1:

```
Mvdc = (3*pi + 2)/(4*pi) * (fs/f0) = 0.9092*(fs/f0)
```

For full-wave (h > 0), the gain is nearly linear: Mvdc ~ fs/f0 when h = Mvdc.

#### Component Stresses (Buck ZCS-QRC)

```
Switch peak current:  ISM = (Q/Mvdc + 1)*Io     (proportional to RL!)
                      ISM = 2*Io  (for h=0, Q=Mvdc)
Switch peak voltage:  VSM = Vi = Vo/Mvdc
Diode peak current:   IDM = Io
Diode peak voltage:   VDM = 2*Vi = 2*Vo/Mvdc
```

**Warning**: In ZCS-QRC, switch peak current is proportional to load resistance. This means current stress INCREASES at light load, opposite to ZVS-QRC.

#### Design Example (Buck ZCS-QRC, from Example 22.4)

Specs: Vi = 28V, Vo = 14V, Po = 17.5W, RL_max/RL_min = 5

```
RL_min = 10 ohm, Mvdc = 0.5
Q_min = Mvdc = 0.5
fs = 1 MHz, f0 = fs/Mvdc = 2 MHz  (for full-wave, A = Mvdc)
Z0 = RL_min/Q_min = 20 ohm
Lr = Z0/omega0 = 1.59 uH
Cr = 1/(omega0*Z0) = 3.97 nF --> 4 nF
ISM_max = 2*Io_max = 2.8A, VSM = 28V
IDM_max = 1.4A, VDM = 28V
```

### Boost ZCS Quasi-Resonant Converter

#### DC Voltage Transfer Function

For h = 0, n = 1:

```
Mvdc = 1 / (1 - 0.9092*(fs/f0))
```

Same load range advantage: RL > Z0*Mvdc (compatible with typical applications).

#### Component Stresses (Boost ZCS-QRC)

```
Switch peak current:  ISM = (Q/Mvdc + 1)*Ii
Switch peak voltage:  VSM = Vo
Diode peak current:   IDM = (Q/Mvdc + 1)*Ii
Diode peak voltage:   VDM = 2*Vo
```

### Comparison: ZVS-QRC vs ZCS-QRC

| Property | ZVS-QRC | ZCS-QRC |
|----------|---------|---------|
| Soft switching at | Turn-ON (zero voltage) | Turn-OFF (zero current) |
| Resonant Cr placement | Parallel with switch | Parallel with Lr+switch |
| Resonant Lr placement | Series with switch+Cr | Series with switch |
| Load range for soft switching | 0 < RL < Z0*Mvdc (LOW RL) | RL > Z0*Mvdc (HIGH RL) |
| Practical compatibility | Needs impedance inverter | Naturally compatible |
| Switch voltage stress | (1+Q/Mvdc)*Vi, increases with Q | Vi (constant) |
| Switch current stress | Io (constant) | (Q/Mvdc+1)*Io, increases with Q |
| Absorbs Coss? | Yes (into Cr) | No |
| Absorbs diode Cj? | No (causes ringing) | Yes (into Cr) |
| Best for | IGBTs, high-voltage switches | MOSFETs, body-diode issues |
| Preferred devices | MOSFETs (antiparallel diode free) | MOSFETs + series diode (or IGBTs) |

### Multi-Resonant Converters (ZVS-MRC)

Multi-resonant converters use THREE resonant elements: Lr, Cr1 (across transistor), and Cr2 (across diode). This achieves ZVS for BOTH the transistor and the rectifying diode, eliminating:
- Transistor turn-on capacitive loss
- Diode reverse recovery loss
- Ringing between diode Cj and Lr

The penalty is: higher voltage stress on the switch (typically 3-4x Vi for buck), more complex analysis (requires numerical solution of multiple coupled nonlinear equations), and a narrower range of load for which both ZVS conditions are satisfied.

### Quasi-Resonant Flyback and Forward

All the QR switch cells can be applied to isolated topologies by replacing the buck switch cell:

**ZVS-QR Flyback**: Cr across the primary MOSFET (absorbs Coss + reflected secondary capacitance), Lr = transformer leakage inductance. The resonant ring during turn-off transition brings the MOSFET drain voltage back to zero before the next turn-on.

**ZCS-QR Flyback**: Lr in series with the primary MOSFET, Cr across Lr+switch. The switch current rings sinusoidally to zero before turn-off.

**Design equations for QR flyback**: Same as QR buck with:
```
Vi_eff = Vi (on-interval) or n*Vo (off-interval)
Mvdc relates to duty cycle through the flyback gain: Vo/Vi = n*D/(1-D) modified by the resonant intervals
```

### Practical Design Considerations for QR Converters

1. **Frequency variation**: QR converters use variable frequency. The frequency range is typically 2:1 or wider, complicating EMI filter and magnetics design.

2. **Valley switching**: In practical QR flyback converters, the MOSFET turns on at the first valley of the drain voltage ringing after the demagnetization interval (quasi-resonant valley switching). This is a widely used technique in LED drivers and low-power adapters.

3. **Multi-mode operation**: At light load, the switching frequency would need to increase beyond practical limits. Most QR controllers implement burst mode or frequency clamping at light load.

4. **Component selection for Lr and Cr**: In many practical designs, Lr = transformer leakage inductance and Cr = MOSFET Coss + parasitic capacitance. No additional resonant components are needed if the parasitic values are in the right range.

### Additional QR Converter Types (from Kazimierczuk Ch22)

The buck-boost variants and generalized formulas complement the buck and boost ZVS/ZCS converters already covered above.


### Buck-Boost ZVS Quasi-Resonant Converter

**DC voltage transfer function** (h = 0):
```
MvDC = Vo/Vi = 1 / ((3*pi + 2)/(4*pi) * (f0/fs) - 1)
     ~ 1 / (0.9092*(f0/fs) - 1)
```

**Component stresses**:
```
ISM = (MvDC + 1) * Io                              (peak switch current)
VSM = (Q/MvDC + 1) * (MvDC + 1) * Vi               (peak switch voltage)
IDM = 2 * (MvDC + 1) * Io                           (peak diode current)
VDM = Vi + Vo                                       (peak diode voltage)
```

---

### Buck ZCS Quasi-Resonant Converter

**DC voltage transfer function** (h = 0):
```
MvDC = Vo/Vi = (3*pi + 2)/(4*pi) * (fs/f0)
     = 0.9092 * (fs/f0)
```

For full-wave (h > 0), MvDC is nearly independent of Q (load), making it very suitable for wide load range operation.

**Component stresses**:
```
VSM = Vi = Vo/MvDC                         (peak switch voltage)
ISM = (Q/MvDC + 1) * Io                    (peak switch current, increases with RL!)
IDM = Io                                   (peak diode current)
VDM = 2*Vi = 2*Vo/MvDC                     (peak diode voltage)
```

**Design equations** (h=0, n=1):
```
Z0 = RL/Q = RL/MvDC
f0 = fs / (MvDC/0.9092)
Lr = Z0/Q = RL/(omega0*MvDC)
Cr = Q/(omega0*RL) = MvDC/(omega0*RL)
D = A = fs/f0 = MvDC/0.9092  (full-wave)
```

---

### Boost ZCS Quasi-Resonant Converter

**DC voltage transfer function** (h = 0):
```
MvDC = 1 / (1 - 0.9092*(fs/f0))
```

**Component stresses**:
```
VSM = Vo                                   (peak switch voltage)
ISM = (Q + MvDC) * Io                      (peak switch current)
IDM = MvDC * Io                            (peak diode current)
VDM = 2*Vo                                 (peak diode voltage)
```

---

### Buck-Boost ZCS Quasi-Resonant Converter

**DC voltage transfer function** (h = 0):
```
MvDC = 1 / (1 - 0.9092*(fs/f0)) - 1
     = 0.9092*A / (1 - 0.9092*A)
```
where A = fs/f0.

**Component stresses**:
```
ISM = 2*(MvDC + 1)*Io                      (peak switch current, at h=0 boundary)
VSM = Vi + Vo = (MvDC + 1)*Vi              (peak switch voltage)
IDM = (MvDC + 1)*Io                        (peak diode current)
VDM = 2*(Vi + Vo)                          (peak diode voltage)
```

---

### Generalization of QR DC-DC Converters

All QR converters follow the same pattern as their PWM counterparts, with a correction factor:

**ZVS converters**:
```
Buck:       MvDC = D - Yv
Boost:      MvDC = 1 / (1 - (D - Yv))
Buck-Boost: MvDC = (D - Yv) / (1 - (D - Yv))

where Yv = MvDC * (1-h)^2 / (4*pi*sqrt(1-h^2)) * (fs/f0)
```

**ZCS converters**:
```
Buck:       MvDC = D - Yi
Boost:      MvDC = 1 / (1 - (D - Yi))
Buck-Boost: MvDC = (D - Yi) / (1 - (D - Yi))

where Yi = MvDC * (1-h)^2 / (4*pi*sqrt(1-h^2)) * (fs/f0)
```

The correction terms Yv and Yi have the same form but apply in complementary load ranges:
- ZVS: works for 0 < RL < Z0*MvDC (light load loses ZVS)
- ZCS: works for RL > Z0*MvDC (heavy load loses ZCS)

---

### ZVS vs ZCS Comparison

| Property | ZVS-QRC | ZCS-QRC |
|---|---|---|
| Resonant Cr placement | Parallel with switch | Parallel with switch+Lr |
| Resonant Lr placement | Series with switch+Cr | Series with switch |
| Parasitic absorbed | MOSFET Coss -> Cr | Diode Cj -> Cr |
| NOT absorbed | Diode Cj (causes ringing) | MOSFET Coss (causes loss) |
| Turn-on switching loss | Zero (ZVS) | Non-zero (Coss discharge) |
| Turn-off switching loss | Non-zero | Zero (ZCS) |
| Soft-switching range | 0 < RL < Z0*MvDC (fails at light load) | RL > Z0*MvDC (fails at heavy load) |
| Preferred for | MOSFETs (eliminates Coss loss) | IGBTs, GTO (eliminates tail current loss) |
| Circulating current | Higher | Lower |
| Switch voltage stress | Higher (~2x) | Same as PWM |
| Switch current stress | Same as PWM | Higher (~2x) |

---

### Multiresonant Converters (ZVS-MRC, ZCS-MRC)

**Concept**: add a second resonant capacitor Crd in parallel with the rectifying diode to a ZVS-QRC. This absorbs BOTH the MOSFET Coss (into Crs) and the diode Cj (into Crd), achieving double-zero-voltage switching.

**ZVS-MRC resonant elements**:
```
Crs: parallel with switch (absorbs MOSFET Coss)
Crd: parallel with diode (absorbs diode Cj)
Lr:  in series between the two parallel combinations
```

**Three resonant frequencies** (ZVS-MRC):
```
f01 = 1/(2*pi*sqrt(Lr*Crd))               (Lr-Crd, S ON, D OFF interval)
f02 = 1/(2*pi*sqrt(Lr*(Crs+Crd)/(Crs*Crd)))  (all three, S OFF, D OFF)
f03 = 1/(2*pi*sqrt(Lr*Crs))               (Lr-Crs, S OFF, D ON interval)
```

Typical design: Crd/Crs = 3. Both switch and diode achieve ZVS.

**ZCS-MRC**: dual of ZVS-MRC. Adds resonant inductor Lrd in series with diode. Both switch and diode achieve ZCS.

---

### Zero-Voltage Transition (ZVT) PWM Converters

ZVT-PWM converters add an auxiliary switch and resonant circuit to a conventional PWM converter. Unlike QR converters, ZVT maintains:
- **Fixed switching frequency** (PWM, not FM)
- **ZVS on the main switch** via the auxiliary circuit
- **Soft turn-off of the rectifying diode** (eliminates reverse recovery)

**Auxiliary circuit** (ZVT boost example):
```
Components: auxiliary switch S2, auxiliary diode D2, resonant Cr (parallel with S1), resonant Lr
```

**Operating principle** (7 intervals per cycle):
1. S2 turns on; Lr current ramps up, main diode D1 current ramps down (soft turn-off)
2. Cr-Lr resonance reduces main switch voltage to zero
3. Main switch S1 turns on at zero voltage (ZVS)
4. S2 turns off; Lr current ramps down through D2
5. Normal PWM interval (S1 ON)
6. S1 turns off; Cr charges to Vo
7. D1 conducts (normal freewheeling)

**Advantages over QR converters**:
- Fixed frequency operation (easier EMI filter, magnetics design)
- ZVS maintained over wide load range
- Lower circulating current than QR
- Parasitic Coss and Cj absorbed

**Disadvantages**:
- Additional switch and diode (cost, complexity)
- Auxiliary circuit losses at high frequency
- Gate driver complexity for auxiliary switch timing

---

### Zero-Current Transition (ZCT) PWM Converters

Dual of ZVT. Adds auxiliary circuit to achieve ZCS on the main switch at fixed frequency. The auxiliary resonant inductor creates a current pulse that brings the main switch current to zero before turn-off.

---

### QR Converter Design Guidelines (Practical)

1. **Choose ZVS for MOSFETs** (eliminates Coss switching loss, dominant at high frequency). Choose ZCS for IGBTs (eliminates tail current loss).

2. **Half-wave vs full-wave**:
   - Half-wave ZVS (h < 0): wider frequency range needed, MvDC depends on Q (load)
   - Full-wave ZVS (h > 0): narrow frequency range, MvDC nearly independent of Q, but requires series diode (higher conduction loss, Coss not dischargeable)
   - Practical: use half-wave ZVS (standard MOSFET with body diode)

3. **Resonant component selection**:
```
Lr: choose to absorb MOSFET Coss and achieve desired Z0
    Lr = Z0 * Q / omega0
    In practice: Lr = transformer leakage inductance (isolated converters)

Cr: choose to achieve resonant frequency and absorb parasitics
    Cr = Q / (omega0 * Z0)
    In practice: Cr = MOSFET Coss + PCB stray capacitance
```

4. **Load range limitation**: ZVS-QRC loses ZVS at light load. Solutions:
   - Add impedance inverter (transformer with resonant network)
   - Use burst mode at light load
   - Switch to ZVT-PWM for wide load range

5. **Frequency range**: typical 2:1 or wider. This is the main disadvantage of QR converters compared to fixed-frequency solutions.

6. **Component stresses**: ZVS converters have ~2x voltage stress on the switch compared to PWM. ZCS converters have ~2x current stress. Size components accordingly.

## Practical ZVS Implementation (from Andreycak SLUA159)

Reference: Bill Andreycak, "Zero Voltage Switching Resonant Power Conversion," TI/Unitrode Application Note U-138 (SLUA159).

### ZVS Concept

Zero voltage switching is conventional square wave power conversion during the switch's on-time with resonant switching transitions during the off-time. The conversion frequency varies (constant off-time, variable on-time) to regulate the output voltage. This is analogous to fixed-frequency PWM with adjustable duty cycle.

During the switch off-time, an L-C tank circuit resonates, traversing the voltage across the switch from zero to its peak and back to zero. The switch is reactivated at zero voltage, eliminating:
- MOSFET output capacitance (Coss) discharge losses (regardless of frequency and voltage)
- Miller charge effects on gate drive (VDS = 0 at turn-on)
- Switching transition losses

ZVS is applicable to all buck-derived topologies: buck, forward, half-bridge, and full-bridge.

### ZVS Benefits

- Zero power "lossless" switching transitions
- Reduced EMI/RFI at transitions
- No Coss discharge loss
- Same peak currents as square wave (unlike ZCS which has 2x peak current)
- High efficiency at high voltage inputs and any frequency
- Parasitic circuit L and C can be incorporated into the resonant tank
- Reduced gate drive requirements (no Miller effect at turn-on)
- Short circuit tolerant

### ZVS Differences from Square Wave

- Variable frequency operation (frequency inversely proportional to load)
- Higher off-state voltages in single-switch unclamped topologies
- Requires more sophisticated control circuit

### ZVS Switching Cycle Analysis (Buck Regulator Model)

The ZVS buck converter has four distinct intervals per switching cycle:

**Interval 1: Capacitor Charging (t0-t1)**

Switch Q1 turns OFF. Output current IO (constant, maintained by output inductor LO) diverts from the switch into resonant capacitor CR. The capacitor charges linearly:
```
VCR(t) = IO * (t - t0) / CR
```
Duration: t01 = CR * VIN / IO

At t1, VCR = VIN and the catch diode D0 begins conducting.

**Interval 2: Resonant (t1-t2)**

Series L-C resonance begins between LR and CR, stimulated by the initial current IO. The resonant current follows a cosine function:
```
ICR(t) = IO * cos(wR * (t - t1))
VCR(t) = VIN + IO * ZR * sin(wR * (t - t1))
```
where:
```
wR = 1 / sqrt(LR * CR)    (resonant angular frequency)
ZR = sqrt(LR / CR)         (tank impedance)
fR = wR / (2*pi)           (resonant frequency)
```

Peak switch voltage:
```
VDS_peak = VIN + IO * ZR = VIN * (1 + IO_max / IO_min)    for unclamped topologies
```

The interval ends when VCR returns to zero. Duration:
```
t12 = pi/wR + (1/wR) * arctan(sin_ratio / (1 - sin_ratio^2))
```
where sin_ratio = VIN / (IO * ZR).

Maximum duration (270 degrees of resonance, at min load / max line):
```
t12_max = 3*pi / (2*wR)
```

**Interval 3: Inductor Charging (t2-t3)**

At t2, VCR = VDS = 0. Switch Q1 is activated (ZVS condition). The resonant inductor current ramps linearly from -IO to +IO:
```
ILR(t) = -IO + (VIN / LR) * (t - t2)
t23 = 2 * LR * IO / VIN
```

Key observation: at t2 the switch current is actually flowing in reverse (from source to drain through the body diode). The switch can be turned on any time during the first half of t23 without affecting operation. This provides timing margin for the ZVS transition.

**Interval 4: Power Transfer (t3-t4)**

Once ILR reaches IO, the converter operates identically to a conventional square wave converter. The on-time of this interval is modulated by the control loop to regulate the output voltage. Increasing t34 (lowering frequency) raises the output voltage; decreasing it (raising frequency) lowers it.

### Resonant Component Selection

**Tank impedance**: Must satisfy the condition for resonance at minimum load:
```
ZR >= VIN_max / IO_min    (ensures resonant voltage swing reaches zero)
```

**Resonant inductor**:
```
LR = ZR / wR
```
Note: the calculated value does NOT include transformer leakage inductance or wiring inductance, which are additive.

**Resonant capacitor**:
```
CR = 1 / (ZR * wR)
```
Note: the calculated value does NOT include MOSFET output capacitance (Coss) which is in parallel. For multi-switch topologies (half-bridge, full-bridge), all switch capacitances must be accounted for.

### Maximum Conversion Frequency

At minimum on-time (t34 -> 0) and minimum load (IO -> IO_min):
```
f_conv_max = KT * fR
```
where KT is the topology coefficient. For the buck regulator and derivatives:
```
KT_max = 2 / (2 + 3*pi) ~ 0.175
f_conv_max ~ 0.175 * fR
```

This means the resonant frequency should be approximately 5-6x the maximum desired conversion frequency.

### Minimum Output Voltage Limitation

Even at maximum frequency (minimum on-time), some energy is transferred during the capacitor charging interval t01. The minimum achievable output voltage is:
```
VO_min = (VIN_max * IO_min * CR * fR^2) / (2 * KT)    (approximate)
```
This represents less than 7% of minimum input power (typically <1% of total input power with 10:1 load range), so it can usually be neglected.

### Effective Duty Cycle

The power transfer period t34 relative to the total conversion period Tconv defines an effective duty cycle analogous to square wave conversion:
```
D_eff = t34 / Tconv
```
The volt-second balance applies: D_eff * (VIN - V_DSon) = VO + VF (for buck topology).

### Accommodating Losses

Replace ideal values in the equations:
- When switch is ON: replace VIN with (VIN - IO * RDS_on)
- When diode conducts: replace VO with (VO + VF)
- Temperature effects on RDS_on and VF can be included
- Resonant component tolerances affect timing; computer calculation recommended

### Transformer-Coupled Topologies

For forward, half-bridge, and full-bridge converters, transform output voltage and current to primary side:
```
VO' = N * VO       (N = Npri / Nsec)
IO' = IO / N
```

The resonant tank equations become:
```
ZR >= VIN_max / IO'_min = N * VIN_max / IO_min
LR = ZR / wR       (does NOT include transformer leakage)
CR = 1 / (ZR * wR) (does NOT include Coss in parallel)
```

Turns ratio derivation from volt-second product:
```
N = (VIN - IO'*RDS_on) * t34 / ((VO + VF) * (t34 + t23))
```

The transformer leakage inductance is part of the resonant inductance. Design the transformer inductance slightly smaller than LR, then "shim" with a small series inductor for precision applications.

### Clamped Topologies (Half-Bridge and Full-Bridge)

In bridge configurations:
- Switch voltage is clamped to DC input rails by body diodes (VDS_max = VIN, not VIN*(1+IO_max/IO_min))
- The resonant interval is shortened: the opposite switch must be activated as soon as its voltage reaches zero (body diode clamps)
- Transformer reset is automatic due to bidirectional switching
- Series transformer leakage inductance becomes a beneficial additive to the resonant inductor value
- All parasitic inductances generally snubbed in square wave designs can be incorporated

For the half-bridge: VIN in the equations is half the bulk rail-to-rail voltage. CR is the parallel combination of capacitors across each switch.

### ZVS Design Procedure (Buck-Derived, Continuous Current)

1. List all input/output specs: VIN min/max, VO, IO min/max
2. Estimate maximum switch voltage:
   - Unclamped (buck, forward): VDS_max = VIN_max * (1 + IO_max/IO_min)
   - Clamped (bridges): VDS_max = VIN_max
   - If VDS_max is too high, raise IO_min if possible
3. Select resonant tank frequency fR (hint: fR = wR / 2*pi)
4. Calculate ZR, LR, CR from the equations above
5. Calculate all interval durations (t01, t12, t23, t34) across all line/load combinations. Use a computer program (BASIC listing provided in original paper).
6. Analyze results: verify frequency range is practical, voltages and currents are within component ratings
7. Finalize circuit:
   - Derive transformer turns ratio (non-buck)
   - Design output filter for lowest conversion frequency
   - Select components (MOSFET, diode)
8. Breadboard using RF layout techniques. Parasitic inductances and capacitances resonate upon stimulation.
9. Debug: accommodate component parasitics and layout effects

### ZVS Forward Converter Design Example (50 W)

**Specifications**:
```
VIN = 18-26 V DC
VO = 5.0 V
IO = 2.5-10 A
fR = 500 kHz
```

**Resonant tank**:
```
ZR >= VIN_max / IO_min = 26/2.5 = 10.4 ohm (used 10.526 ohm after RDS_on correction)
CR = 1/(ZR * wR) = 30.3 nF
LR = ZR/wR = 3.35 uH
VDS_max = 26 * (1 + 10/2.5) = 130 V
```

**Results across line and load** (computed):

| VIN (V) | IO (A) | t01 (us) | t12 (us) | t23 (us) | t34 (us) | Tconv (us) | f_conv (kHz) |
|---|---|---|---|---|---|---|---|
| 18 | 2.5 | 0.22 | 1.29 | 0.93 | 1.39 | 3.83 | 261 |
| 18 | 10 | 0.05 | 1.06 | 3.73 | 6.68 | 11.51 | 87 |
| 27 | 2.5 | 0.33 | 0.52 | 0.62 | 0.44 | 1.91 | 524 |
| 27 | 10 | 0.08 | 1.09 | 2.48 | 1.60 | 5.25 | 190 |

Conversion frequency range: 87-524 kHz (approximately 6:1 range).

### Control Circuit: UC3861-64 Family

The UC3861-64 ZVS controllers provide:
- **One-shot timer**: Programs maximum off-time (3:1 range capability). Set for maximum t_off, then modulated by zero-crossing detection.
- **Zero voltage detection**: 0.5 V threshold senses when VDS crosses zero, initiating turn-on. The offset accommodates propagation delays to prevent non-zero switching.
- **VCO**: Voltage controlled oscillator sets conversion frequency range. Program for min fC (75 kHz) and max fC (350 kHz).
- **Soft start / restart delay**: Single capacitor, 19:1 ratio of restart delay to soft-start time (e.g., CSS = 1 uF gives TSS = 10 ms, TRD = 200 ms).
- **Fault protection**: Primary current sensing via current transformer for overcurrent/short circuit detection. 3 V fault threshold.
- **Gate drive**: 1 A peak totem pole outputs, 30 ns rise/fall into 1 nF. Two outputs configurable for unison or alternating operation.

### Loop Compensation

ZVS uses single voltage feedback loop (like voltage-mode PWM). The output filter has a two-pole-zero pair. Compensation guidelines:
- Crossover below 1/10 of minimum switching frequency
- Two compensator zeros placed at the output filter double-pole frequency
- High-frequency pole at the output capacitor ESR zero
- Optimize for highest low-frequency gain with adequate phase margin

Alternative: use dual-loop (multi-loop control or average current mode) to eliminate one output pole.

### Avoiding Parasitics

The catch diode junction capacitance resonates with circuit inductance and package leads. Solutions:
- R-C snubber across the diode (dissipative but effective)
- **Multi-resonant ZVS**: shunt the diode with a capacitor CD much larger than its junction capacitance, introducing favorable switching characteristics for both switch and diode
- **Current mode controlled ZVS**: use two loops (outer voltage, inner current) with UC3843A for the current loop and UC3864 for ZVS timing. The current mode makes the power stage a voltage-controlled current source, eliminating the two-pole output inductor characteristic.

### ZVS Off-Line 300 W Design Example

An off-line 300 W multiple-output power supply using the half-bridge ZVS topology is presented as a second design example in the original paper, using the UC3861 controller. The half-bridge configuration:
- Clamps switch voltages to the input bulk rails
- Enables automatic transformer core reset
- Incorporates transformer leakage and wiring inductance as beneficial resonant inductance
- Achieves significant efficiency improvements over square wave counterparts at high input voltages

### Performance Comparison: ZVS vs Square Wave

ZVS is most advantageous for:
- **High voltage inputs**: Coss discharge losses (0.5*Coss*VDS^2*fS) become significant at high VDS and high fS. ZVS eliminates this entirely.
- **High frequency operation**: Switching transition losses scale with frequency in square wave converters; ZVS transitions are lossless.
- **Bridge topologies**: Transformer leakage inductance and circuit parasitics become beneficial resonant elements rather than loss-producing snubbed parasitics, further enhancing efficiency.

Main limitation: the frequency range of ZVS converters is typically wide (6:1 in the 50 W example), which complicates EMI filter design and magnetics optimization. The minimum load must be specified to ensure the resonant condition is maintained (ZR >= VIN/IO_min).
