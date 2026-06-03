# GaN Gate Drive Design Guide

Source: Lidow, Reusch, Strydom, de Rooij, Glaser -- "GaN Transistors for Efficient Power Conversion" (3rd ed, Wiley 2020), Chapters 3, 4, 5.

## Gate Drive Voltage Requirements

GaN transistors have a much tighter gate voltage window than silicon MOSFETs:

- **Enhancement-mode (e.g. EPC)**: Absolute max +6 V / -4 V. Recommended drive: 4.0 V to 5.25 V. RDS(on) is essentially flat from 4 V to 5 V. Keep below 5.25 V to maintain margin to the 6 V absolute max.
- **Other manufacturers (e.g. GaN Systems, Infineon)**: May specify +7 V / -10 V or +4.5 V / -10 V. Always check the specific datasheet.
- **Cascode GaN (e.g. Transphorm)**: Gate limits are +/-18 V, similar to Si MOSFETs. These are driven like MOSFETs.
- **Consequence of exceeding limits**: Permanent gate damage. Unlike Si MOSFETs with robust gate oxide, GaN gates have very little margin.

**Key difference vs MOSFETs**: Si MOSFETs typically allow +/-20 V on the gate and are driven at 8-12 V. GaN enhancement-mode devices have roughly 1 V of margin between recommended drive voltage and absolute maximum. This demands careful attention to overshoot.

## Gate Drive Loop as an LCR Resonant Tank

The gate driver, GaN transistor, bypass capacitor (CVDD), and interconnect inductance (LG) form a series LCR resonant circuit. This is the fundamental model for GaN gate drive design.

### Critical Damping Requirement

To prevent gate voltage overshoot beyond the absolute maximum, the gate loop must be at least critically damped on the turn-on edge:

```
R_G(eq) >= sqrt(4 * LG / CGS)
```

Where R_G(eq) = R_G(internal) + R_source(driver) + R_external.

### Separate Turn-On and Turn-Off Resistance

- **Turn-on**: Must be critically damped to avoid positive overshoot beyond +6 V (or device max).
- **Turn-off**: Can be under-damped (faster) because the negative limit (-4 V) provides more margin. Some negative ringing is acceptable, but subsequent positive ringing must stay below VGS(th) to avoid re-triggering the device.
- **Implementation**: Use separate pull-up and pull-down gate resistors. Many GaN-specific gate drivers provide separate source/sink outputs.

### Minimizing Gate Loop Inductance (LG)

Lower LG allows lower R_G(eq) while maintaining critical damping, enabling faster switching:

- Place gate driver as close as possible to the GaN transistor
- Use the source pads closest to the gate as the gate-return path
- Route gate and gate-return on adjacent PCB layers for magnetic field cancellation
- Keep the gate bypass capacitor (CVDD) in the turn-on loop, close to the driver
- The turn-off loop is smaller (does not include CVDD), so it naturally has lower inductance

## dv/dt Induced Turn-On (Miller Effect)

This is one of the most critical failure modes in GaN half-bridge circuits. When the complementary device switches, the high dv/dt couples through CGD (Miller capacitance) and can induce enough gate voltage to falsely turn on the device.

### The Miller Ratio

```
Miller Ratio = QGD / QGS1
```

- If the voltage induced on the gate by dv/dt across CGD exceeds VGS(th), the device turns on spuriously.
- GaN transistors have very low QGD (which is good for switching speed) but also very low QGS1, making the Miller ratio less favorable than it first appears.
- The critical metric is whether CGD / (CGD + CGS) times the bus voltage exceeds VGS(th).

### Prevention Strategies

1. **Keep the gate pulled low during off-state**: Use a strong pull-down (low impedance from gate to source) when the device is off. Do not leave the gate floating.
2. **Minimize common-source inductance (CSI)**: CSI couples power loop di/dt into the gate loop, adding to the Miller effect. CSI voltage opposes gate drive during turn-on (slowing it) but can assist false turn-on during dv/dt events.
3. **Controlled gate timing**: Use under-damped turn-off so that the gate is driven slightly negative at the moment the complementary device creates the dv/dt. This provides additional margin against Miller turn-on.
4. **Do NOT use negative gate drive voltage for enhancement-mode GaN**: While it helps prevent dv/dt turn-on, it increases the reverse conduction voltage drop during dead time (adding approximately 1 V per volt of negative bias), increasing dead-time losses. It is not recommended.
5. **Never add external CGS**: Unlike MOSFETs, adding capacitance from gate to source slows down the intentional gate drive without proportionally helping dv/dt immunity (because it also slows the pull-down response).

### Characteristic dv/dt Turn-On Signature

When dv/dt turn-on occurs, the drain voltage waveform shows a characteristic "knee" where the voltage rise rate becomes self-limited. The device partially turns on, clamping the dv/dt. This reduces efficiency and can cause shoot-through.

## di/dt and Common-Source Inductance (CSI)

### What is CSI?

Common-source inductance is the inductance on the source side of a GaN transistor that is shared between the power loop (drain-to-source current path) and the gate drive loop (gate-to-source drive path). It is the single most critical parasitic for GaN circuits.

### Effects of CSI

- **During turn-on**: Rising drain current creates a voltage across CSI (V = L * di/dt) that opposes the gate drive voltage. This slows the current rise, extending the overlap period and increasing switching losses.
- **During turn-off**: Falling drain current creates a voltage across CSI that enhances the gate drive, accelerating turn-off (beneficial).
- **Net effect**: CSI is always detrimental to overall losses because the turn-on penalty exceeds the turn-off benefit.

### Minimizing CSI

- Use devices with dedicated gate-return (Kelvin source) pins. Connect the gate driver return directly to this pin.
- For LGA/BGA devices without a dedicated Kelvin source, allocate the source pads closest to the gate as the gate-return connection. Route gate loop and power loop currents in opposite or orthogonal directions from this connection.
- Keep the gate driver physically close to the device source connection.
- CSI symmetry is the highest priority when paralleling GaN devices.

## Bootstrap Considerations for GaN

### Standard Bootstrap Operation

Bootstrap circuits charge a capacitor (CBOOT) through a diode from VDD to the high-side gate driver supply. The capacitor charges when the low-side device is on (switch node near ground).

### GaN-Specific Bootstrap Challenges

1. **Higher reverse conduction voltage**: GaN has no body diode. The reverse conduction voltage during dead time is higher than a Si MOSFET body diode (~2.0-2.5 V vs ~0.7 V for Si). This can cause the bootstrap capacitor to overcharge during dead time, potentially exceeding the gate voltage limit.

2. **Bootstrap voltage regulation**: At high duty cycles and high switching frequencies, the bootstrap capacitor may not fully charge during the short low-side on-time. Conversely, at low duty cycles, leakage and driver quiescent current can drain the bootstrap cap.

3. **Light-load bootstrap refresh**: At light load, some converters enter pulse-skipping or burst mode. The bootstrap capacitor may discharge below the UVLO threshold if not periodically refreshed.

4. **Bootstrap diode selection**: Must be fast (low Qrr) to avoid charging losses and must tolerate the full bus voltage. Schottky diodes are preferred. The diode's forward drop directly reduces the available gate drive voltage.

### Recommended Solutions

- Use gate drivers with integrated bootstrap regulation and clamp circuits (e.g. LMG1210, LMG3410). These clamp the bootstrap voltage to a safe level.
- For multilevel converters, cascaded diode bootstraps suffer from cumulative diode drops that can cause upper-level drivers to have insufficient gate voltage. Use cascaded synchronous bootstrapping with GaN transistors replacing the bootstrap diodes.
- Size CBOOT to maintain less than 100-200 mV voltage droop during one switching cycle. Typical values: 100 nF to 1 uF depending on frequency and gate charge.

## Dead Time Optimization

Dead time is more critical in GaN circuits than in MOSFET circuits because GaN has no body diode and has higher reverse conduction voltage.

### GaN Reverse Conduction Mechanism

- GaN transistors conduct in reverse through the 2DEG channel when VGS = 0 V and current flows into the source.
- The effective "diode" forward voltage is approximately: VSD = VGS(th) + I * RDS(on). For typical devices this is 1.5-4 V depending on current, compared to 0.5-1.0 V for a Si MOSFET body diode.
- There is zero reverse recovery charge (Qrr = 0). This is a major advantage: no reverse recovery losses.

### Dead Time Loss Equation

```
P_SD = (I_L,turn_off * V_SD1 * t_SD1 + I_L,turn_on * V_SD2 * t_SD2) * f_sw
```

Because VSD is high (2-4 V), dead time losses can be significant. Excessive dead time is costly.

### Dead Time Guidelines

- **Minimize dead time**: GaN's zero Qrr means there is no need for large dead times to allow reverse recovery. The only purpose of dead time is to prevent shoot-through and (optionally) allow ZVS.
- **Adaptive dead time**: Strongly recommended. Fixed dead time wastes power at all operating points except the design point. Adaptive dead time controllers can set the dead time to the minimum safe value.
- **Typical values**: 5-20 ns for optimized GaN designs at MHz switching frequencies. Compare with 30-100 ns typical for MOSFETs.
- **Anti-parallel Schottky diode**: Adding a Schottky diode (with ~0.3 V forward drop) in parallel with the GaN transistor can reduce dead-time conduction losses. However, the Schottky diode adds parasitic capacitance and inductance. Net benefit depends on dead time duration, current level, and switching frequency. Generally beneficial only when dead time cannot be minimized by other means.

## PCB Layout Requirements for Gate Drive Loop

### Priority Order for Layout Parasitics

1. **Common-source inductance (CSI)**: Highest priority. Must be minimized and, when paralleling devices, must be symmetric.
2. **Power loop inductance**: Second priority. Determines voltage overshoot and switching speed.
3. **Gate loop inductance**: Third priority. Determines achievable switching speed and gate voltage overshoot.

### Gate Loop Layout Rules

- Gate and gate-return traces should be on adjacent PCB layers with currents flowing in opposite directions (magnetic field cancellation).
- The gate driver bypass capacitor (CVDD) must be placed within the gate turn-on loop, as close to the driver as possible.
- Each paralleled device should have its own individual gate resistor (both pull-up and pull-down) for independent speed adjustment.
- The gate-return source plane should be a dedicated copper area not connected to the power ground at any other point. This isolates the gate loop from power loop transients.

### Transient Immunity / Ground Bounce

- High dv/dt switching generates large transient currents through parasitic capacitances. These currents flowing through layout inductances create "ground bounce" voltage pulses.
- Ground bounce can change the logic state of gate driver inputs, causing unintended switching.
- Best mitigation: Place the controller on the same ground as the gate driver source return. Use the GaN device source as the local ground reference for the gate driver, and accept that the controller ground will "bounce" relative to the system ground.

## Key Differences: GaN vs MOSFET Gate Driving

| Parameter | Si MOSFET | Enhancement-Mode GaN |
|-----------|-----------|---------------------|
| Gate voltage range | +/-20 V typical | +6 V / -4 V typical (varies by mfr) |
| Recommended VGS | 8-12 V | 4-5 V |
| Gate charge QG | 20-100 nC typical | 1-10 nC typical (10x lower) |
| Body diode | Yes (PN junction) | No (2DEG reverse conduction) |
| Reverse recovery Qrr | 20-200 nC | 0 nC |
| Reverse conduction VSD | 0.5-1.0 V | 1.5-4.0 V (higher) |
| Miller plateau | Prominent, long duration | Brief or absent (fast switching) |
| dv/dt sensitivity | Moderate | High (demands attention) |
| CSI sensitivity | Moderate | Very high (dominant loss mechanism) |
| Dead time criticality | Moderate | High (high VSD makes dead time costly) |
| Negative gate drive | Common, beneficial | Not recommended (increases VSD) |
| Gate drive topology | Half-bridge drivers, bootstrap | Dedicated GaN drivers recommended |

## Gate Driver IC Selection

### Desirable Features for GaN Gate Drivers

- 5 V gate drive output with tight regulation (+/- 5% or better)
- Fast rise/fall times (< 2 ns)
- Low output impedance (< 1 ohm source, < 0.5 ohm sink)
- Integrated bootstrap with voltage clamping
- Separate source and sink outputs for independent resistor tuning
- High CMTI (common-mode transient immunity) > 100 V/ns for high-side driver
- Under-voltage lockout (UVLO) to prevent operation with insufficient gate voltage
- Short propagation delay and tight matching between high-side and low-side channels

### Example GaN-Specific Gate Drivers

- **TI LMG1210**: Half-bridge driver, 200 V/ns CMTI, adjustable dead time, integrated bootstrap FET
- **TI LMG3410/3411**: Integrated driver + GaN FET, eliminates gate loop inductance entirely
- **Silicon Labs Si827x**: Isolated gate driver, high CMTI
- **EPC (monolithic half-bridge ICs)**: Driver integrated on the same GaN die, eliminating all external gate loop parasitics

## Measurement Considerations

From Chapter 5 -- when debugging GaN gate drive circuits:

- **Probe bandwidth**: Gate drive signals require >= 500 MHz bandwidth probes. Standard 10x passive probes are inadequate.
- **Probe loading**: A 10 pF probe tip capacitance is comparable to the GaN input capacitance (~1 nF at low VGS but varying). Use active probes or low-capacitance differential probes.
- **Ground lead inductance**: Standard probe ground clips add 5-15 nH of inductance, which can ring with probe capacitance and create measurement artifacts. Use probe tip ground springs or solder-in probe points.
- **Non-ground-referenced signals**: The high-side gate-source voltage cannot be measured with a ground-referenced probe. Use a differential probe with high CMRR at the frequencies of interest (> 100 MHz).
- **Current measurement**: Rogowski coils or coaxial shunts are recommended. Standard current probes lack the bandwidth needed for GaN switching events (< 5 ns transitions).

---

## General MOSFET/IGBT Gate Drive Design (from TI SLUA618)

Source: Balogh, "Fundamentals of MOSFET and IGBT Gate Driver Circuits" (TI SLUA618A, Rev. Oct 2018).

### MOSFET Switching Model

The MOSFET is a charge-controlled device. Switching speed is determined by how fast the parasitic capacitances can be charged/discharged, not by carrier transit time.

**Key parasitic capacitances:**
- **CGS**: Gate-to-source, mostly linear, defined by overlap of source/channel under gate
- **CGD**: Gate-to-drain (Miller capacitor), highly nonlinear -- large at low VDS, small at high VDS
- **CDS**: Drain-to-source, junction capacitance of body diode, nonlinear

Datasheet values CISS, CRSS, COSS relate to these as:
```
CGD = CRSS
CGS = CISS - CRSS
CDS = COSS - CRSS
```

The Miller effect amplifies CGD in switching applications:
```
CGD,eff = (1 + gfs * RL) * CGD
```

### Gate Charge Analysis

The gate charge curve (QG vs VGS) reveals four switching intervals:

**Turn-on intervals:**
1. **Turn-on delay (t1)**: VGS rises from 0 to VTH. CGS charges. Drain current and voltage unchanged.
2. **Current rise (t2)**: VGS rises from VTH to VGS,Miller. Drain current ramps up linearly. VDS unchanged (diode still conducting).
3. **Miller plateau (t3)**: VGS stays at VGS,Miller. All gate current discharges CGD. VDS falls from VDS,off to near 0. This is where switching loss occurs.
4. **Overdrive (t4)**: VGS rises from VGS,Miller to VDRV. RDS(on) decreases to final value.

**Turn-off is the reverse sequence.** The Miller plateau voltage is:
```
VGS,Miller = VTH + ID / gfs
```

**Critical insight**: The most important driver characteristic is source/sink current capability at the Miller plateau voltage (~5 V for standard MOSFETs, ~2.5 V for logic level). Peak current at VDRV is NOT representative of actual switching performance.

### Gate Drive Power Loss

```
P_gate = VDRV * QG * f_sw
```
This power is dissipated in the resistive path (driver output impedance + RGATE + RG,internal), split proportionally:
```
P_driver_on  = (R_HI / (R_HI + RGATE + RG,I)) * VDRV * QG * f_sw / 2
P_driver_off = (R_LO / (R_LO + RGATE + RG,I)) * VDRV * QG * f_sw / 2
P_total_driver = P_driver_on + P_driver_off
```

### Switching Loss Estimation

```
P_sw = (VDS,off * IL / 2) * (t2 + t3) * f_sw
```

Where the switching times depend on gate drive current at the Miller plateau:
```
IG2 = (VDRV - 0.5*(VGS,Miller + VTH)) / (R_HI + RGATE + RG,I)
IG3 = (VDRV - VGS,Miller) / (R_HI + RGATE + RG,I)
t2 = (VGS,Miller - VTH) * CISS / IG2
t3 = VDS,off * CRSS / IG3
```

### Source Inductance Effects

Source inductance (LS) is the most critical parasitic in the gate drive loop:

1. **Resonance**: LS and CISS form a resonant circuit excited by gate drive edges. The optimal gate resistor for critical damping:
```
RGATE,opt = 2 * sqrt(LS / CISS) - RG,I - R_driver
```

2. **Negative feedback**: During current rise (t2), LS * di/dt opposes the gate voltage, slowing switching. This is the primary reason for using Kelvin source connections.

3. **Drain inductance (LD)**: Acts as turn-on snubber (reduces turn-on loss) but causes voltage overshoot at turn-off (increases turn-off loss and peak VDS stress).

### Driver Bypass Capacitor Sizing

```
CDRV >= (IQ,HI * DMAX + QG * f_sw) / (delta_V * f_sw)
```
Where delta_V is the acceptable ripple on the driver supply (typically 100-200 mV). Rule of thumb: CDRV >= 10 * QG / delta_V.

### Ground-Referenced Gate Drive Circuits

**Direct drive from PWM controller:**
- Simplest, but limited by controller drive current (<1 A typical)
- Layout critical: long trace from controller to FET adds loop inductance
- Bypass capacitor must handle gate charge transients

**Bipolar totem-pole driver:**
- NPN (pull-up) + PNP (pull-down) driven from PWM output
- Self-protecting: base-emitter diodes clamp gate between VDRV+VBE and GND-VBE
- No Schottky protection needed (unlike integrated bipolar drivers)
- Place directly next to MOSFET

**Turn-off speed enhancement circuits:**
1. **Anti-parallel diode**: Shunts RGATE during turn-off. Simple but only helps when gate current exceeds diode VF/RGATE (~150-300 mA)
2. **PNP turn-off transistor**: Shorts gate to source locally. Most popular solution. Gate clamped to GND-VBE. High peak discharge current stays local (no ground bounce).
3. **NPN turn-off**: Holds gate closer to GND. Requires inverted PWM signal. Self-biasing during power-up (keeps MOSFET off).

### dv/dt Immunity

Maximum dv/dt the MOSFET can withstand without false turn-on:
```
dv/dt_limit = (VTH - 0.007 * (TJ - 25)) / (RG,I * CGD)
```
This is the "natural" limit with zero external impedance. With external pull-down:
```
dv/dt_max = VTH / (R_total * CGD)
where R_total = R_LO || RGATE + RG,I
```
At elevated temperature, VTH drops by ~7 mV/degC -- account for worst case.

### Synchronous Rectifier Gate Drive

Synchronous rectifiers operate in the 4th quadrant (current source-to-drain). Key differences:
- Gate charge is lower than datasheet QG (no Miller plateau since VDS ~ 0 during turn-on)
- Actual QG,SR = (CGS + CGD,avg) * VDRV, where CGD,avg is the low-voltage average
- Both turn-on and turn-off dv/dt are forced by the forward switch, not by QSR's own driver
- The SR pull-down impedance must satisfy: R_LO(SR) <= (VTH / (VDRV - VGS,plateau)) * R_HI(FW)
- Typical ratio: R_LO(SR) < 0.42 * R_HI(FW) for 10 V drive with logic-level FETs

### Bootstrap Gate Drive Design

The bootstrap technique provides a floating supply for high-side N-channel MOSFET gate drive.

**Operating principle:**
1. Low-side FET on: CBOOT charges through DBOOT from VCC to VDRV
2. High-side FET on: CBOOT powers the floating driver (referenced to switch node)
3. Level-shift circuit translates ground-referenced PWM to floating domain

**Bootstrap capacitor sizing:**
```
CBOOT >= QG / delta_V_boot
```
Where delta_V_boot is acceptable droop (100-200 mV typical). Must also supply driver quiescent current during on-time.

Rule of thumb: CDRV (ground-side bypass) >= 10 * CBOOT.

**Critical issues:**
- **Negative switch node voltage at turn-off**: Source inductance causes switch node to swing below ground. Can overcharge CBOOT and damage driver. Mitigation: relocate RGATE to source lead + Schottky clamp from GND to VS pin.
- **Maximum duty cycle limit**: CBOOT must recharge every cycle during low-side on-time. At D > 0.95, charge time may be insufficient.
- **Start-up**: First few cycles may have insufficient bootstrap voltage. Some ICs include charge pump or start-up circuit. External solutions: trickle-charge resistor from VIN, or dedicated start-up circuit.
- **Capacitive currents**: dv/dt at switch node forces current through parasitic capacitances to ground, flowing through the GND/COM pin. Layout must return these currents locally.

**High-voltage driver ICs (600 V rating):**
- Use pulsed-latch level translators to minimize power dissipation
- Typical pulse width ~120 ns adds turn-on/turn-off delay
- Limits operating frequency to < 200-300 kHz
- Some lower-voltage ICs (< 100 V) use DC level shift for lower delay

### AC-Coupled Gate Drive

AC coupling modifies the turn-on and turn-off voltages using a series capacitor CC and shunt resistor RGS:
- Gate is driven between -VCL and (VDRV - VCL), providing negative turn-off bias
- Coupling capacitor voltage: VC = D * VDRV (without clamp)
- At low D: insufficient negative bias. At high D: insufficient turn-on voltage
- Clamp circuit (Zener or diode) limits VCL for guaranteed turn-on at high duty cycles

**Sizing CC:**
```
CC,min = 20 * QG * f_sw / (VDRV * (2 * f_sw * tau - 5))
```
where tau = CC * RGS is the start-up time constant. Limit AC ripple to ~10% of VDRV.

### Transformer-Coupled Gate Drive

Used for isolated gate drive in high-voltage applications. Key design considerations:

**Volt-second balance:** The transformer must reset each cycle. For single-ended drive:
```
V * t_on = V_reset * t_reset
```
Failure to reset causes flux walking and core saturation.

**Duty cycle limitations:**
- Unidirectional (single-ended) drive: limited to D < 0.5 without auxiliary reset
- DC restore circuit (diode clamp) can recover volt-second balance for wider D range
- Bidirectional (push-pull) drive: full 0-100% duty cycle, but requires complementary signals

**Transformer design rules:**
- Low leakage inductance is critical (delays gate current delivery)
- Magnetizing inductance must be large enough to keep magnetizing current small relative to gate charge current: LM >= RC * (VDRV / (2 * IG,peak))
- Core selection: small high-frequency cores (toroids, planar). Must avoid saturation from magnetizing current DC bias at asymmetric duty cycles.
- Turns ratio typically 1:1 (no voltage scaling needed, just isolation)

**Push-pull half-bridge gate drive:**
- Drives both high-side and low-side with complementary signals from one transformer
- Three-winding transformer (primary + 2 secondaries)
- Inherent 50% duty cycle per output with adjustable dead time
- Commonly used in half-bridge and full-bridge topologies

### Driver Sizing Summary

| Parameter | How to Determine |
|-----------|-----------------|
| QG (gate charge) | From MOSFET datasheet at operating VDS and VDRV |
| Peak gate current | I_peak = (VDRV - VGS,Miller) / (R_driver + RGATE + RG,I) |
| Average drive current | I_avg = QG * f_sw |
| Driver power | P = VDRV * QG * f_sw |
| Bypass cap | CDRV >= QG / delta_V + IQ * D / (delta_V * f_sw) |
| RGATE (for speed) | Sets t2 + t3; smaller = faster switching but more ringing |
| RGATE (for damping) | R >= 2*sqrt(LS/CISS) - RG,I - R_driver |
| dv/dt immunity check | VTH(hot) / ((R_LO + RGATE + RG,I) * CRSS) > dv/dt_max |
