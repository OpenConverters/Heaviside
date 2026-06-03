# Component Selection Guide for Switchmode Power Supplies

Source: Billings & Morey, *Switchmode Power Supply Handbook*, 3rd Ed. (2010)

---

## 1. Power Switching Transistor / MOSFET Selection

### 1.1 Voltage Rating

The switching device must withstand the maximum off-state voltage, which depends on the topology:

**Flyback converter (single-ended):**
- Maximum collector/drain voltage = flyback voltage + inductive overshoot
- Flyback voltage >= 2 * Vcc_max (for 1:1 reflected ratio)
- Allow 25% margin for leakage inductance overshoot
- Example: 137 Vrms input (doubled) => Vcc = 389 V => flyback = 778 V => with margin = 972 V => select Vcex >= 1000 V

**Forward converter (single-ended):**
- Vflyback = 2 * Vcc_max + 10% overshoot margin
- Example: 130 Vrms => Vcc = 362 V => Vfb = 2 * 362 * 1.1 = 796 V => select Vcex >= 800 V

**Half-bridge:** Vds_max = Vcc (each device sees supply voltage)

**Full-bridge:** Vds_max = Vcc (each device sees supply voltage)

**General rule:** Select voltage rating >= 1.25 * calculated maximum voltage stress.

### 1.2 Current Rating

Select a device with continuous current rating at least 2x the calculated mean "on" current to ensure adequate gain margin and efficient switching.

**Mean primary current during "on" period:**

```
I_mean = (2 * P_in) / Vcc_min
```

where P_in = P_out / efficiency

**Peak current** depends on operating mode:
- Continuous mode: I_peak = I_mean * (1 + ripple_fraction/2), typically 1.1 to 1.2 * I_mean
- Discontinuous flyback: I_peak can be 3x to 6x the mean current

### 1.3 MOSFET Rds_on and Conduction Loss

```
P_conduction = I_rms^2 * Rds_on
```

- Rds_on increases with temperature (typically 1.5x to 2x at 100C vs 25C)
- Use the Rds_on at the expected junction temperature
- For paralleled MOSFETs: Rds_on_total = Rds_on / N_parallel (positive temperature coefficient enables natural current sharing)

### 1.4 Gate Charge and Switching Loss

**Simplified overlap model (rough estimate only, see switching-loss-models.md for accurate methods):**

```
P_switching = 0.5 * Vds * Id * (t_rise + t_fall) * f_sw
```

**Gate-charge-based model (recommended, per TI SLYT664 and Infineon app notes):**

```
P_cross = 0.5 * V_bus * I_load * (Q_GS2 + Q_GD) * (1/I_G_on + 1/I_G_off) * f_sw
```

**Total switching loss must also include Coss, Qrr, gate, and dead time losses.**
See `knowledge/components/switching-loss-models.md` for complete treatment with 5 models (Si, SiC, GaN, IGBT), Coss energy integral, Qrr temperature effects, and ZVS conditions.

- Gate charge Qg determines drive power: P_drive = Qg * Vgs * f_sw
- Lower Qg => faster switching, lower switching loss, but potentially more EMI
- Tradeoff: Low Rds_on devices have high Qg (larger die area)
- FOM (Figure of Merit) = Rds_on * Qg -- minimize this product
- Hard-switching FOM: (Q_GD + Q_GS2) * Rds_on -- determines overlap switching losses

### 1.5 Safe Operating Area (SOA) and Secondary Breakdown

For bipolar transistors (critical concern):
- Must ensure collector current reaches zero BEFORE collector voltage reaches Vceo
- Snubber networks required to shape the turn-off load line
- Secondary breakdown occurs when current concentrates in a small chip area during simultaneous high V and high I

For MOSFETs:
- No secondary breakdown concern (positive temp coefficient distributes current)
- Must satisfy maximum dV/dt rating to prevent parasitic BJT latch-up
- Snubber may still be required to limit dV/dt

### 1.6 Thermal Considerations

- Calculate total loss: P_total = P_conduction + P_switching + P_drive
- Junction temperature: Tj = Ta + P_total * (Rth_jc + Rth_cs + Rth_sa)
- Keep Tj well below rated maximum (150C typical for Si MOSFETs)
- Reliability halves for every 10-15C rise above 25C (MIL-HDBK-217A)

---

## 2. Diode / Rectifier Selection

### 2.1 Types and Application

| Parameter | Schottky | Ultrafast Si | Standard Si |
|---|---|---|---|
| Forward drop Vf | 0.3-0.6 V | 0.8-1.2 V | 0.8-1.0 V |
| Reverse recovery trr | ~0 (majority carrier) | 35-75 ns | 200+ ns |
| Max Vrrm | 45-200 V (typical) | 200-1200 V | up to 1200 V |
| Best for | Low-voltage outputs (<48V) | High-voltage outputs | 50/60 Hz rectification |

**Selection rule:** Use Schottky diodes wherever Vrrm rating permits. Use ultrafast recovery (trr < 75 ns) for all high-frequency rectification above Schottky voltage limits.

### 2.2 Voltage Rating (Vrrm)

- Must exceed the maximum reverse voltage with margin
- Flyback secondary diode/SR: `Vrrm >= Vout + Vbus_max/n` with derating margin
- Forward converter secondary: Vrrm >= 2 * Vout (approximately) with margin

**⚠ ACF/Flyback SR FET voltage — critical formula:**
```
Vds_SR_max = Vout + Vbus_max / n    [correct — secondary blocking voltage]
           ≠ Vout + VOR              [WRONG — VOR = n×Vout is primary-referred]

IPC-9592B derating: V_rated ≥ 1.5 × Vds_SR_max

Example: Vout=20V, n=6.67, Vbus_max=450V:
  Vds_SR_max = 20 + 450/6.67 = 87.5V → need ≥ 131V rated FET
  → 80V SR FETs are undersized for 400V-bus flybacks (n=6–8)
  → Use 100V minimum, 150V preferred
```

### 2.3 Current Rating

**Flyback converters** (high stress on rectifiers):
- RMS current: typically 1.6 to 2.0 * I_dc_out
- Peak current: up to 6 * I_dc_out
- Empirical measurement recommended due to complex waveforms

**Forward converters:**
- RMS current approximately equal to I_dc_out (lower stress than flyback)

### 2.4 Rectifier Loss Estimation

Approximate dissipation (empirical):
```
P_diode = I_rms * Vf_effective
```
where:
- Vf_effective ~ 800 mV for silicon diodes
- Vf_effective ~ 600 mV for Schottky diodes

Additional losses from reverse recovery (ultrafast Si):
```
P_rr = Qrr * V_reverse * f_sw          [total system loss, use for efficiency budget]
P_rr_diode = 0.5 * Qrr * V_reverse * f_sw  [loss in diode only, use for diode thermal]
```
**Use Qrr at max junction temperature (125-150C) -- Qrr can 2-3x from 25C to 125C.**
See `switching-loss-models.md` Section 5 for the 0.5 vs 1.0 factor explanation.

### 2.5 Thermal Design

- Measure temperature rise in prototype; calculate junction temperature
- Provide heat sinking based on: Tj = Ta + P_diode * (Rth_jc + Rth_cs + Rth_sa)
- Junction temperature must stay within Tj_max with adequate margin

---

## 3. Capacitor Selection

### 3.1 Electrolytic Capacitors

#### 3.1.1 Key Parameters

1. **Absolute capacitance** -- sets low-frequency ripple voltage
2. **ESR (Effective Series Resistance)** -- sets high-frequency ripple voltage
3. **ESL (Effective Series Inductance)** -- affects transient response
4. **Ripple current rating** -- sets thermal limit and lifetime

#### 3.1.2 Minimum Capacitance (Flyback Output)

```
C = (t_off * I_dc) / V_ripple_pp
```

where:
- t_off = maximum "off" time (us)
- I_dc = load current (A)
- V_ripple_pp = allowable peak-to-peak ripple voltage (V)

Example: 5 V / 10 A output, 100 mV ripple, 18 us off-time:
```
C = (18e-6 * 10) / 0.1 = 1800 uF
```

Note: Below 100 mV ripple in a single-stage filter is not cost-effective. Add an LC post-filter stage instead.

#### 3.1.3 ESR-Dominated Ripple

In practice, the output ripple is dominated by ESR, not capacitance:
```
V_ripple_ESR = I_peak * ESR
```

Use low-ESR capacitors or parallel multiple capacitors to reduce effective ESR.

#### 3.1.4 Ripple Current Rating

**Flyback converters:** RMS ripple current in output capacitors = 1.2 to 1.4 * I_dc_out

**Correction factors for published ratings** (typically specified at 120 Hz, 85C):
- Temperature: at 40C ambient, multiply rating by ~2.0x
- Frequency: at 10 kHz, multiply by ~1.1x (voltage-dependent)
- Combined example: 2200 uF / 25 V rated at 1780 mA => at 40C, 10 kHz: 1780 * 2.0 * 1.1 = 3920 mA

**Important:** Published ratings assume sine-wave ripple. Switchmode waveforms have high harmonic content -- the real RMS value will be higher. Measurement in prototype is recommended.

#### 3.1.5 Capacitor Life and Thermal Limits

- Maximum permitted internal temperature rise from ripple current: typically 5-10C
- Absolute case temperature limit (typical): 93C (85C rated + 8C ripple rise)
- **MTBF doubles for every 10C reduction in operating temperature**
- Electrolytic capacitors become more lossy at high temperatures => risk of thermal runaway
- Proximity heating from adjacent components often exceeds internal dissipation

**Verification procedure:**
1. Measure temperature rise from ripple current alone (isolate from other heat sources)
2. Confirm rise < manufacturer's limit (typically 5-10C)
3. Mount in final position; measure case temperature at max load, max ambient
4. Confirm case temperature < absolute maximum rating

#### 3.1.6 Input Reservoir/Filter Capacitor Sizing

For direct-off-line supplies with capacitor input filters:
- Capacitor must supply load during non-conduction angle of rectifier
- Minimum DC voltage at full load determines holdup capability
- RMS ripple current in input capacitors at 100/120 Hz is significant and must be rated

**Bus capacitor (PFC output) life at high ambient:**
```
Capacitor life (Arrhenius): L(Ta) = L0 × 2^((T0 - Tc) / 10)
  L0 = rated life (e.g., 5000h at T0 = 105°C)
  Tc = actual capacitor core temperature (ambient + self-heating + nearby components)

At Tc = 80°C with L0 = 5000h: L = 5000 × 2^((105-80)/10) = 5000 × 2^2.5 = 28,284h ≈ 3.2 yr
At Tc = 80°C with L0 = 10000h: L = 10000 × 2^2.5 = 56,568h ≈ 6.5 yr

For 10-year target at Ta = 85°C: specify L0 ≥ 20,000h rated capacitor,
or derate Ta to ≤ 70°C for the 10-year requirement.
→ Always specify Nippon Chemi-Con KY / Panasonic FM series (10,000h+) for bus caps
  in designs targeting Class II (-40 to +85°C) operation.
```

**Bus capacitor discharge hazard (IEC 62368-1 §5.7.4):**
```
Bus capacitor (470µF/450V) stores E = ½ × 470e-6 × 450² = 47.6J
Passive discharge time to <60V with 1MΩ bleed: τ = 470s → ~40 minutes
IEC 62368-1 requires <1s to <120V for accessible terminals.

For open-frame modules: add safety warning in datasheet.
For end-equipment: mandate active discharge or 100kΩ/2W passive bleed (τ = 47s).
ALWAYS include bus cap discharge provisions in application section.
```

#### 3.1.7 Output Capacitor Voltage Rating — Derate vs OVP, Not vs Vout

```
IPC-9592B §3.2.2: V_rated ≥ 1.25 × V_max_operational
V_max_operational = OVP trip threshold (not Vout nominal)

Example: Vout = 20V, OVP = 23V (115%)
  Wrong: V_rated ≥ 1.25 × 20V = 25V   → 25V cap at 1.25× Vout but only 1.09× at OVP
  Correct: V_rated ≥ 1.25 × 23V = 28.8V → use 35V-rated capacitors

Rule: Always select output capacitors rated for ≥ 1.25× OVP_threshold.
```

### 3.2 Ceramic Capacitors

- Excellent for high-frequency decoupling and output filtering (very low ESR/ESL)
- **DC bias derating:** Capacitance of Class II ceramics (X5R, X7R) drops significantly under DC bias (can lose 50-80% of rated capacitance at rated voltage)
- **Voltage coefficient:** Capacitance changes with applied voltage (nonlinear)
- **Temperature coefficient:** X7R: +/-15% over -55C to +125C; X5R: +/-15% over -55C to +85C; C0G/NP0: essentially zero TC
- Use C0G/NP0 for precision timing/filtering; X7R for bulk decoupling
- Piezoelectric effect (audible noise) in some applications -- consider if near audio-frequency switching

### 3.3 Film Capacitors

- Metalized polyester/polypropylene: very high ripple current capability
- Example: 3.3 uF / 450 V film capacitor can handle ~15 A RMS ripple at 50 kHz
- Ideal for high-frequency current steering in parallel with electrolytics
- Used in snubber circuits (low ESR, high pulse current capability)
- Use series resistance (or NTC) with electrolytic caps to steer HF ripple into film caps

**Ripple current steering arrangement (high-power PFC example):**
- Place film capacitors (e.g., 2 x 3.3 uF) in parallel with electrolytic capacitors
- Insert NTC thermistors (cold: ~50 ohm, hot: ~2 ohm) in series with each electrolytic
- HF ripple current flows preferentially through low-impedance film capacitors
- NTCs also provide inrush current limiting at startup

---

## 4. Inductor / Choke Selection

### 4.1 Terminology

- **Inductor:** Wound component with no DC current (filters, EMI)
- **Choke:** Wound component carrying large DC bias current with small AC ripple (output filters, PFC)

### 4.2 Key Selection Parameters

| Parameter | Significance |
|---|---|
| Inductance (L) | Sets ripple current magnitude and continuous/discontinuous boundary |
| Saturation current (I_sat) | Maximum DC + peak AC current before inductance drops (typically defined at 10-30% inductance reduction) |
| DC resistance (DCR) | Copper loss = I_dc^2 * DCR |
| Core material | Determines frequency range, core loss, and saturation behavior |
| AC resistance (Rac) | Skin and proximity effects increase winding loss at high frequency |

### 4.3 Inductance Value Selection

**Buck regulator output choke:**

```
L = (Vin - Vout) * D / (delta_I * f_sw)
```

where:
- D = duty cycle = Vout / Vin
- delta_I = peak-to-peak ripple current (typically 10-40% of I_load)
- f_sw = switching frequency

**Boost PFC choke:**

```
L = V_in * t_on / delta_I
```

Maximum ripple occurs at D = 50% (when Vin = Vout/2).

Typical design target: 5-20% ripple current relative to peak load current.

### 4.4 Saturation Current

- Choke must not saturate at I_dc_max + 0.5 * delta_I_pp
- Include margin for transient overcurrent conditions
- Iron powder cores: gradual saturation (soft), permeability remains linear to >150 Oe for some materials
- Ferrite cores: sharp saturation -- must use air gap for DC bias applications

### 4.5 Core Material vs. Frequency

| Material | Frequency Range | Notes |
|---|---|---|
| Iron powder (#2, #26, etc.) | 10 kHz - 200 kHz | Low cost, soft saturation, inherent distributed gap, higher loss than ferrite |
| Ferrite (gapped) | 20 kHz - 2 MHz | Low core loss, sharp saturation (needs gap), good for high-frequency chokes |
| MPP (Molypermalloy) | 10 kHz - 1 MHz | Low core loss, soft saturation, expensive, excellent for high-Q inductors |
| Sendust/Kool-Mu | 10 kHz - 500 kHz | Moderate cost, soft saturation, good for moderate-power chokes |

### 4.6 Core Loss Estimation

```
P_core = k * f^alpha * (delta_B)^beta * Volume
```

where delta_B is the AC flux density swing (not DC bias). Core loss data is obtained from manufacturer curves (mW/cm^3 vs. B_peak at given frequency).

### 4.7 Copper Loss (DC + AC)

```
P_copper = I_dc_rms^2 * R_dc + I_ac_rms^2 * R_ac
```

where R_ac = R_dc * Fr (the AC/DC resistance ratio due to skin and proximity effects).

**Critical warning:** Even when the AC ripple current is small compared to DC, the AC copper loss can exceed DC copper loss if a single solid conductor is used. In one design example (1 mH PFC choke, 10.9 A DC, 0.66 A AC at 50 kHz):
- DC copper loss: 17 W (using #13 AWG solid wire)
- AC copper loss: 20 W (Fr = 80 for 5 layers of solid wire!)

**Solution:** Use multi-strand (Litz) wire. Example: 13 strands of #24 AWG reduced AC loss dramatically while maintaining the same current rating.

### 4.8 Temperature Rise

```
T_rise = P_total / (h * A_surface)
```

where:
- P_total = P_core + P_copper (total dissipation)
- h = heat transfer coefficient (depends on convection/radiation)
- A_surface = exposed surface area

Use manufacturer thermal resistance curves or empirical data for the chosen core size. Typical target: 30-40C rise in free air.

---

## 5. Input Rectifier Selection (AC Mains)

### 5.1 Bridge Rectifier

- Must handle peak inrush current at startup (before NTC/active limiting takes effect)
- Repetitive peak current rating must exceed the peak capacitor charging current
- Voltage rating: >= 1.414 * Vin_max_rms with margin (typically 600 V for universal input)
- Average current: approximately I_dc_load / efficiency / PF

### 5.2 Transient Protection Devices (Input Surge)

**Metal Oxide Varistors (MOV):**
- Voltage-dependent resistance; conduct above turnover voltage
- Advantages: low cost, high energy absorption
- Limitations: progressive degradation with repetitive stress; high slope resistance (1250 V at 500 A for a 275 V MOV)
- Not suitable as sole protection for high-stress locations

**Transient Suppressor Diodes (TVS):**
- Very fast clamping (nanoseconds), very low slope resistance
- Terminal voltage only 220 V at 200 A for a 200 V bidirectional device
- Disadvantage: limited current capability, higher cost
- Fail to short circuit (clears fuse -- fails safe)

**Gas-Filled Surge Arresters:**
- Handle thousands of amperes; arc drop ~25 V
- Disadvantage: slow striking, dV/dt dependent, may not extinguish after transient
- Require current-limiting inductors and/or fuses in series
- Best used with MOV + TVS in combination filters

**Recommended approach:** Multi-stage protection combining all three device types with filter inductors and capacitors.

---

## 6. General Component Selection Philosophy

1. **Calculate** stress levels (voltage, current, power) from topology equations
2. **Derate** -- select components with ratings exceeding calculated stress by 25-30% minimum
3. **Prototype** -- build and measure actual waveforms; RMS current meters with adequate crest factor (>=10:1)
4. **Verify thermally** -- measure component temperatures in the finished unit at maximum load, maximum ambient
5. **Iterate** -- optimize based on measurements; calculations alone are insufficient due to parasitic effects (leakage inductance, ESR, ESL, layout parasitics)

The most important parameter for long-term reliability is component temperature in the working environment. Design for low temperature operation whenever possible.

---

## Current Sensing Methods (from SLUA535 -- Active Clamp Forward Design)

Source: Texas Instruments SLUA535A, "Understanding and Designing an Active Clamp Current Mode Controlled Converter Using the UCC2897A."

### 7.1 Sense Resistor (Direct)

The simplest approach: a low-value resistor in the switch current path (typically in the MOSFET source).

**Design equations:**

```
R_cs = V_cs_threshold / I_peak_max
P_cs = I_rms^2 * R_cs
```

**Example (100 W forward, 30 A output):** With V_cs = 0.43 V threshold and I_peak = 6.25 A primary:
- R_cs = 0.43 / 6.25 = 69 mohm
- P_cs = (3.91 A_rms)^2 * 0.069 = 1.06 W (~1% efficiency penalty)

**Pros:**
- Simple, low cost, no additional components
- DC-accurate (no magnetizing current error)
- No reset circuit needed

**Cons:**
- Power dissipation (I^2*R) directly reduces efficiency -- significant at high currents
- Introduces source inductance (degrades MOSFET switching speed)
- Voltage rating and pulse handling must be adequate
- Requires low-inductance resistor (reverse-geometry or 4-terminal Kelvin)

### 7.2 Current Sense Transformer (CT)

A small transformer (typical ratio 1:100) reduces the sensed current, allowing a much larger sense resistor with lower power dissipation.

**Design equations:**

```
R_cs = V_cs_threshold * N / (I_peak_max)
P_total = P_primary_winding + P_secondary_winding + P_diode + P_resistor

P_primary = I_rms^2 * R_pri           (primary winding DCR loss)
P_secondary = (I_rms/N)^2 * R_sec     (secondary winding DCR loss)
P_diode = (I_rms/N)^2 * V_f           (rectifier forward drop)
P_resistor = (I_rms/N)^2 * R_cs       (sense resistor loss)
```

**Magnetizing inductance and reset:** The CT requires a reset network. The magnetizing current must have a path to flow when the power switch turns off. A reset resistor RR in parallel with the secondary provides this path:

```
R_reset = (V_cs_threshold + V_diode) * N * D_max / ((1 - D_max) * I_mag)
```

The magnetizing current adds a DC offset error to the sensed current. Minimize by choosing a CT with high magnetizing inductance.

**Example (same 100 W forward):** With 100:1 CT (Pulse P8208), R_cs = 6.88 ohm:
- P_total = 91.8 mW (primary DCR) + 8.4 mW (secondary DCR) + 23.5 mW (diode) + 10.5 mW (resistor) = 134 mW
- Efficiency penalty: 0.13% (vs. 1.06% for direct sense resistor -- 8x improvement)

**Pros:**
- Much lower power dissipation than direct sensing (typically 5-10x less)
- Provides galvanic isolation of sense signal
- High sense voltage improves noise immunity
- No source inductance added to the power MOSFET

**Cons:**
- Additional component cost and board space
- Requires reset circuit (limits maximum duty cycle if not carefully designed)
- Magnetizing current introduces sensing error (offset and slope)
- Cannot sense DC current (only works with pulsed/AC waveforms)
- Bandwidth limited by leakage inductance and winding capacitance

### 7.3 Selection Guidelines

| Parameter | Sense Resistor | Current Transformer |
|---|---|---|
| Best for | Low-current designs (<2 A primary) | High-current designs (>2 A primary) |
| Efficiency impact | Significant (1-3% at high current) | Minimal (<0.2%) |
| Accuracy | Excellent (DC-accurate) | Good (magnetizing current offset) |
| Complexity | Minimal | Moderate (reset circuit, turns ratio) |
| Noise immunity | Moderate (low sense voltage ~0.5 V) | Good (higher sense voltage) |
| Duty cycle limit | None | May need careful reset design for D > 0.5 |
| Cost | Lowest | Moderate (CT + diode + resistors) |

**Decision rule:** If the sense resistor power dissipation exceeds ~0.5% of the output power, switch to a current sense transformer. For high-current, high-efficiency designs, the CT approach almost always wins.
