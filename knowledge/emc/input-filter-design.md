# Input Filter Design (from Erickson Ch17)

Reference: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapter 17, pp.678-726.

## The Problem: Filter-Converter Interaction

Switching converters inject pulsating currents into the power source. The Fourier series of the buck converter input current is:

```
i_g(t) = D*I + sum_{k=1}^{inf} (2I / k*pi) * sin(k*pi*D) * cos(k*omega*t)
```

Input filters are required to attenuate these harmonics (typically 80 dB or more) to meet conducted EMI regulations. However, adding an input filter changes the converter dynamics, potentially destabilizing the control loop.

**The core problem**: An undamped L-C input filter adds complex poles and RHP zeros to the control-to-output transfer function G_vd(s). If the filter resonant frequency is near or below the loop crossover frequency, the phase margin becomes negative and instability results.

## Middlebrook Stability Criterion

The modified control-to-output transfer function with an input filter is:

```
G_vd(s) = G_vd(s)|_{Z_o=0} * [1 + Z_o(s)/Z_N(s)] / [1 + Z_o(s)/Z_D(s)]
```

Where:
- `G_vd(s)|_{Z_o=0}` = original transfer function without filter
- `Z_o(s)` = output impedance of the input filter
- `Z_N(s)` = converter input impedance with output voltage nulled (via ideal feedback)
- `Z_D(s)` = converter open-loop input impedance (d_hat = 0)

**Middlebrook's impedance inequalities** (sufficient conditions for negligible interaction):

```
||Z_o|| << ||Z_N||   for all frequencies
||Z_o|| << ||Z_D||   for all frequencies
```

When both inequalities are satisfied, the correction factor approaches unity and the input filter does not alter G_vd(s).

Similarly, for the output impedance:

```
||Z_o|| << ||Z_e||   and   ||Z_o|| << ||Z_D||
```

where `Z_e = Z_i|_{v_hat=0}` is the converter input impedance with output shorted.

## Output Impedance of the Filter

For a simple L_f-C_f input filter:

```
Z_o(s) = sL_f / (1 + s^2 * L_f * C_f)
```

The filter output impedance peaks at the filter resonant frequency:

```
f_f = 1 / (2*pi*sqrt(L_f * C_f))
```

At resonance, the undamped filter has `||Z_o|| = sqrt(L_f/C_f)` (the characteristic impedance). This peak must remain below `||Z_N||` and `||Z_D||` at all frequencies.

## Input Impedance of the Converter

### Z_N: Null-output input impedance

Z_N is the converter input impedance when an ideal controller nulls the output voltage. At DC, for a lossless converter regulating its output:

```
Z_N(DC) = -V_g / I_g = -R / M^2
```

This is a **negative resistance** -- the key source of instability. An increase in input voltage causes a decrease in input current (constant power load behavior).

### Z_D: Open-loop input impedance

Z_D is the converter input impedance with duty cycle held constant (d_hat = 0).

### Input filter design criteria for basic converters

| Converter | Z_N(s) | Z_D(s) | Z_e(s) |
|-----------|--------|--------|--------|
| Buck | -R/D^2 | (R/D^2)(1 + sL/R + s^2LC) / (1 + sRC) | sL/D^2 |
| Boost | -D'^2*R * (1 - sL/(D'^2*R)) | D'^2*R * (1 + sL/(D'^2*R) + s^2*LC/D'^2) / (1 + sRC) | sL |
| Buck-boost | -(D'^2*R/D^2)(1 - sDL/(D'^2*R)) | (D'^2*R/D^2)(1 + sL/(D'^2*R) + s^2*LC/D'^2) / (1 + sRC) | sL/D^2 |

**Key observations:**
- Z_N is always negative at DC (constant power load)
- Z_D is positive and equals the open-loop input impedance
- At low frequencies, Z_N dominates stability concerns
- At high frequencies, Z_D (inductive) dominates

## Designing for Stability

### Impedance inequality design approach

1. Plot `||Z_N(jw)||` and `||Z_D(jw)||` vs. frequency for the converter
2. Design filter to achieve required attenuation at f_sw
3. Verify that `||Z_o(jw)||` remains well below both `||Z_N||` and `||Z_D||` at all frequencies
4. A practical margin is `||Z_o|| < 1/3 * min(||Z_N||, ||Z_D||)` (about 10 dB margin)

### Exact stability criterion using minor loop gain

The closed-loop input impedance Z_i transitions between:
- Z_N at frequencies well below loop crossover f_c (where loop gain is large)
- Z_D at frequencies well above f_c (where loop gain is small)

The minor loop gain is:

```
T_m(s) = Z_o(s) / Z_i(s)
```

Stability requires that the Nyquist plot of T_m does not encircle -1. Practically:
- The phase margin of T_m at each crossover frequency must be positive
- If `||Z_o||` exceeds `||Z_i||` at any frequency, check phase carefully

### Modified loop gain approach

The modified loop gain with input filter is:

```
T'(s) = T(s) * [1 + Z_o/Z_N] / [1 + Z_o/Z_D]
```

where T(s) is the original loop gain. The modified phase margin must remain positive.

## Damping Strategies

### Three practical damping networks for single-section L_f-C_f filter:

#### 1. R_f-C_b parallel damping (most common)

Damping resistor R_f in series with blocking capacitor C_b, both in parallel with C_f.

- C_b blocks DC to avoid power loss in R_f
- C_b >> C_f required for effective damping (typically C_b >= 4*C_f)
- Optimal damping (for given C_b/C_f ratio n = C_b/C_f):

```
R_f_opt = 1/(2*pi*f_f) * sqrt((n+1)^3 / (n^2 * C_f * L_f))

Simplified: R_f_opt ~ sqrt(L_f / C_f) * sqrt(1/n)  for large n

Peak ||Z_o|| at optimum: ||Z_o||_max = sqrt(L_f / C_f) * (2/(n+1)) * sqrt((n+1)/n)
```

#### 2. R_f-L_b parallel damping

Damping resistor R_f in parallel with L_f, with high-frequency blocking inductor L_b in series with R_f.

- L_b maintains -40 dB/decade rolloff at high frequencies
- L_b << L_f (typically L_b = L_f/4 to L_f/8)
- Optimal R_f:

```
R_f_opt ~ sqrt(L_f / C_f) * sqrt(n/(n-1))   where n = L_f/L_b
```

#### 3. R_f-L_b series damping

Damping resistor R_f in series with L_f, with DC bypass inductor L_b in parallel with R_f.

- L_b carries the DC current, avoiding power loss in R_f
- Requires L_b > L_f for effective damping

### Damping design guidelines

- Increasing damping reduces the peak of Z_o but may reduce high-frequency attenuation
- The blocking element (C_b or L_b) should be large enough that damping is effective but not so large as to dominate size/cost
- For R_f-C_b: aim for n = C_b/C_f between 4 and 10
- For R_f-L_b: aim for n = L_f/L_b between 4 and 8

## Design Procedure

### Step-by-step input filter design:

1. **Determine required attenuation**: Calculate the harmonic current magnitudes at f_sw and compare with EMI limits. Required attenuation = 20*log10(I_harmonic / I_limit).

2. **Choose filter topology**: Single-section for moderate attenuation (40-80 dB), two-section cascade for higher attenuation (>80 dB).

3. **Select filter resonant frequency**: f_f should be well below f_sw for adequate attenuation. For a single-section LC filter with -40 dB/decade rolloff:

```
f_f <= f_sw / 10^(Attenuation_dB/40)
```

4. **Compute L_f and C_f**: Choose L_f*C_f = 1/(2*pi*f_f)^2. Trade off between inductor size and capacitor size.

5. **Plot Z_N and Z_D for the converter**: Use expressions from the table above.

6. **Check impedance inequalities**: Verify ||Z_o|| < ||Z_N||/3 and ||Z_o|| < ||Z_D||/3 at all frequencies. The critical frequency is usually near f_f where Z_o peaks.

7. **Add damping**: If the undamped Z_o peak violates the inequalities, add a damping network. Choose R_f and blocking element values using the optimization formulas above.

8. **Verify**: Plot the modified G_vd(s) and loop gain to confirm minimal impact. Check phase margin of the modified system.

### For cascaded (two-section) filters:

Each section should be designed with well-separated resonant frequencies (at least factor of 5 apart). The impedance inequality approach extends:

```
||Z_o1|| << ||Z_i2||   (first section output impedance << second section input impedance)
```

This ensures the two sections do not interact. Each section must also be independently damped.

## Worked Examples

### Buck converter with single-section input filter

**Given**: D = 0.5, L = 100 uH, C = 100 uF, R = 3 ohm, V_g = 30V

**Filter**: L_f = 330 uH, C_f = 470 uF

**Without damping**:
- Filter resonant frequency: f_f = 1/(2*pi*sqrt(330e-6 * 470e-6)) = 404 Hz
- Z_o peak (undamped): sqrt(330e-6/470e-6) = 0.838 ohm
- Z_N(DC) = -R/D^2 = -3/0.25 = -12 ohm
- Z_D(DC) = R/D^2 = 12 ohm
- Impedance inequality satisfied at DC (0.838 << 12)
- BUT at resonance, the undamped Q can make Z_o >> Z_N, violating the inequality

The undamped filter causes a glitch in G_vd at f_f and introduces 360 degrees of additional phase lag. If f_c > f_f, the system is unstable.

**With R_f-C_b damping** (R_f = 0.8 ohm, C_b = 4700 uF = 10*C_f):
- Peak Z_o is reduced well below Z_N and Z_D
- Modified G_vd(s) closely tracks the original (no-filter) response
- Phase margin is preserved

### Design rule of thumb

For the damped input filter to have negligible effect on converter dynamics:
- The filter resonant frequency f_f should be at least a factor of 5 below the converter LC resonant frequency f_o
- The damping should yield Q_f <= 1 (critically damped or overdamped)
- The peak ||Z_o|| should be at least 10 dB below the minimum of ||Z_N|| and ||Z_D||

### Current-programmed converters

The impedance inequalities also apply to current-programmed converters, but Z_N and Z_D are modified because the effective input impedance changes with current-mode control. In general, current programming increases the converter input impedance at low frequencies, making the input filter design somewhat easier. See Erickson Section 18.4.4 for details.
