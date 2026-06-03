# Practical Design Guide for Isolated DC/DC Converters

> Compiled from: Scott & Isurin 2026 (Practical Design Considerations for Isolated DC/DC
> Converters, APEC S.02, Miami University / Power Converters Future)

---

## 1. Engineering Philosophy

### 1.1 Cost-Driven Design

Engineering for power electronics is ultimately a business tool. The main cost focus areas:
- Research and development costs
- Product costs (BOM, manufacturing)
- Costs after sales (warranty, maintenance, field failures)

High maintenance costs demand high reliability. Engineering is a compromise between
competing design objectives.

### 1.2 Characteristics of an Ideal Isolated DC-DC Converter

1. 100% efficiency
2. Perfect electrical isolation
3. Bidirectional power flow
4. Zero response time to load variations
5. Wide input voltage range (5-10x)
6. High power density
7. Zero electromagnetic interference (EMI)
8. Zero ripple current at input and output

**Realistic targets:**
- Efficiency: 95-98% across most of the power and voltage range
- Soft-switching achievable over most of the operating range; partial ZVS/ZCS acceptable
- Minimum output voltage above 0 V (practical limitation)
- Transformer interwinding capacitance minimized but cannot reach zero
- Some auxiliary circuits are necessary for reliability

### 1.3 Factors Influencing Cost (for P > 2 kW)

Key cost drivers: input voltage, input current, target efficiency, system complexity,
and switching frequency. All are interconnected -- optimizing one often impacts others.

---

## 2. Wide-Bandgap (WBG) Device Considerations

### 2.1 Si vs SiC MOSFET Comparison

| Parameter | Si MOSFET (IXFN32N120P) | SiC MOSFET (MSC040SMA120J) | SiC Cascode (UF4C120053K4S) |
|-----------|------------------------|---------------------------|---------------------------|
| Package | SOT-227 | SOT-227 | TO-247-4 |
| Voltage | 1200 V | 1200 V | 1200 V |
| Current (25 C) | 32 A | 53 A | 34 A |
| Rds_on | 310 mOhm (Vgs=10V) | 50 mOhm (Vgs=20V) | 53 mOhm (Vgs=12V) |
| Qg | 360 nC | 137 nC | 37.8 nC |
| Vgs_th | 3.5 V (min) | 1.8 V (min) | 4 V (min) |
| Vgs_max | +/- 30 V | -10 / +25 V | +/- 20 V |
| Vsd (body diode) | 1.5 V | 3.9 V | 1.28 V |
| Rth_jc | 0.125 C/W | 0.72 C/W | -- |
| Pd | 1000 W | 208 W | -- |

### 2.2 Key Differences

**SiC advantages:**
- Much lower on-resistance (6x in this comparison)
- Lower parasitic capacitance
- 2x or faster switching speed
- Higher thermal conductivity of SiC material

**SiC challenges:**
- On-resistance strongly dependent on Vgs (must drive to 18-20 V for full enhancement)
- Gate threshold (Vth) closer to Vgs_max -- smaller margin
- Smaller die area means higher thermal resistance junction-to-case
- Lower power dissipation capability per package
- Body diode has higher forward voltage drop (3.9 V vs 1.5 V for Si)
- Temperature coefficient of Rds_on is different: SiC Rds_on increases at low temperatures,
  complicating parallel operation and desaturation protection

**SiC MOSFET I-V characteristics:**
- Si MOSFET: Rds_on relatively constant above Vgs ~ 10 V
- SiC MOSFET: Rds_on continues to decrease from Vgs = 10 V to 20 V
- This means SiC gate must switch from 4 V to ~20 V as fast as possible to avoid
  excessive switching loss -- limits practical maximum switching frequency in hard-switching

**Temperature behavior of Rds_on:**
```
Si MOSFET:  Rds_on increases ~1.8x from 25 C to 100 C (positive tempco)
SiC MOSFET: Rds_on increases ~1.15x from 25 C to 100 C (much flatter)
SiC MOSFET: Rds_on increases at temperatures below 25 C (unusual behavior)
```

### 2.3 SiC Cascode Structure

Combines a low-voltage enhancement-mode Si MOSFET with a high-voltage depletion-mode
SiC JFET:

```
Vgs_JFET = -Vds_MOSFET
```

**Benefits of cascode:**
- Behaves like Si MOSFET from the gate-drive perspective (Vgs drive = 0-12 V)
- Gets SiC benefits (low Rds_on, fast switching)
- Wider margin between Vth and Vgs_max (4 V min vs 20 V max)
- Much smaller gate charge (37.8 nC vs 137 nC for standalone SiC)
- I-V characteristics closer to Si MOSFET (less Vgs-dependent)
- Rds_on temperature dependency similar to Si MOSFET

**For reliability: CISS/CRSS ratio should be as high as possible:**

| Device | CISS (pF) | CRSS (pF) | CISS/CRSS |
|--------|-----------|-----------|-----------|
| Si MOSFET | 21,000 | 77 | 272 |
| SiC MOSFET | 1,990 | 17 | 117 |
| SiC Cascode | 1,370 | 2.2 | 622 |

The SiC cascode has the best CISS/CRSS ratio, making it most resistant to false turn-on
from dv/dt.

### 2.4 Practical SiC Replacement Results

**Test case:** 9 kW converter, replacing Si MOSFETs with SiC, no changes to form factor
or control:

| Configuration | Power | Efficiency | Frequency |
|--------------|-------|------------|-----------|
| Original (Si) | 9 kW | 92% | 80 kHz |
| Modified (SiC) | 9.4 kW | 95.4% | 100 kHz |
| Modified (SiC, pushed) | 13.2 kW | 94.4% | 100 kHz |

Additional benefits: transformer heatsink and shielding eliminated, power stage cost reduced
from $441 to $398.

### 2.5 Recommendation for SiC

SiC transistors are NOT drop-in replacements for silicon:
- Higher switching transients require layout attention
- Lower Cgd/Cgs ratio than Si MOSFETs
- Gate-drive voltages close to maximum ratings
- Different on-resistance behavior

**Strongly recommended to use SiC with:**
- Soft-switching technology
- Sine-wave current waveforms (reduces EMI and switching stress)

---

## 3. EMI and Reliability

### 3.1 EMI Impact on Reliability

EMI directly affects converter reliability. Key considerations:

- Parasitic capacitance (Miller capacitance) creates unwanted noise and false triggering
- Transformer winding capacitance is critical -- reducing it 5x lowers EMI by ~20 dB
- Proper transformer-to-power-stage connection is essential

**Design targets for reliable operation:**
```
Maximum dV/dt: 10,000 V/us (10 V/ns)
Maximum dI/dt: 7,000 A/us (7 A/ns)
```

### 3.2 Commutation Inductance Effects

Stray inductance in traces, connections, and wirebonds causes:
- Overvoltage spikes on the die (Vspike = L_stray * di/dt)
- Extra energy loss that shortens device lifetime
- Resonance with die capacitance causing ringing

**Countermeasures:**
- Use sine-wave current for best performance
- With pure ZVS, increase internal turn-off time by increasing gate resistance
- Minimize commutation loop inductance through layout

### 3.3 Miller Capacitance Problems

False triggers due to high dv/dt through Miller capacitance (Cdg):
- Problem is worse for WBG devices (lower Ciss/Crss ratio for SiC MOSFETs)
- Cascode devices have inherently better Cdg rejection (Ciss/Crss = 622 for SiC cascode)

### 3.4 Reliability Techniques

- Use of dosed energy transfer (limits energy per switching cycle)
- Series inductance with source and load (limits di/dt)
- Reduction of active switches (fewer failure points)
- Correct selection of passive components (especially capacitors -- lifetime-limiting)

---

## 4. Regulation Methods

### 4.1 Input-Side Regulation

**Application:** Most common configuration
**Topologies:** LLC, Phase-Shifted Full Bridge, Resonant-PWM
**Advantages:** High efficiency across broad load range; simplifies control by maintaining
constant duty cycle

### 4.2 Output-Side Regulation

**Application:** Low input voltage, high input current
**Topology:** Resonance with PWM in step-up configuration
**Advantages:** Significant energy savings during low-power operation; reduced switching losses

### 4.3 Both-Sides Regulation

**Application:** Wide input and output voltage range
**Topologies:** CLLC, DAB, Dual Current-Fed
**Advantages:** Maximizes efficiency across entire load range; smooth transitions between
light and heavy load

### 4.4 Operating Modes

**Constant voltage converter:** Most widely used. Challenges with parallel operation.

**Constant current converter:** Useful for disconnected load situations and parallel operation.
Generators often modeled as current sources with voltage control loops.

**Constant power converter:** Stable output power by dynamically adjusting voltage and
current. Valuable for precise power delivery under changing conditions.

---

## 5. Topology Comparison (12 kW, 550-800 V to 30 V, 400 A)

### 5.1 Evaluation Methodology

Seven metrics for topology comparison:

1. **Cost summary:** Power stage + magnetics + filters + auxiliary + large components
   coefficient (LCC)
2. **Transformer use coefficient (Co/XFMR):** Output power / transformer VA product
3. **Power conversion duty cycle (P-C):** Time energy flows to load / switching period
4. **Stability coefficient:** % change in PWM duty cycle that changes Vout by 1% at
   nominal load
5. **Reliability estimation:** Response to overload without control compensation
6. **EMI guidelines:** dV/dt < 10 kV/us, dI/dt < 2.5 kA/us, minimize interwinding
   capacitance (reducing 5x --> 20 dB EMI reduction)
7. **Practicality of modification:** Ease of adjusting/improving the topology

### 5.2 LLC Converter

**Parameters (12 kW evaluation):**
```
Vin: 550-800 V,  Vout: 30 V
f_resonance: 131 kHz
L_res: 4 uH,  L_mag: 32 uH (ratio m ~ 9.3)
C_res: 110 nF each (half-bridge)
Turns ratio: 15:1
HV switches: Two UF4SC120030K4S in parallel
LV switches: Ten IAUC120N06S5N032 in parallel
```

**Performance:**

| Vin | Pout | Efficiency | P-C | f_com |
|-----|------|-----------|-----|-------|
| 550 V | 11.8 kW | 96.3% | 56.5% | 81 kHz |
| 650 V | 12.0 kW | 96.9% | 68.7% | 91 kHz |
| 800 V | 12.0 kW | 97.3% | 89.3% | 119 kHz |

**Overload capability (maximum power at minimum resistance):**
- At 550 V: 14.2 kW at 95.5% efficiency
- At 800 V: 22.1 kW at 95.6% efficiency

**Cost:** $520.63 (without CM filter) to $654.24 (with CM filter)

**Advantages:**
- Soft switching across all load and voltage ranges
- Cost-effective when used with pre-regulators

**Disadvantages:**
- Higher cost
- Increased size of power components
- High stress on resonant capacitors
- Complex output filtering and soft start
- Reduced efficiency at lower input voltage
- Relatively slow response time

**Application range:**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Vin max | 850 V | Limited by 1200 V semiconductors |
| Vin min | 30-200 V | Below 200 V, cost increases |
| Vin range | 1.5x | Above 1.25x, cost and efficiency suffer |
| Load range | 0-100% | Soft-switch full range |
| Iout max | ~500 A | Limited by secondary implementation cost |
| Max frequency | 1000 kHz | Limited by passive components |

### 5.3 Phase-Shift Converter

**Parameters (12 kW evaluation):**
```
Vin: 550-800 V,  Vout: 30 V
f_com: 100 kHz (constant)
L_conv: 5 uH
L1, L2: 4.2 uH each (current doubler)
Turns ratio: 15:2
HV switches: UF4SC120030K4S (one per position)
LV switches: Ten EPC2034 (GaN) in parallel
Snubber: Csnub = 30 nF, Rsnub = 1.5 Ohm
DC-bias blocking cap: Cbl = 20 uF
Soft-switching caps: L3, L4 = 170 uH, C3-C6 = 5 uF each
```

**Performance:**

| Vin | Pout | Efficiency | P-C | Pout/Tr-VA |
|-----|------|-----------|-----|-----------|
| 550 V | 11.9 kW | 97.2% | 85.4% | 0.849 |
| 700 V | 12.0 kW | 97.0% | 67.4% | 0.734 |
| 800 V | 12.0 kW | 96.9% | 59.6% | 0.689 |

**Overload capability:**
- At 550 V: 19.6 kW at 96.1% efficiency
- At 800 V: 24.5 kW at 95.1% efficiency

**Cost:** $866.56

**Advantages:**
- ZVS capability
- Simple control (constant frequency, phase-shift modulation)
- Resonant inductor can be built into transformer

**Disadvantages:**
- Circulation current during off-time
- Very difficult to maintain ZVS across full power range (idle to full)
- DC bias current through primary winding
- Hard-switched output rectifier
- Requires output filter inductor

**Application range:**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Vin max | 750 V | Limited by 1200 V devices |
| Vin range | 1.8x | Limited by secondary implementation |
| Load range (with ZVS) | 30-100% | Cannot maintain ZVS at light load |
| Max frequency | 300 kHz | Limited by hard-switched rectifier |

**Output inductor design tip:** Splitting into two series inductors (each half the value)
provides better cooling and reduces parasitic capacitance by 2-4x, improving EMI.

### 5.4 Dual Active Bridge (DAB)

**Parameters (12 kW evaluation):**
```
Vin: 550 V,  Vout: 30 V
f_com: 100 kHz
L_conv: 29 uH
Turns ratio: 20:1
Snubber: Cs1, Cs2 = 10 uF, R1, R2 = 0.2 Ohm
PCB stray inductance: L1, L2 = 5 nH each
```

**Performance:**

| Vin | Pout | Efficiency | P-C | Snubber Loss |
|-----|------|-----------|-----|-------------|
| 550 V | 11,960 W | 93.7% | 78% | 350 W |
| 675 V | 12,000 W | 97.0% | 89% | 83 W |
| 800 V | 12,000 W | 97.3% | 91.2% | 68 W |

**Fundamental problem (Isurin/Scott):** The DAB topology violates a fundamental principle of
power conversion -- energy should flow in one direction from source to load. In the DAB,
energy flows backward during part of the cycle:

```
Example at 550 V input, 12 kW:
  t0 to t3 (5 us): 61 mJ delivered to load
  t0 to t2 (4.03 us): 72 mJ transferred
  t2 to t3 (0.97 us): 11 mJ transferred, but current swings 1560 A in 50-80 ns
  t1 to t2 (0.7 us): 5.4 mJ returned from inductor to source
```

The 1560 A current swing in 50-80 ns creates severe EMI and stress on components.

**Cost:** $1,109.04 (highest of all topologies evaluated)

**Advantages:**
- Inherent bidirectional energy flow
- ZVS capability
- No synchronized rectification needed
- Simple control, constant frequency
- No output filter inductor

**Disadvantages:**
- High ripple current in Cin and Cout
- High current interruption (1560 A swing)
- Eight active switches
- Possible DC bias current
- Very difficult full-range ZVS
- Not cost-effective for currents below 500 A

**Application range:**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Vin range | 1.3x | Vout is fixed |
| Load range (with ZVS) | 60-100% | Narrow soft-switching range |
| Iout max | 100 A | Limited by switching current cost |
| Direction | Bidirectional | Only topology with native bidirectionality |
| Response time | Slow | Limited by Lconv and stability |

**Practical note:** Required 78 dB CM noise reduction; needed minimum two CM filter stages
with shielding between stages on the low-voltage side.

### 5.5 Resonance with PWM (R-PWM)

**Parameters (12 kW evaluation, step-down):**
```
Vin: 550-800 V,  Vout: 30 V
f_com: 100 kHz
L_res: 5.65 uH,  L_mag: 55 uH
C_res: 198 nF each
f_resonance: 104 kHz
Turns ratio: 9:1
HV switches T1, T2: Two UF4SC120030K4S in parallel
HV switches T3, T4: One UJ4SC075011K4S
LV switches T5-T8: Ten IAUC120N06S5N032 in parallel
```

**Performance (step-down):**

| Vin | Pout | Efficiency | P-C | Pout/Tr-VA |
|-----|------|-----------|-----|-----------|
| 550 V | 12,484 W | 96.7% | 96% | 0.92 |
| 700 V | 11,931 W | 96.7% | 87.4% | 0.88 |
| 800 V | 12,000 W | 96.7% | 85.4% | 0.875 |

**Overload capability:**
- At 800 V: 23.4 kW at 94% efficiency (nearly 2x rated power)

**Cost:** $561.54 (step-down) / $468.76 (step-up only)

**Advantages:**
- Soft-switching for all semiconductors across full power range
- Sinusoidal winding current with nearly constant duty cycle regardless of Vin
- Constant frequency with PWM or phase-shift control
- Can combine variable frequency and phase-shift control
- Power stages can connect in parallel without current sharing issues
- No output filter inductor
- Dosed energy transfer provides natural overcurrent protection
- Very wide input voltage range (3x)

**Disadvantages:**
- Excessive conduction losses on T3/T4 with internal overshoot
- Start-up is not simple
- Relatively large transformer and resonant inductor

**Application range (step-down regulation before transformer):**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Vin max | 850 V | Limited by 1200 V devices |
| Vin range | 3x | Best range of all topologies |
| Load range | 0-100% | Full-range soft-switching |
| Iout max | ~1000 A | Cost increases above 500 A |
| Max frequency | 1000 kHz | Limited by passive components |
| Response time | Fast | -- |
| Direction | Bidirectional | -- |

### 5.6 Dual Current-Fed with Active Clamp (DCF-AC)

**Parameters (step-up, 12 kW):**
```
Vin: 19-37 V, Imax: 400 A,  Vout: 650 V
f_com: 100 kHz
L_res: 3 uH,  L_mag: 10 uH
C_res: 160 nF each
L1, L2: 4 uH each
Turns ratio: 2:13
```

**Performance:**

| Vin | D/C | Pout | Efficiency | P-C |
|-----|-----|------|-----------|-----|
| 19 V | 0.65 | 7,420 W | 95.4% | 77.6% |
| 26 V | 0.5 | 10,093 W | 94.7% | 91.6% |
| 37 V | 0.35 | 12,606 W | 94.3% | 78.2% |

**Cost:** $680.47

**Advantages:**
- ~2x reduction in transformer turns ratio
- Nearly constant duty cycle winding current
- No output filter inductor
- Rectifier achieves soft-switching across full power range
- Small input current ripple
- Constant frequency with PWM control

**Disadvantages:**
- T1 and T3 turn off under hard-switch conditions
- Reverse recovery of body diodes in T2 and T4
- Relatively large resonant inductor
- Not cost-effective for 400 A applications

### 5.7 Dual Current-Fed with Switched Capacitors (DCF-SC)

**Parameters (step-up, 12 kW):**
```
Vin: 19.5-37 V, Imax: 400 A,  Vout: 650 V
f_com: 100 kHz
L_res: 13 uH,  L_mag: 10 uH
C_sw: 13.3 uF each (switched capacitors)
L1, L2: 4 uH each
Turns ratio: 2:14
```

**Performance:**

| Vin | D/C | Pout | Efficiency | P-C |
|-----|-----|------|-----------|-----|
| 19.5 V | 0.65 | 7,670 W | 96% | 96% |
| 24 V | 0.5 | 9,770 W | 95.7% | 94% |
| 30 V | 0.41 | 12,109 W | 95.6% | 94% |
| 37 V | 0.35 | 11,820 W | 95.6% | 93% |

**Cost:** $531.63 (film caps) to $643.03 (ceramic caps)

**Advantages:**
- T1/T3 turn off under ZVS at full power
- Nearly constant duty cycle regardless of Vin
- ~2x reduction in transformer turns ratio
- Small input current ripple
- No output filter inductor
- Soft-switching rectifier across full range
- Constant frequency with PWM

**Disadvantages:**
- T1-T4 require relatively high voltage rating
- High current stress on switched capacitors C1sw, C2sw
- Commutation frequency limited by switched capacitors

### 5.8 Topology Comparison Summary

**Cost comparison (step-down, 400 A):**

| Topology | Total Cost | Power Stage | Filters | Coefficient |
|----------|-----------|-------------|---------|-------------|
| DAB | $1,109 | $423 (38%) | $187 (17%) | 1.5 |
| Phase-Shift | $867 | $426 (49%) | $88 (10%) | 1.5 |
| LLC (w/ CM filter) | $654 | $375 (57%) | $126 (19%) | 1.3 |
| LLC (w/o CM filter) | $521 | $375 (72%) | $57 (11%) | 1.2 |
| R-PWM | $562 | $389 (69%) | $66 (12%) | 1.2 |

**Capability comparison:**

| Topology | P-C (%) | Co/XFMR (%) | Stab Coeff | Soft-Switch Range | Reliability | f_max |
|----------|---------|-------------|------------|-------------------|-------------|-------|
| DAB | 78-91 | 70-90 | 0.4/0.6 | Narrow | Low | 100 kHz |
| Phase-Shift | 60-85 | 69-85 | 0.81 | Needs aux | Middle | 200 kHz |
| R-PWM (step-down) | 85-96 | 87-92 | 0.97 | Full range | High | 1000 kHz |
| LLC | 56-89 | 52-62 | 0.957 | Full range | Middle | 1000 kHz |
| DCF-AC | 77-92 | 86-92 | 0.77 | Full range | Middle-High | 200 kHz |
| DCF-SC | 93-96 | 73-85 | 1.3 | Full range | High | 700 kHz |

---

## 6. Parallel Power Stage Analysis

### 6.1 Single vs Multiple Power Stages

For a 9 kW system (550-800 V to 30 V, 300 A):

| Configuration | Normalized Cost |
|--------------|----------------|
| One 9 kW stage | 1.00 |
| Two 4.5 kW stages | 1.49 |
| Three 3 kW stages | 1.72 |

**Key findings:**
- Not cost-effective for current ratings below 500 A
- More active switches and complex control reduce reliability
- Current-sharing control adds cost (unless topology inherently avoids it)
- Each parallel stage should have individual output filter for best results

### 6.2 Gate Drive with Energy Recovery

For high-current applications requiring many paralleled MOSFETs (e.g., 8 x IRF3805 with
Qg = 200 nC each):

```
Without energy recovery: P_gate = 3 W at 200 kHz
With energy recovery: P_gate = 1.2 W at 200 kHz  (60% reduction)
```

US Patent 6,570,416: Recovers gate charge energy and reduces transient overvoltage spikes
by changing gate inductance value.

---

## 7. Power Transformer Design

### 7.1 Equivalent Circuit

Complete transformer equivalent circuit includes:
- R1: primary winding resistance
- L1: primary leakage inductance (includes high-voltage leads)
- Lm: magnetizing inductance
- Rc: core loss resistance
- R2: secondary winding resistance (includes PCB traces and connections)
- L2: secondary leakage inductance (includes PCB and connections)
- C1: primary winding capacitance
- C2: secondary winding capacitance
- Cw: interwinding (primary-to-secondary) capacitance

**Critical insight:** The total inductance includes transformer AND its leads/connections.
The total resistance includes transformer AND PCB traces. The transformer cannot be
characterized in isolation -- it must be measured as installed in the power stage.

### 7.2 Measurement Method (Resonance Technique)

Setup for measuring transformer parameters integrated with the low-voltage power stage:

```
Components in measurement circuit:
  L_leak: leakage inductance
  Rac: winding AC resistance
  Lm: magnetizing inductance
  Rcore: core loss equivalent resistance
  Rcp, Lcp, Cblock: low-voltage side parasitic elements
  Rb: non-inductive power resistor (2-4 Ohm)
  Cres: resonance capacitor
  Oscilloscope channels for voltage and current measurement
```

### 7.3 Transformer Integration Principles

A power transformer cannot be used by itself -- it is part of the power converter system.
The transformer must be fully integrated into the power stage both electrically and
mechanically.

**Key design principles:**
1. Single transformer with single low-voltage side is generally smaller, more cost-effective,
   and more reliable than multiple transformers or parallel stages
2. For multi-port applications (3-4 ports), use separate transformer per port rather than
   one transformer with multiple windings
3. Keep turns ratio as low as possible
4. Minimize number of turns while meeting electrical requirements
5. The simplest transformer that meets requirements is usually the best

### 7.4 High-Current Transformer Implementation

**E-core configurations:**
- Single heatsink for transformer and low-voltage power stage
- Two-heatsink winding configuration for better thermal management

**U-core configurations (advantages for single-turn secondaries):**
- Shorter turn length
- Better cooling efficiency
- Controllable leakage inductance (high or low, by winding arrangement)
- Two-turn foil winding possible

**Leakage inductance control:**
- High leakage: separate primary and secondary on different core legs
- Low leakage: interleave primary and secondary on same leg

### 7.5 Planar Transformers

**Problem:** Planar transformers have difficulty sharing current between parallel windings.

**Comparison (7.5 kW, 30 V, 250 A, 125 kHz):**
- Planar transformers increase EMI by ~26 dB on the low-voltage side compared to
  wound transformers

**Recommendations for cost-effective planar transformers:**
- Best suited for power not exceeding 1.5-2 kW
- Winding current less than 50 A
- Isolation voltage below 1500 V
- Most advantageous when integrated into PCB (common below ~500 W)
- At high switching frequencies, insulation adequacy becomes more challenging due to
  increased dV/dt stresses

---

## 8. Power Inductor Design

### 8.1 High-Voltage Side Inductors

**AC inductors with magnetic material cover:**
- Use Litz wire to reduce skin and proximity effects
- Ferrite core with low permeability (requires air gaps or distributed gaps)
- AC magnetic components generate EM fields causing interference -- careful placement
  and shielding may be needed (increases cost)

### 8.2 Low-Voltage Side Inductors

**Differential inductors (busbar + U-cores):**
- Total losses are approximately 2x the DC losses alone
- Manufacturer datasheets typically provide only DC loss values -- this is inadequate
  for design

**Common-mode inductors:**
- More complex to implement -- require combining positive and negative busbars
- Expensive for high-current applications

**Autotransformer implementation:**
- For dual current-fed topology, autotransformers can replace discrete inductors

---

## 9. Soft-Switching Methods

### 9.1 Hard-Switching vs Soft-Switching

| Aspect | Hard-Switching | Soft-Switching |
|--------|---------------|----------------|
| Implementation | Simple | Requires specialized knowledge |
| Component stress | Increased | Minimized |
| Voltage/current spikes | Present | Low overshoot |
| EMI | Higher | Lower |
| Size/weight | Larger | Smaller |
| Efficiency/density tradeoff | Greater | Smaller |

### 9.2 Zero Voltage Switching (ZVS)

**Disadvantages to consider:**
- Dependency on commutation current (must maintain minimum current for ZVS)
- Additional capacitance and components may be needed
- Light-load ZVS maintenance is challenging

### 9.3 Zero Current Switching (ZCS)

**Disadvantages to consider:**
- High dv/dt and ringing at switch turn-off
- Energy stored in body capacitance is lost
- Commutation frequency limitations

---

## 10. Developing New Topologies -- Practical Example

### 10.1 Specification

**Bidirectional isolated DC-DC converter:**
```
Low-voltage side: Battery, 40-60 Vdc
High-voltage side: 380-420 Vdc
Step-up (discharge): Vout = 380-400 V, Iin_max = 80 A, Pout up to 4 kW
Step-down (charge): Vin = 400-420 V, Iout_max = 40 A
Cooling: Natural convection, 0-50 C ambient
EMI: CISPR 25 Class 4
Cost: Must be cost-effective
Switching frequency: 250-500 kHz (appropriate for this power level)
```

### 10.2 Topology Selection Process

**Candidate analysis:**
1. DCF-SC: ideal for this application but blocked by existing patent
2. CLLC: voltage range limited to 1.3x (need 1.5x)
3. Bidirectional R-PWM: not cost-effective (6 switches on HV side)

**Solution:** Combine CLLC and R-PWM into a hybrid topology
- CLLC section provides the bridge configuration (can be half-bridge)
- R-PWM section provides wide voltage range and full-range soft-switching
- Resulting topology inherits the best properties of both

### 10.3 Projected Performance of Combined Topology

**Step-down (from R-PWM/CLLC):**
| Parameter | Value |
|-----------|-------|
| P-C | 85-96% |
| Co/XFMR | 87-92% |
| Vin range | 3x |
| Soft-switch | Full range |
| Max frequency | 1000 kHz |
| Start-up | Self |
| Reliability | High |

---

## 11. Key Design Rules and Conclusions

### 11.1 Top-Level Design Rules

1. **Fight the cause, not the symptoms** -- start every project by defining the ideal goal,
   then estimate what is realistically achievable
2. **EMI prevention is crucial** -- no matter how much effort is spent upfront, it is always
   less than the effort to fix EMI later
3. **Cost is king** -- but must include R&D, product, and post-sales costs
4. **Single transformer preferred** -- generally results in smaller, more reliable solutions
5. **Keep magnetics simple** -- lowest possible turns ratio, minimum turns count
6. **Soft-switching is essential** for cost-effective high-frequency designs
7. **Use dosed energy transfer** for natural overcurrent protection

### 11.2 Topology Selection Guidelines

**For narrow Vin range (< 1.3x) with HV input:**
- LLC (simplest, full-range soft-switching, but limited Vin range)

**For moderate Vin range (1.5-2x) with HV input:**
- R-PWM (best overall: wide range, full soft-switching, high reliability, lowest cost)

**For wide Vin range (> 2x) with step-down:**
- R-PWM with variable frequency + phase-shift control

**For bidirectional, low Vin:**
- DCF-SC (best for high current, but limited by switched capacitor stress)
- R-PWM (most versatile)

**Avoid DAB for high-current (> 100 A) applications:**
- Highest cost ($1,109 vs $521-$867 for alternatives)
- Lowest reliability (all protection by control)
- Controversial topology for this application class

### 11.3 SiC Design Rules

- SiC MOSFETs are NOT drop-in replacements for Si
- Use soft-switching technology with SiC
- Prefer sine-wave current waveforms
- SiC cascode structure enjoys benefits of both Si and SiC technologies
- For automotive: prefer Vth >= 2-4 V for reliability (SiC MOSFET Vth too low at 1.8 V)


## 25kW SiC DC Charger Design (from How2Power/onsemi)

Source: onsemi Systems Engineering team (Filló, Rendek, Kosterec et al.),
"Developing A 25-kW SiC-Based Fast DC Charger" Parts 2, 4, 8,
How2Power Today, 2021-2022.

### System Architecture

**Two-stage architecture for 25 kW EV fast charger:**

1. **AC-DC PFC Stage**: Three-phase six-switch active rectifier (2-level)
   - Power factor: 0.99, THD < 7%
   - DC link: 800 V (high voltage to reduce currents and maximize efficiency)
   - Switching frequency: 70 kHz (keeps 2nd harmonic < 150 kHz for EMI compliance)
   - Requires 1200 V breakdown voltage switches (2-level topology)
   - Bidirectional capable

2. **DC-DC Stage**: Dual Active Bridge (DAB) with phase-shift modulation
   - Output voltage range: 200 V to 1000 V
   - Switching frequency: 100 kHz
   - Single 25 kW isolation transformer
   - External resonant inductor on primary (enables ZVS)
   - Target efficiency: 98% peak (between 650 V and 800 V output)
   - Flux-balancing control eliminates need for series blocking capacitor
   - Bidirectional capable

**Why DAB over CLLC**: DAB with phase-shift offers better efficiency across wide output voltage range. CLLC provides highest peak efficiency but control/optimization for bidirectional + wide Vout is complex, may need combined frequency + PWM modulation.

### Key Semiconductor Components

| Component | Part | Specs |
|-----------|------|-------|
| SiC power module | NXH010P120MNF1 | Half-bridge, 1200 V, 10 mohm SiC MOSFET |
| Gate driver | NCD57000 | 5 kV isolated, +4A/-6A source/sink, DESAT protection |
| Gate drive voltage | -- | +20 V turn-on (lowest Rdson), -5 V turn-off (prevent spurious turn-on) |
| Isolated bias supply | SECO-LVDCDC3064-SiC-GEVB | +20V / -5V |

**SiC driving recommendations:**
- VGS = +20 V for turn-on: unlike Si MOSFETs, SiC shows significant Rdson improvement even at high VGS
- VGS = -5 V for turn-off: reduces switching losses, prevents unintended turn-on
- Split gate resistors (separate turn-on/turn-off Rg) for independent dv/dt optimization
- High drive current essential for fast transitions that minimize switching losses
- DESAT on-chip for overcurrent protection (SiC has shorter short-circuit withstand than IGBT)

### DAB DC-DC Design Guidelines

#### Transformer Turns Ratio (n1/n2)
- Peak efficiency occurs when VSEC = VPRIM / (n1/n2)
- For 800 V DC link targeting 650-800 V output: optimal n1/n2 = 1.0:1 to 1.4:1
- Selected: **1.2:1** as best overall compromise
- Lower n1/n2: higher IPRIM_PEAK at low Vout (risk of saturation)
- Higher n1/n2: higher ISEC at high Vout, sharper efficiency falloff above optimal point
- 98% efficiency achieved across 340 V to 830 V (simulation, excluding core losses)

#### Resonant Inductance
- P = (VPRIM * VSEC * sin(gamma)) / (2*pi*fs * (LRESONANT + LLEAK))
- Selected: LRESONANT + LLEAK ~ 22 uH total
- At worst case (VSEC = 200 V): max power transfer = 11.57 kW (spec = 10 kW, sufficient margin)
- Resonant inductor supplements transformer leakage; leakage alone insufficient at high power

#### Magnetizing Inductance (LM)
- Simulated: 150 uH, 300 uH, 720 uH
- Effect on efficiency: only 0.4% difference across range (not a critical parameter)
- Higher LM -> lower IM -> smaller core Ae needed BUT more turns needed
- More turns + high RMS current (45-65 A) -> larger wire cross-section -> larger transformer
- Rule of thumb: design for IM_PEAK ~ 5-10% of IPRIM_PEAK
- Best approach: provide specs to magnetics manufacturer, let them optimize

#### Flux Balancing
- Prevents transformer saturation from flux-walking (dc bias accumulation)
- Caused by: duty cycle imbalances, Rdson asymmetry, even fine tolerances
- Traditional solution: series blocking capacitor -- impractical at 25 kW
  - Would need ~tens of uF at ~1000 V with 45-65 A RMS
  - Requires 15-20 paralleled ceramic caps or bulky film/electrolytic
- Solution: digital flux-balancing algorithm
  - Senses primary and secondary transformer currents
  - Adjusts duty cycle to maintain zero average DC current
  - Eliminates bulky capacitor, improves efficiency, reduces size/cost

### Simulation Parameters

| Parameter | Value |
|-----------|-------|
| SiC modules | 4x 1200 V, 10 mohm SiC PIM |
| DC link capacitor | 130 uF, 1.3 mohm ESR |
| Resonant inductor | 10 uH, 12 mohm winding resistance |
| Transformer leakage (primary) | 12 uH |
| Transformer primary winding R | 18 mohm |
| Transformer secondary winding R | 8 mohm |
| Gate drive | NCD57001, +20V/-5V, Rg = 3.3 ohm (turn-on), 6.8 ohm || diode (turn-off) |
| Dead time | Primary: 142.8 ns, Secondary: 166.6 ns |
| PWM clock | 84 MHz |
| Core losses | Not modeled (estimate ~ equal to winding losses) |

### Thermal Management

#### SiC Module vs Discrete Advantages
- Module (NXH020F120MNF1PTG) switching losses: EON = 0.24 mJ, EOFF = 0.24 mJ
- Discrete (NTH4L020N120SC1) switching losses: EON = 0.49 mJ, EOFF = 0.39 mJ
- Module has ~45% lower total switching losses due to reduced package parasitics
- Module junction-to-heatsink: 0.80 C/W (with 5 kV isolation included)
- Discrete junction-to-heatsink: min 1 C/W, typically 3.3 C/W with TIM + isolation

#### Thermal Design Approach
- Fan-based active cooling on SiC modules
- Ambient temperature assumption: 30 C max (no housing)
- PFC stage: 3 SiC PIMs at ~80 W/module = 240 W total
  - Heatsink: Zth = 0.2 C/W -> 16 C rise per module
  - Fan also cools PFC chokes (~27 W/choke, <30 C rise with 3 m3/s airflow)
- DAB DC-DC stage: primary PIMs = 300 W combined, secondary PIMs = 150 W
  - Heatsink: Zth = 0.15 C/W
  - Primary cooling: peaks at 75 C, secondary: peaks at 52.5 C
  - Separate fan for transformer + resonant inductor (designed for 70 C rise without cooling)

#### Fan Control System
- PWM-to-voltage converter regulates fan RPM
- Input: NTC temperature from SiC PIM module (integrated sensor)
- Automatic speed control: low noise at light load, full speed at high power
- Closed-loop control with compensator design for stable regulation

### Physical Dimensions
- Combined PFC + DC-DC modules: 380 x 345 x 200-270 mm (L x W x H)
- PFC stage stacked on top of DC-DC stage
- Multiple 25 kW units can be stacked for higher power (ultra-fast charger)
