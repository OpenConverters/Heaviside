# Digital Control of Power Converters (from Erickson Ch19)

Reference: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapter 19, pp.807-847.

## Why Digital Control

Digital control of switched-mode power converters offers:
- Programmability and flexibility (compensator tuning via software)
- Integration of complex control, power management, and monitoring
- Adaptive algorithms and nonlinear control strategies
- Communication interfaces for system-level power management
- Elimination of component tolerances in the compensator

Digital control is well-established in high-power applications (motor drives, grid-tied inverters). For high-frequency DC-DC converters (hundreds of kHz to MHz), challenges include quantization effects, computational delays, and controller cost/power consumption.

Commercial DPWM controller ICs are now widely available from multiple vendors.

## Sampling and Quantization

### Digital control loop architecture

The digital control loop consists of:
1. A/D converter: samples the output voltage (or other feedback signal)
2. Digital compensator: computes the control law in discrete time
3. DPWM (Digital Pulse-Width Modulator): generates the gate drive signal

### A/D Conversion

The A/D converter quantizes the analog feedback signal into discrete levels. Key parameters:
- Resolution: N_ADC bits, giving 2^N_ADC quantization levels
- Sampling rate: typically once per switching period (synchronous sampling)
- Quantization step: Delta_ADC = V_ref / 2^N_ADC

### Digital Pulse-Width Modulation

The DPWM converts the digital control word into a pulse width. Key parameters:
- Resolution: N_DPWM bits
- Minimum duty cycle step: Delta_d = 1 / 2^N_DPWM
- Minimum output voltage step: Delta_v = V_g * Delta_d = V_g / 2^N_DPWM

### Ideal quantization characteristics

Both A/D and DPWM exhibit staircase input-output characteristics. The quantization introduces nonlinearity that can cause limit cycling (see below).

## DPWM Resolution Requirements

The DPWM resolution must be fine enough to avoid steady-state limit cycling of the output voltage. The key requirement:

```
DPWM resolution step in output voltage <= ADC resolution step in output voltage

V_g / 2^N_DPWM <= V_ref / 2^N_ADC
```

This gives the minimum DPWM resolution:

```
N_DPWM >= N_ADC + log2(V_g / V_ref)
```

**Typical example**: For a 12V-to-1V buck converter with 10-bit ADC and V_ref spanning 0.9V to 1.1V:
- Delta_ADC = 0.2V / 1024 = 195 uV
- Required Delta_d <= 195 uV / 12V = 16.3 ppm
- N_DPWM >= log2(1/16.3e-6) = 16 bits

This very high DPWM resolution is a practical challenge. Solutions include:
- Sigma-delta DPWM (dithering between adjacent duty cycles)
- Hybrid analog-digital approaches
- Counter-based DPWM with very high clock frequencies

## z-Domain Modeling

### Sampling and the z-transform

The continuous-time compensator transfer function G_c(s) must be converted to a discrete-time transfer function G_cd(z). The z-transform variable is related to the s-domain by:

```
z = e^(sT_s)
```

where T_s is the sampling period (= switching period for synchronous sampling).

### Delays in the digital control loop

The digital loop introduces delays:
- A/D conversion time: typically a fraction of T_s
- Computation time: one or more clock cycles
- DPWM update: may occur at next switching cycle

**Total effective delay**: approximately 1 to 1.5 switching periods, modeled as:

```
G_delay(s) = e^(-s*T_d)    where T_d ~ T_s to 1.5*T_s
```

In the z-domain, a one-sample delay is simply z^(-1).

This delay limits the achievable bandwidth. As a rule of thumb:

```
f_c_max ~ f_sw / (2*pi * T_d/T_s) ~ f_sw / 6 to f_sw / 10
```

### Frequency response of discrete-time systems

The frequency response of a discrete-time system G_cd(z) is evaluated at z = e^(j*omega*T_s):

```
G_cd(e^(j*omega*T_s)) = |G_cd| * e^(j*angle(G_cd))
```

The frequency response is periodic with period f_s (the sampling frequency). Nyquist frequency is f_s/2.

## Digital Redesign of Analog Compensators (Tustin, matched pole-zero)

### Tustin (Bilinear) Transform

The most common s-to-z mapping. Replace s in the continuous-time compensator G_c(s):

```
s -> (2/T_s) * (z - 1) / (z + 1)
```

So:

```
G_cd(z) = G_c(s)|_{s = (2/T_s)(z-1)/(z+1)}
```

**Properties of Tustin mapping:**
- Maps the entire left half s-plane to the interior of the unit circle in z
- Stable continuous-time systems map to stable discrete-time systems
- The imaginary axis in s maps to the unit circle in z
- Frequency warping occurs: the mapping is nonlinear in frequency

```
omega_analog = (2/T_s) * tan(omega_digital * T_s / 2)
```

At low frequencies (omega << 1/T_s), the mapping is approximately linear. Near f_s/2, significant warping occurs.

**Frequency pre-warping**: To place critical frequencies (crossover, zeros, poles) at the correct discrete-time frequencies, pre-warp the analog prototype:

```
omega_prewarped = (2/T_s) * tan(omega_desired * T_s / 2)
```

Design the analog compensator using pre-warped frequencies, then apply the Tustin transform.

### Matched Pole-Zero Method

An alternative mapping that directly maps continuous-time poles and zeros to discrete-time:

```
s-domain pole at s = -a  ->  z-domain pole at z = e^(-a*T_s)
s-domain zero at s = -b  ->  z-domain zero at z = e^(-b*T_s)
```

The DC gain is matched between continuous and discrete-time transfer functions.

**Advantage**: No frequency warping -- poles and zeros are placed at the correct frequencies.

### Design procedure for Tustin redesign

1. Design the analog compensator G_c(s) using continuous-time techniques (Chapter 9)
2. Identify critical frequencies (crossover, compensator poles and zeros)
3. Pre-warp these frequencies if they are a significant fraction of f_s/2
4. Apply the Tustin transform to obtain G_cd(z)
5. Verify the discrete-time frequency response matches the analog design intent
6. Express G_cd(z) as a difference equation for implementation

### Example: PID compensator

Continuous-time PID:

```
G_c(s) = K * (1 + s/omega_z1)(1 + s/omega_z2) / (s/omega_p0 * (1 + s/omega_p1))
```

After Tustin transform, each s factor becomes a ratio of (z-1)/(z+1) terms. The result is a ratio of polynomials in z, which can be implemented as a difference equation.

## Direct Digital Design

Instead of redesigning an analog compensator, one can design directly in the z-domain:

1. Obtain the discrete-time plant model G_p(z) (by z-transforming the sampled plant)
2. Specify desired closed-loop performance (bandwidth, phase margin, disturbance rejection)
3. Design G_cd(z) directly to meet specifications
4. Express as a difference equation

The discrete-time plant includes the effect of the zero-order hold (ZOH) and any computational delays:

```
G_p(z) = Z{ (1 - e^(-sT_s))/s * G_vd(s) * e^(-sT_d) }
```

where Z{} denotes the z-transform operation.

**Advantage**: Accounts exactly for sampling effects without approximation.

## Limit Cycling and Resolution

### Steady-state limit cycling

In digital control loops, quantization in the A/D and DPWM can cause the output voltage to oscillate between adjacent quantization levels in steady state, even when the system is stable in the linear sense. This is called limit cycling.

**Conditions to avoid limit cycling:**

1. DPWM resolution condition (necessary):

```
Delta_v_DPWM <= Delta_v_ADC
```

The DPWM voltage step must be smaller than the ADC voltage step. Otherwise, the loop cannot settle to a single ADC code.

2. Integral action: The compensator must include an integrator (pole at z = 1). Without integral action, a steady-state error remains that the quantized loop cannot resolve.

3. Sufficient resolution: Both ADC and DPWM must have enough bits that the quantization-induced oscillations are acceptable (typically below the output voltage specification).

### Practical resolution guidelines

| Application | Typical ADC bits | Typical DPWM bits |
|-------------|------------------|--------------------|
| POL regulator (1V output) | 10-12 | 12-16 |
| Isolated DC-DC (48V to 12V) | 10-12 | 10-12 |
| PFC rectifier | 10-12 | 10-12 |
| Motor drive | 10-12 | 10-14 |

### Sigma-delta DPWM

To achieve effective DPWM resolution beyond what the hardware counter provides, a sigma-delta modulator dithers between adjacent duty cycle values:

```
Effective resolution = Hardware resolution + oversampling ratio (in bits)
```

This trades temporal resolution for amplitude resolution, spreading the quantization noise to higher frequencies where the loop gain attenuates it.

## Practical Implementation

### Difference equation form

A general second-order compensator in z-domain:

```
G_cd(z) = (b0 + b1*z^(-1) + b2*z^(-2)) / (1 + a1*z^(-1) + a2*z^(-2))
```

Implemented as the difference equation:

```
u[n] = b0*e[n] + b1*e[n-1] + b2*e[n-2] - a1*u[n-1] - a2*u[n-2]
```

where e[n] is the error signal (reference minus feedback) and u[n] is the control output (duty cycle command).

### Implementation considerations

1. **Fixed-point arithmetic**: Most DPWM controllers use fixed-point (not floating-point). Coefficient quantization can shift poles and zeros, requiring careful word-length selection.

2. **Coefficient quantization**: Compensator coefficients must be representable in the chosen word length. Poles near z = 1 (low-frequency poles like integrators) are most sensitive to coefficient quantization.

3. **Computation time**: The difference equation must complete within one switching period (or a fraction thereof). Higher-order compensators require more multiplications and additions.

4. **Anti-windup**: When the duty cycle saturates (0 or 1), the integrator state must be clamped to prevent windup, just as in analog implementations.

5. **Initialization**: The integrator state and delay registers must be properly initialized at startup to avoid transients.

### Digital control advantages for power converters

- Nonlinear control strategies (e.g., variable gain depending on operating point)
- Adaptive tuning (auto-tuning of compensator parameters)
- Multi-mode operation (CCM/DCM/burst mode transitions in software)
- Power management and sequencing
- Fault detection and protection in software
- Communication (PMBus, I2C) for system-level management
- Phase shedding in multiphase converters

### Practical limitations

- Computational delay limits bandwidth (f_c < f_sw/6 to f_sw/10)
- DPWM resolution can be a bottleneck for low-voltage high-Vin converters
- Power consumption of the digital controller (significant at very low power levels)
- EMI from digital clock and data lines
- Cost of high-resolution A/D and DPWM hardware

## Comprehensive Digital Control (from Corradini, Maksimovic, Mattavelli, Zane 2015)

Reference: Corradini, Maksimovic, Mattavelli, Zane, "Digital Control of High-Frequency Switched-Mode Power Converters" (2015, Wiley-IEEE Press), 357 pages.

This is the definitive reference for digital control of high-frequency switched-mode power converters. It goes well beyond Erickson Ch19, covering exact discrete-time modeling, direct-digital compensator design, quantization analysis, fixed-point implementation, and autotuning -- from theory to HDL code.

### Loop Delays: The Critical Difference from Analog Control (Ch2)

The most important difference between analog and digital control is the presence of loop delays. There are two distinct delay components:

#### Control delay (t_cntrl)
Time from sampling event to when the DPWM latches the computed control command.

- **Hardware-based controllers** (FPGA/ASIC): t_cntrl can be reduced to a small fraction of T_s. The A/D conversion and combinational logic propagation complete within the same switching interval, so d[k] = u[k]/N_r (same-cycle update).
- **Software-based controllers** (MCU/DSP): t_cntrl can approach a full switching period due to CPU execution time. In this case d[k] = u[k-1]/N_r (one-cycle-delayed update).

#### Modulation delay (t_DPWM)
An inherent small-signal delay introduced by the uniformly sampled PWM (USPWM), absent in analog naturally sampled PWM. This delay arises because the modulating signal is latched at the start of each cycle and held constant during comparison with the carrier.

The USPWM response to a small-signal Kronecker input impulse is a Dirac delta delayed by t_DPWM:

```
c_hat(t) ~ (T_s / N_r) * u_hat[0] * delta(t - D*T_s)    [trailing-edge]
```

Modulation delays for different PWM types:

| Modulation Type | G_PWM(jw)                        | t_DPWM        |
|-----------------|----------------------------------|----------------|
| Trailing-edge   | (1/N_r) * e^(-jw*D*T_s)         | D * T_s        |
| Leading-edge    | (1/N_r) * e^(-jw*(1-D)*T_s)     | (1-D) * T_s    |
| Symmetrical     | cos(wDT_s/2)/N_r * e^(-jw*T_s/2)| T_s / 2        |

Key insight: Symmetrical (triangle) modulation has t_DPWM = T_s/2 independent of duty cycle, making it the preferred choice for digital control because the delay does not change with operating point.

#### Total loop delay

```
t_d = t_cntrl + t_DPWM
```

For hardware controllers with trailing-edge modulation: t_d ~ D*T_s (small t_cntrl).
For software controllers with trailing-edge modulation: t_d ~ T_s + D*T_s.

**Critical impact on stability**: The loop delay contributes additional phase lag. When using averaged models, the effective uncompensated loop gain becomes:

```
T_u_eff(s) = T_u(s) * e^(-s*t_d)
```

This extra phase lag dramatically reduces phase margin compared to analog control. Example: A Buck converter compensator designed for 45 deg phase margin in analog control may have only ~20 deg phase margin with digital control due to loop delay.

**Operating point dependence**: For trailing-edge modulation, t_DPWM = D*T_s, so the delay changes with duty cycle. A design validated at D = 0.36 (1.8V from 5V) may become marginally stable at D = 0.66 (3.3V from 5V).

#### Small-aliasing approximation

The averaged model with delay correction T_u_eff(s) is only valid under the "small-aliasing approximation" -- when the sampled signal closely follows the averaged waveform:

```
v_s[k] ~ <v_s>(t_k)
```

This holds when:
1. A well-filtered state variable (like output voltage) is sampled
2. The controller intentionally samples at the point where ripple is zero (e.g., symmetrical PWM for average current control)

When aliasing is significant, averaged models fail and exact discrete-time modeling (Ch3) is required.

### A/D Conversion Practical Considerations (Ch2)

#### Sampling rate selection
The sampling rate should equal the switching frequency (T = T_s). This eliminates in-band aliased images of the switching ripple. Switching harmonics alias only at dc, producing a constant offset that can be managed.

If sampling at multiples of f_s (multisampling), aliased images of switching ripple appear below the Nyquist rate and must be filtered digitally.

#### DC aliasing effect
With synchronous sampling at f_s, switching harmonics alias at dc. The sampled steady-state value v_s[k] differs from the true dc value of v_s(t). For well-filtered signals (small ripple), this error is negligible. For inductor current with large ripple, use symmetrical modulation and sample at peak/valley of the carrier to capture the true average.

#### Average current sampling
For digital average current-mode control:
- Use symmetrical (triangle) PWM modulation
- Sample at the peak or valley of the PWM carrier
- The triangular inductor current waveform crosses its average value at these instants
- This eliminates dc aliasing error regardless of duty cycle (valid in CCM only)

#### Windowed-flash A/D converters
For digital power control, specialized windowed A/D converters use a small set of analog comparators centered around an analog reference V_ref, spaced q_ADC apart. The output is directly the error signal e[k] in thermometer code, converted to binary. Advantages:
- Very fast conversion (comparator + encoder propagation delay only)
- Small number of comparators needed (typically 5-7 bits of window)
- The conversion range is narrow, centered around the regulation point
- Reduces A/D complexity vs. wide-range converters

### Digital Compensator Architecture (Ch2)

The PID compensator is the standard choice for digital power control. Two common discretization methods:

#### Backward Euler discretization (s -> (1-z^-1)/T_s)

```
u_p[k] = K_p * e[k]
u_i[k] = u_i[k-1] + K_i * T_s * e[k]
u_d[k] = (K_d / T_s) * (e[k] - e[k-1])
u[k] = u_p[k] + u_i[k] + u_d[k]
```

#### Tustin (bilinear) discretization (s -> (2/T_s)*(1-z^-1)/(1+z^-1))

```
u_p[k] = K_p * e[k]
u_i[k] = (K_i * T_s / 2) * (e[k] + e[k-1]) + u_i[k-1]
u_d[k] = (2*K_d / T_s) * (e[k] - e[k-1]) - u_d[k-1]
u[k] = u_p[k] + u_i[k] + u_d[k]
```

The Tustin approach is more accurate (trapezoidal integration) and its frequency response closely matches the analog prototype up to near f_s/2.

### Discrete-Time Small-Signal Modeling (Ch3)

#### Why averaged models are insufficient

Averaged continuous-time models:
- Do not capture modulation delay (t_DPWM)
- Do not capture aliasing effects
- Can only approximately account for loop delays via e^(-s*t_d)
- Fail when sampling waveforms with significant ripple

Discrete-time modeling provides exact z-domain transfer functions with no approximations.

#### Three-step discrete-time modeling process

**Step 1**: Express sampled state at k+1 in terms of state and control at k:

```
x[k+1] = f(x[k], V, u[k])
```

This uses the matrix exponentials of the state-space descriptions for each switching subinterval.

**Step 2**: Find the operating point Q = (X, V, U) by solving:

```
X = f(X, V, U)
```

**Step 3**: Linearize around Q to get the small-signal discrete-time model:

```
x_hat[k+1] = Phi * x_hat[k] + gamma * u_hat[k]
y_hat[k] = delta * x_hat[k]
```

where Phi is the state transition matrix and gamma is the control-to-state vector.

#### Key matrices

For a converter alternating between topologies S0 (matrices A0, B0) and S1 (matrices A1, B1), with trailing-edge modulation and small control delay:

```
Phi = e^(A0 * D'*T_s) * e^(A1 * D*T_s)          [state transition matrix]
```

The control vector gamma depends on the modulation type and captures the modulation delay effect. For trailing-edge modulation:

```
gamma = e^(A0 * D'*T_s) * (A1 - A0) * X + (B1 - B0) * V) * T_s
```

(simplified; exact expressions involve matrix exponential products)

#### z-domain transfer functions

The control-to-output transfer function in z-domain:

```
G_vu(z) = delta * (zI - Phi)^(-1) * gamma
```

This is exact -- no approximations. It correctly captures:
- Modulation delay effects
- Sampling/aliasing effects
- High-frequency dynamics beyond the averaged model

#### Time-invariant topologies (Buck converter shortcut)

For converters like the Buck that maintain the same topology in both subintervals (inductor always connected to the output), a simplified discretization rule exists:

```
G_vu(z) = (1/N_r) * Z{ G_vd(s) * G_PWM(s) }
```

where Z{} is the z-transform with zero-order hold, and G_PWM(s) is the USPWM transfer function from Table above. This links directly to the averaged model G_vd(s) and provides the exact discrete-time result.

For time-varying topologies (Boost, Buck-Boost), this shortcut does not apply and the full discrete-time modeling procedure is required.

#### MATLAB implementation

```matlab
% Buck converter discrete-time model
% State matrices for each subinterval
A1 = [-rL/L, -1/L; 1/C, -1/(Ro*C)];  % switch ON
B1 = [1/L; 0];
A0 = [-rL/L, -1/L; 1/C, -1/(Ro*C)];  % switch OFF (Buck: same topology)
B0 = [0; 0];

% Matrix exponentials
Phi1 = expm(A1 * D * Ts);
Phi0 = expm(A0 * (1-D) * Ts);

% State transition matrix
Phi = Phi0 * Phi1;

% Control vector (trailing-edge)
gamma = Phi0 * (A1*X + B1*Vg - A0*X - B0*Vg) * Ts;  % simplified

% z-domain transfer function
[num, den] = ss2tf(Phi, gamma, delta, 0);
Gvu = tf(num, den, Ts);
```

For the Boost converter, the matrices A1 and A0 differ (time-varying topology), and the full discrete-time modeling procedure must be used. The book provides complete MATLAB scripts for Buck, Boost, and Buck-Boost converters.

### Direct Digital Compensator Design via Bilinear Transform (Ch4)

#### The p-domain design approach

Instead of designing in z-domain directly (unfamiliar), or redesigning an analog compensator (approximate), the bilinear transform maps the exact z-domain model to an equivalent continuous-time p-domain:

```
z(p) = (1 + p*T_s/2) / (1 - p*T_s/2)
```

Inverse: p = (2/T_s) * (z-1)/(z+1)

The frequency mapping between z and p domains:

```
w' = (2/T_s) * tan(w*T_s/2)
```

At low frequencies w' ~ w. Near the Nyquist rate, significant warping occurs (w_Nyquist maps to infinity in p-domain).

**Design procedure:**
1. Map the exact uncompensated loop gain T_u(z) to p-domain: T_u'(p)
2. Evaluate |T_u'| and angle(T_u') at the warped crossover frequency w_c' = (2/T_s)*tan(w_c*T_s/2)
3. Design PID compensator in p-domain using standard analog techniques
4. Map back to z-domain using (4.15)

This approach reuses all familiar analog design methods while working with exact discrete-time models.

#### PID compensator in the p-domain

The z-domain PID:

```
G_PID(z) = K_p + K_i/(1-z^-1) + K_d*(1-z^-1)
```

Maps to p-domain as:

```
G'_PID(p) = K_p + K_i/T_s * (1 + p/w_p) / p + K_d*T_s * p / (1 + p/w_p)
```

where w_p = 2/T_s = w_s/pi (~0.318 * f_s).

In multiplicative form:

```
G'_PID(p) = G'_PI_inf * (1 + w_PI/p) * G'_PD0 * (1 + p/w_PD) / (1 + p/w_p)
```

The pole at w_p originates from the finite sampling rate and limits the derivative action.

#### Converting p-domain design to z-domain PID gains

```
K_p = G'_PI_inf * G'_PD0 * (1 + w_PI/w_PD - 2*w_PI/w_p)
K_i = 2 * G'_PI_inf * G'_PD0 * w_PI / w_p
K_d = G'_PI_inf * G'_PD0 / 2 * (1 - w_PI/w_p) * (w_p/w_PD - 1)
```

Valid PID coefficients (K_p >= 0, K_i >= 0, K_d >= 0) require:
```
0 <= w_PI <= w_p
0 <= w_PD <= w_p
```

#### Phase margin bounds for digital PD compensation

The maximum achievable phase margin with a digital PD compensator:

```
phi_m_u < phi_m < phi_m_u + pi/2 - arctan(w_c'/w_p)
```

where phi_m_u = pi + angle(T_u'(jw_c')) is the uncompensated phase margin. If the target phase margin exceeds the upper bound, a more complex compensator structure is needed.

#### Worked design example: Voltage-mode Buck (5V to 1.8V, 1 MHz, L=1uH, C=200uF)

Target: f_c = f_s/10 = 100 kHz, phi_m = 45 deg

1. Evaluate T_u at f_c: |T_u| = -24 dB, angle = -199 deg
2. Warped crossover: w_c' ~ 2*pi*103.4 kHz
3. w_p = 2/T_s ~ 2*pi*318 kHz
4. Uncompensated phase margin: phi_m_u = 180-199 = -19 deg (unstable without compensation!)
5. Phase margin bounds: -19 < phi_m < 53 deg (45 deg is achievable)
6. PD zero: w_PD = 2*pi*14.9 kHz
7. PD gain: G'_PD0 = 2.37
8. PI zero: w_PI = w_c/20 = 2*pi*5 kHz, G'_PI_inf = 1
9. Final z-domain gains: K_p = 3.09, K_i = 0.0745, K_d = 23.8

#### Other design examples in the book

- **Digital current-mode control of a Boost converter** (120V to 380V, 100 kHz): Average current control with symmetrical modulation, PI compensator design
- **Multiloop control of a Buck converter**: Inner current loop + outer voltage loop, both designed digitally. Current loop bandwidth ~ f_s/5, voltage loop ~ f_s/50
- **Boost power factor corrector**: Two-loop design (fast inner current loop + slow outer voltage loop), current reference shaped as rectified sine. The voltage loop sampling rate is 2*f_line (twice per line cycle). Complete MATLAB design flow provided.

### Other Converter Transfer Functions (Ch4)

For closed-loop disturbance analysis, the standard s-domain definitions are retained even for digitally controlled converters:

```
G_vg,cl(s) = G_vg(s) / (1 + T_eff(s))     [closed-loop audiosusceptibility]
Z_o,cl(s) = Z_o(s) / (1 + T_eff(s))       [closed-loop output impedance]
```

where T_eff(s) = G_c_eff(s) * T_u(s) * e^(-s*t_d), and G_c_eff(s) is obtained by inverse bilinear transform of G_c(z):

```matlab
Gcs = d2c(Gcz, 'tustin');
```

This is valid under the small-aliasing approximation.

### Actuator Saturation and Anti-Windup (Ch4)

When duty cycle saturates (at 0% or 100%), the integrator continues accumulating error, causing windup. Two mitigation strategies:

#### 1. Saturated integrator

Clamp the integrator state variable to [U_i_min, U_i_max]:

```
u_acc[k] = K_i * e[k] + u_i[k-1]
u_i[k] = clamp(u_acc[k], U_i_min, U_i_max)
```

Common choice: [U_i_min, U_i_max] = [0, 1] (duty cycle range).

#### 2. Conditional integration (preferred)

Freeze integration when the overall control command saturates:

```
sat[k] = 1 if u_PID[k] < 0 or u_PID[k] > 1, else 0
u_i[k] = u_i[k-1] + K_i * e[k] * (1 - sat[k-1])
```

Note: sat[k] is available with one-cycle delay (must compute u_PID first). Conditional integration immediately stops error accumulation upon saturation and provides faster recovery than simple clamping.

In multiloop control (e.g., voltage loop + current loop), anti-windup is also needed on the outer loop integrator when the inner loop reference saturates.

### Amplitude Quantization and No-Limit-Cycling Conditions (Ch5)

#### The limit cycling problem

In steady state, the digital controller must position the output voltage into the A/D zero-error bin (the bin corresponding to e = 0). If quantization prevents this, the controller oscillates between bins -- this is limit cycling.

Two quantization mechanisms interact:
1. **DPWM quantization**: Creates a finite set of achievable output voltages
2. **A/D quantization**: Creates bins the controller tries to match

#### Necessary no-limit-cycling conditions

**Condition 1 -- DPWM resolution (hardware)**:

```
q_vo_DPWM < q_vo_ADC
```

The DPWM voltage step must be finer than the A/D voltage step, both expressed at the output:

```
q_vo_DPWM = (dM/dD)|_D* * q_D * V_g
q_vo_ADC = V_FS / (2^n_ADC * H0)
```

For a Buck converter: q_vo_DPWM = V_g * q_D = V_g / 2^n_DPWM

This condition depends on operating point for Boost and Buck-Boost converters, where dM/dD varies with D.

**Condition 2 -- Integral gain (effective quantization)**:

The integrator combined with A/D quantization creates an effective duty cycle quantization:

```
q_u_Ki = K_i * q_vs_ADC
```

This must also be finer than the A/D resolution at the output:

```
G_vd(s=0) * K_i * H0 / N_r < 1
```

For a Buck: H0 * V_g * K_i < 1

If violated, the integrator cannot position the output within the zero-error bin, even with infinite DPWM resolution.

**Both conditions require integral action (K_i > 0) in the compensator.**

#### Practical example

Buck converter: V_g = 5V, V_o = 1.8V, 8-bit A/D with V_FS = 2V:
- q_vo_ADC = 2V / 256 = 7.8 mV
- With 8-bit DPWM: q_vo_DPWM = 5V / 256 = 19.5 mV > 7.8 mV --> LIMIT CYCLING
- With 10-bit DPWM: q_vo_DPWM = 5V / 1024 = 4.9 mV < 7.8 mV --> OK
- K_i check: H0*V_g*K_i = 1*5*0.0745 = 0.37 < 1 --> OK

#### Dynamic quantization effects

Even when both static conditions are met, limit cycling can still occur due to dynamic interactions. Additional guidelines:
- Design with sufficient gain margin (> 6 dB recommended)
- Statistical analysis relates control bandwidth to probability of limit-cycle oscillations
- Energy-based approaches relate limit cycles to control bandwidth, PID zero positioning, and system damping

### DPWM Implementation Techniques (Ch5)

#### Counter-based DPWM
- Time resolution = clock period T_clk
- Required clock: f_clk = 2^n_DPWM * f_s
- Example: 10-bit DPWM at 1 MHz switching --> 1 GHz clock (impractical!)

#### Delay-line DPWM
- Uses a tapped string of delay cells instead of a high-frequency clock
- Time resolution = cell propagation delay t_c
- Clock frequency = f_s (much lower)
- Disadvantage: delay line and MUX size grow as 2^n_DPWM

#### Hybrid counter/delay-line DPWM (most practical)
- MSB bits handled by counter (moderate clock rate)
- LSB bits handled by delay line (fine resolution)
- Control word split: u = u_MS (counter bits) + u_LS (delay line bits)
- Trade-off: longer delay line reduces required clock rate
- Example: 10-bit DPWM with 4-bit delay line needs only 64 MHz clock instead of 1 GHz

#### Sigma-delta DPWM resolution enhancement
Place a sigma-delta modulator before a lower-resolution hardware DPWM:
- Second-order sigma-delta: NTF(z) = (1 - z^-1)^2
- Shifts quantization noise to high frequencies where the power stage filter attenuates it
- Example: 8-bit hardware DPWM + sigma-delta = effective 10-bit resolution
- The duty cycle command dithers between adjacent levels, but LC filter smooths the output
- Easily provides several bits of effective resolution improvement

#### A/D converter architectures for digital power
- **Windowed-flash A/D**: Small number of comparators centered around V_ref, directly outputs error signal. Very fast, low complexity, narrow conversion range.
- **SAR A/D**: Standard successive-approximation, moderate speed and resolution
- Key specs: conversion time << T_s, resolution adequate for regulation, linearity less critical than in signal processing

### Compensator Implementation: From Design to Hardware (Ch6)

#### Three PID realizations

**1. Parallel form** (most intuitive for tuning):
```
G_PID(z; K) = K_p + K_i / (1 - z^-1) + K_d * (1 - z^-1)

u_p[k] = K_p * e[k]
u_i[k] = u_i[k-1] + K_i * e[k]
u_d[k] = K_d * (e[k] - e[k-1])
u[k] = u_p[k] + u_i[k] + u_d[k]
```

**2. Direct form** (minimum storage, but coefficients affect everything):
```
G_PID(z; b) = (b0 + b1*z^-1 + b2*z^-2) / (1 - z^-1)

w[k] = w[k-1] + e[k]
u[k] = b0*w[k] + b1*w[k-1] + b2*w[k-2]
```

**3. Cascade form** (direct access to gain and zeros):
```
G_PID(z; c) = K / (1 - z^-1) * (1 + c_z1*z^-1) * (1 + c_z2*z^-1)

w_i[k] = e[k] + w_i[k-1]
w_1[k] = w_i[k] + c_z1 * w_i[k-1]
w_2[k] = w_1[k] + c_z2 * w_1[k-1]
u[k] = K * w_2[k]
```

Conversion formulas between p-domain design parameters and each z-domain form are provided in Table 6.1 of the book.

#### Coefficient scaling

Before implementation, compensator coefficients must be scaled to account for the A/D and DPWM gains in the actual hardware:

```
Scaling factor: lambda = q_vs_ADC * N_r

K_p_scaled = lambda * K_p
K_i_scaled = lambda * K_i
K_d_scaled = lambda * K_d
```

After scaling, the error signal e[k] and control command u[k] become integers with unity quantization bin. The scaling does NOT alter the system loop gain T(z).

#### Coefficient quantization

After scaling, coefficients must be rounded to fit in finite-length binary words. Two constraints guide the required resolution:

**Constraint I (crossover frequency accuracy):**
```
| |T_tilde(z)| - 1 | < epsilon_c    at w = w_c
| angle(T_tilde) - angle(T) | < alpha_c   at w = w_c
```

Typical: epsilon_c = 0.1 (10% magnitude error), alpha_c = 5 deg

**Constraint II (low-frequency gain accuracy):**
```
| |T_tilde| - |T| | / |T| < epsilon_0    at w -> 0
```

Typical: epsilon_0 = 0.1 (10% relative error in dc gain)

The sensitivity of the loop gain to coefficient quantization differs by realization:
- **Parallel form**: Each gain independently affects a different frequency range. K_i most sensitive (smallest absolute value after scaling).
- **Direct form**: Each b coefficient affects the entire response. Requires highest coefficient resolution.
- **Cascade form**: K and zero locations can be quantized independently. Best coefficient sensitivity properties for most designs.

#### Coefficient quantization example results (Buck converter design)

| Structure | n_p bits | n_i bits | n_d bits | Total coeff. bits |
|-----------|----------|----------|----------|-------------------|
| Parallel  | 7        | 13       | 5        | 25                |
| Direct    | 11       | 13       | 11       | 35                |
| Cascade   | 7        | 7        | 7        | 21                |

The cascade form requires the least total coefficient bits. The direct form requires the most.

### Fixed-Point Controller Implementation (Ch6)

#### Key concepts

**Effective Dynamic Range (EDR)**: The range of signal values a node in the controller actually experiences during operation. Determined by the input signal range and the transfer function from input to that node.

**Hardware Dynamic Range (HDR)**: The range representable by the assigned word length at that node. Must satisfy HDR >= EDR to avoid overflow.

#### Upper bound estimation via L1-norm

For a causal, stable LTI system with impulse response h[k], the maximum output magnitude given a bounded input |x[k]| <= X_max is:

```
|y[k]|_max <= X_max * ||h||_1 = X_max * sum(|h[k]|, k=0 to inf)
```

The L1-norm ||h||_1 provides a tight upper bound for the peak signal value at any node.

In MATLAB:
```matlab
h1norm = sum(abs(impulse(sys)));  % L1-norm of impulse response
```

#### Fixed-point implementation procedure

1. Determine the error signal range: |e_tilde[k]| <= e_max (determined by A/D window)
2. For each internal node, compute the L1-norm from input to that node
3. Calculate the upper bound: signal_max = e_max * L1_norm
4. Assign integer word length: n_int >= ceil(log2(signal_max)) + 1 (sign bit)
5. Assign fractional bits based on required precision (coefficient quantization analysis)
6. Total word length = integer bits + fractional bits

#### Worked example: Voltage-mode Buck in fixed-point

For the parallel realization with 4-bit windowed A/D (e_max = 8):

| Signal    | L1-norm | Upper bound | Integer bits | Frac bits | Total bits |
|-----------|---------|-------------|--------------|-----------|------------|
| u_p[k]   | K_p     | ~50         | 7            | 0         | 7          |
| u_i[k]   | large   | ~500        | 10           | 0         | 10         |
| u_d[k]   | K_d     | ~380        | 10           | 0         | 10         |
| u_PID[k] | sum     | ~930        | 11           | 0         | 11         |

(Actual values depend on specific coefficients; book provides detailed calculations for all three realizations.)

The direct realization typically requires wider internal word lengths due to the integrator accumulating large values multiplied by large b coefficients. The cascade realization has the most favorable internal signal ranges.

#### Quantized vs. ideal system response

After fixed-point implementation, the time-domain response should be verified against the ideal (infinite precision) response. Typical verification: step-load response comparison. Well-designed fixed-point controllers show negligible deviation from ideal response.

### HDL Implementation of the Controller (Ch6)

The book provides complete VHDL and Verilog examples for implementing a PID compensator.

#### VHDL structure (parallel PID)

```vhdl
-- Key operations per clock cycle:
-- 1. Compute proportional: up = Kp * e
-- 2. Update integral: ui_acc = ui + Ki * e; ui = saturate(ui_acc)
-- 3. Compute derivative: ud = Kd * (e - e_prev); e_prev = e
-- 4. Sum and truncate: u_pid = up + ui + ud; u = truncate_and_saturate(u_pid)
```

Key implementation details:
- All signals in binary two's complement (B2C) fixed-point
- Multiplication by constant coefficients synthesized as shift-and-add (no multiplier hardware needed for hardwired coefficients)
- Saturated arithmetic on integrator to prevent overflow (implements anti-windup)
- Output truncation removes LSBs to match DPWM resolution
- Output saturation clamps to [0, N_r-1]

#### Verilog equivalent

The same PID structure maps directly to Verilog with `reg` declarations for state registers and combinational `assign` statements or `always` blocks for arithmetic.

#### Hardware vs. software implementation

| Aspect                  | Hardware (FPGA/ASIC)      | Software (MCU/DSP)        |
|-------------------------|---------------------------|---------------------------|
| Computation delay       | ~10s of ns                | ~100s of ns to us         |
| Word length flexibility | Fully customizable        | Fixed (16/32-bit)         |
| Coefficient type        | Hardwired constants       | Stored in memory          |
| Power consumption       | Very low                  | Higher                    |
| Programmability         | Limited (needs reprog.)   | Easy (software update)    |
| Best for                | f_s > 200 kHz             | f_s < 200 kHz             |

### Digital Autotuning (Ch7)

#### Overview

Digital autotuning automatically tunes compensator parameters based on online identification of the plant dynamics. Three levels:
- **One-step tuning**: Calibrate once (e.g., at power-up) from a safe initial condition
- **Performance tracking**: Periodic re-tuning to track parametric variations
- **Adaptive tuning**: Continuous tuning throughout operation

All techniques require a programmable compensator structure (full digital multipliers instead of hardwired constants).

#### Programmable PID structures for autotuning

**Parallel form**: Best for independent adjustment of low/mid/high frequency response. Changing K_p mostly affects mid-frequencies, K_d affects high frequencies, K_i affects low frequencies. However, all parameters are interacting (changing one affects both magnitude and phase).

**Cascade form for autotuning** (preferred):
```
G_PID(z; kappa) = K_i / (1-z^-1) * (1-kappa1+kappa1*z^-1) * (1-kappa2+kappa2*z^-1)
```

Advantages:
- Low-frequency asymptote K_i/(1-z^-1) is independent of kappa1, kappa2
- Adjusting zeros does not affect low-frequency gain --> stability preserved during tuning
- Zero locations: z_1,2 = -kappa_1,2 / (1 - kappa_1,2)

**Hybrid PI-PD form**:
```
G_PID(z; h) = K_i/(1-z^-1) + K_PD*(1-kappa+kappa*z^-1)
```
Reduces to parallel PI when kappa=0.

#### Injection-based autotuning (Perturbation Injection)

**Principle**: Inject a sinusoidal perturbation u_pert[k] at frequency w_p into the control loop. Monitor signals u_x[k] (after injection) and u_y[k] (before injection). The relationship between their phasors gives the loop gain:

```
u_y_vec / u_x_vec = -T(e^(jw_p*T_s))
```

**Tuning target**: Adjust PID gains until u_x_vec = u_y_vec * e^(-j*phi_m), which means T(e^(jw_c*T_s)) = -e^(j*phi_m) -- unity gain at w_c with phase margin phi_m.

**Implementation (PD autotuner)**:
1. Inject sinusoid at target crossover frequency w_p = w_c
2. Extract AC components: u_x_hat = u_x - U, u_y_hat = u_y - U (use u_i as estimate of U)
3. Compute tuning error: epsilon[k] = u_x_hat[k] - u_y_hat_delayed_by_phi_m[k]
4. Decompose error into parallel and perpendicular components via time-domain multiplication:
   - p_parallel[k] = epsilon[k] * u_x_hat[k] --> related to crossover frequency error
   - p_perp[k] = epsilon[k] * u_x_hat_90deg[k] --> related to phase margin error
5. Integral tuning loops:
   - K_d[k] = K_d[k-1] + alpha_parallel * p_parallel[k]  (tunes crossover frequency)
   - K_p[k] = K_p[k-1] + alpha_perp * p_perp[k]  (tunes phase margin)

**Fractional delay implementation**: Phase delay of phi degrees at frequency w_p implemented as:

```
F_a(z) = 1 - a + a*z^-1
a = tan|phi| / (w_p * T_s)     [approximate, valid for w_p < f_s/10]
```

**Basic version is two-parameter tuning** (PD or PI). K_i must be preset to a safe value for PD tuning. Extensions: sequential PD then Ki tuning, or conditional PI after PD.

#### Relay feedback autotuning

**Principle**: Insert a relay nonlinearity in the loop to trigger a limit cycle. The oscillation frequency and amplitude carry plant information.

**Relay function**: e_r = +A_r if e > 0, else -A_r

**Describing function**: Psi(a_osc) = 4*A_r / (pi * a_osc)

**Three-phase tuning procedure**:

**Phase 1 -- Identify resonant frequency**:
- Start with kappa1 = kappa2 = 0 (pure integrator) and small K_i
- Insert relay in loop --> system oscillates at frequency where angle(T_u) = -90 deg
- For Buck converter, this is approximately the LC resonant frequency f_0
- Measure f_osc by counting zero crossings
- Set kappa1 to place first PID zero at f_osc:
  ```
  kappa1 = -(1/(2*pi)) * N_s/N    [N_s = counter ticks over N periods]
  ```

**Phase 2 -- Tune phase margin**:
- Insert filter F(z) with phase lag = phi_m at target f_c
- Adjust kappa2 via binary search until f_osc = f_c
- When achieved, the linear loop gain has phase margin = phi_m at f_c

**Phase 3 -- Tune crossover frequency gain**:
- Measure oscillation amplitude a_osc
- Scale K_i so that |T(f_c)| = 1:
  ```
  K_i_new = K_i * 4*A_r*|F(f_c)| / (pi * a_osc)
  ```
- Remove relay and filter --> tuned loop

#### Implementation issues for autotuning

- **Output voltage perturbation**: Must be limited to acceptable values. In injection-based: control via u_m amplitude. In relay-based: control via relay amplitude A_r.
- **Perturbation waveform**: Practical implementations use square waves instead of sinusoids (easier to generate). This introduces harmonics that cause intermodulation errors in the time-domain products.
- **Quantization effects**: Injection-based is more robust (high-resolution internal signals). Relay-based degrades at very small perturbation amplitudes due to A/D resolution on e[k].
- **Monotonicity requirement**: Relay feedback requires monotonically decreasing phase response. Non-monotonic plant phase creates unreachable frequency intervals.
- **Convergence speed**: Tuning loop gains (alpha_parallel, alpha_perp) must be small enough for stability of the tuning loop but large enough for practical convergence times. Typical tuning completes in 0.5-2 ms for MHz-range converters.

### Summary of Key Design Rules

1. **Bandwidth limit**: f_c < f_s/6 to f_s/10 due to loop delay (more restrictive than Erickson's rule)
2. **Use symmetrical modulation** when possible -- constant delay independent of D
3. **No-limit-cycling**: q_vo_DPWM < q_vo_ADC AND G_vd0*K_i*H0/N_r < 1
4. **DPWM resolution**: For Buck, n_DPWM >= n_ADC + log2(V_g * H0 / V_FS)
5. **Sigma-delta dithering** easily adds 2-3 effective bits to hardware DPWM resolution
6. **Average current sampling**: Use symmetrical PWM, sample at carrier peak/valley
7. **Anti-windup**: Always implement conditional integration or saturated integrator
8. **Cascade PID realization** preferred for coefficient quantization sensitivity and autotuning
9. **Fixed-point design**: Use L1-norms to size internal word lengths; verify with quantized simulation
10. **Gain margin > 6 dB** recommended to suppress dynamic limit cycling
