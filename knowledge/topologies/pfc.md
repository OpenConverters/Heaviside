# Power Factor Correction (PFC) Design (from Erickson Ch20-21)

Reference: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Chapters 20-21, pp.849-929.

## Why PFC is Needed

Conventional diode rectifiers (peak-detection type) draw current only near the peak of the AC line voltage, producing large harmonic currents with low power factor (typically PF = 0.5 to 0.7).

**Power factor** is defined as:

```
PF = P_avg / (V_rms * I_rms) = (average power) / (apparent power)
```

For a sinusoidal voltage source with a nonlinear load drawing distorted current:

```
PF = (I_1 / I_rms) * cos(phi_1) = distortion_factor * displacement_factor
```

where:
- I_1 = fundamental component of current
- I_rms = total RMS current (including all harmonics)
- phi_1 = phase angle between voltage and fundamental current
- Distortion factor = I_1/I_rms = 1/sqrt(1 + THD^2)
- Displacement factor = cos(phi_1)

**THD (Total Harmonic Distortion):**

```
THD = sqrt(sum_{n=2}^{inf} I_n^2) / I_1 = sqrt((I_rms/I_1)^2 - 1)
```

### Effects of harmonic currents

- Excessive neutral current in three-phase systems (3rd harmonic and its multiples do not cancel in neutral)
- Transformer and motor heating (increased copper and core losses)
- Voltage waveform distortion
- Power factor correction capacitor overloading
- Interference with protection and metering equipment

### Three-phase harmonic current flow

In three-phase four-wire systems, the neutral current contains triplen harmonics (3rd, 9th, 15th, ...) that add rather than cancel. With typical diode rectifier loads, the neutral current can exceed the phase current.

In three-phase three-wire (delta or ungrounded wye) systems, triplen harmonics cannot flow, but non-triplen harmonics (5th, 7th, 11th, 13th...) circulate.

## Harmonic Standards (IEC 61000-3-2)

IEC 61000-3-2 limits harmonic current emissions for equipment with input current <= 16A per phase. Equipment is classified into four classes:

| Class | Equipment Type | Limit Stringency |
|-------|---------------|------------------|
| A | Balanced three-phase, most non-Class B/C/D | Absolute limits per harmonic |
| B | Portable tools | Class A limits * 1.5 |
| C | Lighting equipment | Limits as % of fundamental |
| D | Equipment with "special waveshape" (<=600W) | Limits in mA/W |

**Class D limits** (most relevant for power supplies <= 600W):

| Harmonic | Limit (mA/W) | Max (A) |
|----------|-------------|---------|
| 3 | 3.4 | 2.30 |
| 5 | 1.9 | 1.14 |
| 7 | 1.0 | 0.77 |
| 9 | 0.5 | 0.40 |
| 11 | 0.35 | 0.33 |
| 13-39 (odd) | 3.85/n | - |

**Class A limits** (absolute mA values, selected harmonics):

| Harmonic | Max current (A) |
|----------|----------------|
| 3 | 2.30 |
| 5 | 1.14 |
| 7 | 0.77 |
| 9 | 0.40 |
| 11 | 0.33 |

For high-power equipment (>16A, <75A per phase): IEC 61000-3-12 applies with less stringent limits.

## Boost PFC Operating Principle

The boost converter is the topology of choice for single-phase PFC because:
1. Input current is the inductor current (continuous, controllable)
2. Non-pulsating input current is easier to filter
3. Output voltage is higher than input (V_out > V_peak), compatible with downstream DC-DC
4. Simple topology with few components

### Loss-Free Resistor (LFR) model

An ideal PFC rectifier behaves as a **Loss-Free Resistor (LFR)** at its input:

```
v_g(t) = R_e * i_g(t)
```

where R_e is the emulated resistance. The rectifier draws power P = V_rms^2 / R_e from the AC line with unity power factor, and delivers this power to the DC output (minus losses).

The LFR is a two-port network:
- Input port: resistive characteristic (v_g = R_e * i_g)
- Output port: power source delivering P = V_rms^2 / R_e

### Boost PFC steady-state operation

For a boost converter operating as a PFC rectifier with sinusoidal input v_g(t) = V_M |sin(omega*t)|:

Required conversion ratio as a function of time:

```
M(t) = V / (V_M * |sin(omega*t)|)
```

Required duty cycle:

```
d(t) = 1 - V_M * |sin(omega*t)| / V = 1 - (V_M/V) * |sin(omega*t)|
```

The output voltage V must be greater than V_M (the peak AC voltage) for boost operation at all times.

**Typical design**: For 230V AC input (V_M = 325V), choose V_out = 385-400V.

## Average Current Mode Control for PFC

The standard control approach for CCM boost PFC uses two feedback loops:

### Inner current loop (fast)
- Senses the inductor (input) current
- Reference = rectified line voltage multiplied by control signal
- Forces input current to follow input voltage waveform
- Bandwidth: 5-20 kHz (well above line frequency, well below f_sw)
- Uses average current mode control (not peak current mode, to avoid distortion)

### Outer voltage loop (slow)
- Senses the output (bus) voltage
- Compares to reference (e.g., 400V)
- Generates the control signal that scales the current reference
- Bandwidth: 5-20 Hz (well below 2x line frequency = 100/120 Hz)
- Very slow to avoid injecting 2nd harmonic into current reference

### Current reference generation

```
i_ref(t) = v_control * v_g(t) / V_rms^2
```

where:
- v_control = output of voltage loop compensator
- v_g(t) = sensed (rectified) input voltage
- V_rms^2 = feedforward term for input voltage (RMS squared)

The feedforward V_rms^2 term provides fast response to line voltage changes and linearizes the power stage gain with respect to line voltage.

### Average current mode control implementation

The inner current loop uses an error amplifier that compares sensed inductor current to i_ref:

```
Current error: e_i(t) = i_ref(t) - i_L(t)
```

A Type 2 (PI + pole) compensator processes this error to generate the duty cycle command. The current loop bandwidth should be:
- High enough to track the line frequency current reference with low distortion
- Low enough to attenuate switching ripple (f_c_current < f_sw/5)
- Typical: f_c_current = f_sw/10 to f_sw/5

The transfer function from duty cycle to inductor current for the boost converter:

```
G_id(s) = V / (sL)   (simplified, valid below f_sw)
```

This is a single integrator, so a Type 2 compensator provides adequate phase margin.

## CCM vs CrCM vs DCM PFC

### CCM (Continuous Conduction Mode)

- Inductor current never reaches zero
- Lowest peak and RMS currents for a given power level
- Best efficiency at high power (>300W)
- Requires average current mode control or current-programmed control
- Significant reverse recovery losses in the boost diode (use SiC or ultrafast diodes)
- EMI: switching-frequency ripple, easier to filter

### CrCM (Critical/Boundary Conduction Mode, also called TCM or BCM)

- Inductor current reaches zero at the end of each switching cycle
- The transistor turns on at zero current (and often zero voltage due to inductor ringing)
- Variable switching frequency (higher at low load and near zero crossings)
- Natural ZVS can be achieved, eliminating diode reverse recovery
- Best for 75-300W range
- Higher peak currents than CCM (2x the average)
- EMI: variable frequency makes filtering harder

### DCM (Discontinuous Conduction Mode)

- Inductor current reaches zero well before the end of each switching cycle
- Highest peak currents, highest RMS currents
- Can achieve near-unity PF without current sensing (inherent PFC with constant duty cycle)
- DCM flyback is popular for low-power PFC (<75W)
- Suitable for Class D compliance (not necessarily unity PF)

### Comparison

| Parameter | CCM | CrCM | DCM |
|-----------|-----|------|-----|
| Power range | >300W | 75-300W | <75W |
| Peak inductor current | I_avg + Delta_i/2 | 2*I_avg | >>2*I_avg |
| MOSFET turn-on | Hard (or with SiC diode) | ZVS possible | Hard |
| Diode reverse recovery | Significant | None (ZCS) | None (ZCS) |
| Switching frequency | Fixed | Variable | Fixed or variable |
| Current sensing | Required (ACM) | Required | Optional |
| Control complexity | High | Medium | Low |

## Design Procedure

### CCM Boost PFC design steps:

1. **Choose output voltage**: V_out > V_M_max (typically 385-400V for universal input 90-265V AC)

2. **Choose switching frequency**: 50-150 kHz typical. Higher frequency reduces inductor size but increases switching losses.

3. **Design the boost inductor**:

```
L = V_M * (V_out - V_M) / (2 * Delta_i * f_sw * V_out)
```

where Delta_i is the peak-to-peak inductor current ripple (typically 20-40% of peak current at full load, evaluated at the peak of the AC waveform where D is maximum).

4. **Select output capacitor**: Must handle 2x line frequency ripple current and meet holdup time requirements.

```
C_out = P_out / (omega_line * V_out * Delta_V_out)
```

The 2nd harmonic voltage ripple:

```
Delta_V_out = P_out / (2 * pi * f_line * C_out * V_out)
```

5. **Select the boost diode**: Must handle reverse recovery (use SiC Schottky or ultrafast). Voltage rating > V_out. Current rating > I_avg_max.

6. **Select the MOSFET**: Voltage rating > V_out with margin. RMS current:

```
I_MOSFET_rms = I_in_rms * sqrt(1 - 8*V_M/(3*pi*V_out))  (approximate)
```

7. **Design the current loop compensator** (Type 2)

8. **Design the voltage loop compensator** (Type 1 or slow Type 2, f_c < 20 Hz)

9. **Add EMI filter** at the input

## Component Stresses

### Inductor current (at full load, peak of AC line)

```
I_L_peak = sqrt(2) * P_out / (eta * V_rms) + Delta_i/2
I_L_rms = P_out / (eta * V_rms)    (approximately equal to AC input RMS current)
```

### Boost diode

```
I_diode_avg = P_out / V_out = I_out
I_diode_rms = I_in_rms * sqrt(8*V_M / (3*pi*V_out))  (approximate for CCM)
V_diode_reverse = V_out
```

### MOSFET

```
I_MOSFET_rms ~ I_in_rms * sqrt(1 - 8*V_M/(3*pi*V_out))
V_DS_max = V_out
```

### Output capacitor

The output capacitor must handle the 2nd harmonic ripple current:

```
I_C_rms = P_out / (2 * V_out)   (the 2nd harmonic component)
```

Plus switching-frequency ripple current. Total RMS capacitor current is typically 40-60% of the DC load current.

### RMS values of boost PFC waveforms (from Erickson Section 21.5)

For a CCM boost rectifier with sinusoidal input, the RMS values depend on the ratio M_min = V_M/V_out:

```
Inductor current RMS: I_L_rms = I_g_rms = (V_M / R_e) * (1/sqrt(2))

MOSFET current RMS: I_Q_rms = V_M/(R_e*sqrt(2)) * sqrt(1 - 16*M_min/(3*pi) + 3*M_min^2/4)

Diode current RMS: I_D_rms = V_M/(R_e*sqrt(2)) * sqrt(16*M_min/(3*pi) - 3*M_min^2/4)
```

where M_min = V_M/V = minimum conversion ratio, R_e = emulated resistance.

## Control Loop Design (inner current + outer voltage)

### Voltage loop design

The power stage, from the perspective of the voltage loop, behaves as:

```
v_out(s) / v_control(s) = (V_rms^2 / (2*V_out)) * (R / (1 + s*R*C/2))
```

This is a single pole at f_p = 1/(pi*R*C) (using the load resistance and output capacitance).

The voltage loop compensator is typically a simple integrator (Type 1) or PI (Type 2 with very low bandwidth):

```
G_cv(s) = K_v / s    or    G_cv(s) = K_v * (1 + s/omega_z) / s
```

**Critical constraint**: The voltage loop bandwidth must be well below 2*f_line (100 Hz for 50 Hz line, 120 Hz for 60 Hz line). If the voltage loop responds to the 2nd harmonic ripple on V_out, it will inject 3rd harmonic distortion into the input current.

Typical voltage loop crossover: 5-20 Hz.

### Current loop design

The plant transfer function for the inner current loop (boost converter, duty cycle to inductor current):

```
G_id(s) = v_g(t) / (s*L)    (at a given instant within the line cycle)
```

Since v_g varies over the line cycle, the plant gain varies. At the peak of the line voltage (where the current is highest), the gain is maximum. Design for worst-case stability at this point.

A Type 2 compensator (PI + high-frequency pole) is standard:

```
G_ci(s) = K_i * (1 + s/omega_zi) / (s * (1 + s/omega_pi))
```

- omega_zi: placed at or below the LC resonant frequency
- omega_pi: placed at f_sw/2 to f_sw/5 for noise attenuation
- Crossover frequency: f_c = f_sw/10 to f_sw/5

### Feedforward

The multiplier feedforward term V_rms^2 compensates for changes in line voltage RMS. Without feedforward, the effective plant gain varies as V_rms^2, making the voltage loop response line-voltage dependent. The feedforward signal is obtained by squaring and low-pass filtering the rectified input voltage.

### Practical control IC implementation

Most PFC controller ICs (e.g., UC3854, UCC28019, NCP1654, L6562) integrate:
- Multiplier (current reference = v_control * v_ac / V_rms^2)
- Current error amplifier
- Voltage error amplifier
- PWM comparator
- Overcurrent protection
- Soft start
- Brownout protection

## TI PFC Practical Design (from SLUA369, SLUA479, TIDM_PFC)

NOTE: TIDM_PFC_Totem.pdf in the Papers directory does not contain PFC content (it is about a 10s-16s battery pack reference design, despite its filename). The content below is from SLUA369 and SLUA479 which are correctly filed.

### Two-Phase Interleaved PFC: 350 W Design (SLUA369C)

Reference: Mike O'Loughlin, "350-W, Two-Phase Interleaved PFC Pre-Regulator Design Review," TI Application Report SLUA369C, 2005/2013. Uses UCC28528 PFC/PWM controller + UCC28220 interleaved PWM controller.

#### Design Specifications

| Parameter | Min | Typ | Max |
|---|---|---|---|
| VIN | 85 V RMS | 110/230 V | 265 V RMS |
| VOUT | 374 V | 390 V | 425 V |
| VRIPPLE | - | - | 30 V |
| THD at 350 W | - | - | 10% |
| PF at 350 W | 0.95 | - | - |
| Efficiency | 90% | - | - |
| fS | - | 100 kHz | - |
| Holdup time | - | 20 ms | - |

#### Interleaving Benefits (Quantified)

**Input ripple current cancellation**: Two boost converters operating 180 degrees out of phase. Input current = IL1 + IL2. Cancellation factor K(D):
```
K(D) = (1 - 2*D) / (1 - D)    if D < 0.5
K(D) = (2*D - 1) / D           if D > 0.5
```
Best cancellation at D = 0.5 (K = 0). For universal input with 385 V output, worst case at low line peak: D = 0.69, K = 0.55 (45% reduction in input ripple vs single inductor).

**Magnetic volume reduction**: Each interleaved inductor stores half the energy of a single-stage inductor. Total interleaved area product is 50% of single-stage area product (WaAc ratio = 0.5).

**Output capacitor ripple reduction**: Interleaved output capacitor RMS current is approximately half that of single-stage boost at same power level.

#### Step-by-Step Component Design

**Boost inductor (L1 = L2)**:
```
L = (Vin_min * sqrt(2) * D_min_LL) / (delta_IL * fS)
L = (85 * sqrt(2) * 0.69) / (4.1 A * 100 kHz) = 200 uH
```
Selected: Cooper CTX16-17309, 200 uH.

**Output capacitor (COUT)**: Sized for both holdup and ripple requirements:
```
COUT_holdup = 2 * POUT / (fLINE * (VOUT^2 - VOUT_min^2))    >= 123 uF
COUT_ripple = POUT / (VOUT * 0.637 * VRIPPLE * 0.8 * pi * fLINE)  >= 104 uF
```
De-rate for 20% tolerance and 20% aging: multiply by 1/(0.8 * 0.8). Selected: 220 uF.

Output capacitor RMS ripple current (interleaved):
```
ICOUT_rms ~ 1 A    (vs ~2 A for single stage)
```

**Boost diode selection**: SiC Schottky diodes (CREE CSD10060) chosen for near-zero reverse recovery.
```
IDIODE_avg = POUT / (2 * VOUT) = 0.45 A per diode
IDIODE_peak = POUT * sqrt(2) / (2 * VIN_min) + delta_IL/2 = 5.3 A per diode
PDIODE = POUT * VF / (2 * VOUT) = 0.6 W per diode
```

**MOSFET selection (IRF840, 500 V, 8 A)**:
```
IPEAK = POUT * sqrt(2) / (2 * VIN_min) + delta_IL/2 = 5.3 A
```

FET loss breakdown:
```
PFET = P_switch_off + P_switch_on + P_Coss + P_gate + P_RDSon
```
Switching delay estimated from Miller charge:
```
t_ON = t_OFF = QGS_miller / I_gate
I_gate = VGS_max / (2 * R_gate)
```
Coss loss:
```
P_Coss = 0.5 * Coss_avg * VOUT^2 * fS
Coss_avg = 2 * Coss_spec * sqrt(VDS_spec / VOUT)  = 160 pF
```
Total estimated: PFET ~ 5 W per switch, 10 W total FET loss. With 1.2 W total diode loss = 11.2 W semiconductors (within 19 W budget for 90% efficiency).

#### Current Loop Compensation

Average current mode control with UCC28528 multiplier. The multiplier output provides the current reference:
```
i_ref(t) = v_control * v_ac(t) / VFF^2
```
Voltage feedforward (VFF) filter pole set to limit contribution to THD at 1.5%:
```
fp_VFF = fLINE * 1.5% / 66%
```

Current loop transfer function:
```
TC(s) = GID(s) * GCA(s)
GID(s) = VOUT * RSENSE * a / (s * L * VC1)    (control-to-inductor-current)
```
where a = current sense transformer ratio (1:50).

Design target: 45 degrees phase margin, crossover at fS/10.

Compensation (Type II):
```
RZB: set to force crossover at fS/10
CZB: zero at crossover frequency for 45 deg phase margin
CPB: pole at fS/2 for noise attenuation
```

#### Voltage Loop Compensation

```
GVD(s) = POUT / (eta * VC2 * s * COUT * VOUT)    (control-to-output voltage)
```
Crossover at 10 Hz (well below 2*fLINE to avoid harmonic distortion injection):
```
RZA: set to force crossover at 10 Hz
CZA: zero at crossover for 45 deg added phase
CPA: pole to attenuate 2*fLINE ripple to < 1.5% of VRIPPLE
```

#### Slope Compensation

Required for stability of the interleaved current mode control. The UCC28220 has internal slope compensation configured by RSLOPE. At least half the inductor current downslope must be added. Note: excessive slope compensation (needed in this topology) causes the peak current limit during startup to be 2x the nominal peak current.

#### Measured Performance

- **Efficiency at 85 V/350 W**: ~91%
- **Efficiency at 265 V/350 W**: ~96%
- **Power factor at 350 W**: >0.95 at both 85 V and 265 V
- **Current loop bandwidth**: Stable at both 120 Vdc and 238 Vdc, with double pole around 30 kHz (attributed to excessive slope compensation)
- **Voltage loop recovery from large transient**: <200 ms (UCC28528 has large-signal comparator for fast recovery)
- **Inductor ripple cancellation**: Confirmed at 50% of individual inductor ripple at input

### Two-Phase Interleaved PFC: 300 W Design (SLUA479B)

Reference: Michael O'Loughlin, "UCC28070 300-W Interleaved PFC Pre-Regulator Design Review," TI Application Report SLUA479B, 2008/2010. Uses UCC28070 dedicated interleaved PFC controller with average current mode control, 200 kHz per phase.

#### Design Specifications

| Parameter | Min | Typ | Max |
|---|---|---|---|
| VIN | 85 V RMS | 115/230 V | 265 V RMS |
| VOUT | - | 390 V | - |
| POUT | - | 300 W | - |
| PF | 0.95 | - | - |
| Efficiency | 90% | - | - |
| fS per phase | - | 200 kHz | - |

#### Inductor Design (200 kHz)

Design for 30% max input ripple at low-line peak:
```
D_PLL = (VOUT - VIN_min*sqrt(2)) / VOUT = 0.69
K(D_PLL) = 0.55
delta_IL = (POUT*sqrt(2)*0.3) / (VIN_min * eta * K(D_PLL)) = 3.0 A
L1 = L2 = (VIN_min*sqrt(2)*D_PLL) / (delta_IL * fS) = 140 uH
```
Selected: Cooper CTX16-18060, 140 uH (swings from 140-350 uH with DC bias). Average inductance for compensation: LAVG = (140+350)/2 = 245 uH.

Inductor RMS current per phase:
```
IL_rms = integral over half-line-cycle of sqrt(Iavg^2 + (ripple)^2) ~ 2 A
```

#### Current Sense Transformer Design

Turns ratio NCT = 50 (primary current / sense current). Key requirements:
```
Magnetizing inductance: LM >= (VS * VOUT * NCT) / (IPEAK * VOUT * fS * 0.02) = 6.24 mH
```
Selected: 8.25 mH (Cooper CTX16-18294). The 2% magnetizing current error budget ensures accurate current measurement.

Sense resistors: RS = 0.9 * VS / (IPEAK / NCT) = 33.2 ohm. Factor 0.9 leaves room for the 10% PWM ramp added for noise immunity.

**Noise immunity at light load**: When inductor current becomes discontinuous, parasitic ringing through the current sense transformer creates false current signals. Solution: add a DC offset (VOFF = 200 mV) to the sense resistor via ROA/ROB resistors, plus a PWM-synchronized ramp (RTA/CTA network) that is activated/deactivated by gate drive outputs. This blocks the ringing signal from triggering false current sensing.

#### UCC28070 Unique Features

- **Phase management**: Automatically generates 180-degree phase-shifted gate drives for two boost stages
- **Frequency dithering**: Reduces conducted EMI by 4.35 dBuV at quasi-peak measurement (spreads switching frequency energy across a band rather than concentrating at harmonics)
- **Integrated average current mode control**: Two independent current amplifiers with individual current sense inputs (CSA, CSB)
- **Synthesized current ramp (RSYNTH)**: Internal ramp signal synthesis for current sensing, alternative to external current sense transformers
- **Programmable DMAX**: Maximum duty cycle clamp via external resistor
- **Soft start**: Single capacitor programs both soft-start time and restart delay (19:1 ratio)

#### Measured Performance (300 W Prototype)

- **Efficiency at 85 V/300 W**: ~92%
- **Efficiency at 115 V/300 W**: ~94%
- **Efficiency at 230 V/300 W**: ~96%
- **Efficiency at 265 V/300 W**: ~96%
- **Power factor at 115 V/300 W**: >0.97
- **Power factor at 230 V/300 W**: >0.98
- **Harmonic content at 230 V/300 W**: All harmonics below EN61000-3-2 Class D limits
- **Startup time (85 V, 300 W)**: ~150 ms to regulated output
- **Recovery from line dropout**: Returns to regulation within ~200 ms
- **EMI with frequency dithering**: 4.35 dBuV reduction in quasi-peak conducted emissions

#### Practical Design Guidance (Common to Both Designs)

1. **SiC diodes are essential**: Both designs use CREE SiC Schottky diodes to eliminate reverse recovery losses. At 100-200 kHz, silicon ultrafast diodes would dissipate several watts of reverse recovery loss per diode.
2. **Current sense transformer magnetizing inductance**: Must be large enough that magnetizing current error is <2% of the sensed signal. Insufficient LM causes current distortion and poor PF.
3. **Slope compensation required**: Interleaved average current mode requires slope compensation. The UCC28220 requires external RSLOPE; the UCC28070 integrates a synthesized ramp. Excessive slope compensation doubles peak startup current.
4. **Voltage loop bandwidth constraint**: Must be well below 2*fLINE (target 10 Hz) to avoid injecting 2nd harmonic into current reference, which creates 3rd harmonic input current distortion.
5. **VFF (voltage feedforward) filtering**: Filter pole must attenuate the AC component of VFF enough that its contribution to THD is <1.5%. Under-filtered VFF causes power-dependent current distortion.
6. **Output capacitor selection**: Holdup energy requirement typically dominates over ripple requirement. Always de-rate for tolerance (20%) and aging (20%). Interleaving halves the high-frequency RMS current, allowing smaller/cheaper capacitors.
7. **Heat sinking**: At 90%+ efficiency, semiconductor losses of 15-20 W require heatsinks rated at RSA < 17 C/W for FETs in TO-220 packages at 40 C ambient.
