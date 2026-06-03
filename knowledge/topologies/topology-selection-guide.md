---
description: Select the optimal power converter topology based on electrical requirements
---

# Topology Selector

Given converter requirements, recommend the best topology with justification.

## Decision Process

### Step 1: Isolation Required?

**Yes** → Go to Isolated Topologies
**No** → Go to Non-Isolated Topologies

### Step 2A: Non-Isolated Topology Selection

| Condition | Recommended | Notes |
|---|---|---|
| Vout < Vin (always) | **Buck** | Simplest, highest efficiency, lowest cost |
| Vout > Vin (always) | **Boost** | Simple, but has RHPZ limiting bandwidth |
| Vout can be > or < Vin, negative OK | **Buck-Boost** | Inverts polarity. Simple but high switch stress |
| Vout can be > or < Vin, must be positive | **SEPIC** | Non-inverting. Needs coupling capacitor. Has RHPZ |
| Vout can be > or < Vin, negative, low ripple | **Cuk** | Capacitive energy transfer, continuous I/O currents |
| Vout can be > or < Vin, positive, input-side switch | **Zeta** | Like SEPIC but switch on input side |
| Wide Vin range, bidirectional needed | **Four-Switch Buck-Boost** | Two cascaded cells. Most flexible non-isolated |

### Step 2B: Isolated Topology Selection

| Power Level | Recommended | Notes |
|---|---|---|
| < 5W | **Flyback** | Simplest isolated. Single magnetic. Low cost |
| 5-75W | **Flyback** | Still competitive. Watch leakage inductance losses |
| 30-300W | **Active Clamp Flyback (ACF)** | ZVS; 2–4% better than RCD flyback; read `active-clamp-flyback.md` |
| 50-150W | **Active Clamp Forward** or **Two-Switch Flyback** | ACF has better efficiency. 2SW-Flyback clamps voltage |
| 75-200W | **Single/Two-Switch Forward** | Needs output inductor. Better transformer utilization |
| 100-500W | **Half-Bridge** | Uses split caps. Transformer sees Vin/2. Cost-effective |
| 200-500W | **Push-Pull** | Good for low-Vin (battery, solar). Flux balance critical |
| 500W+ | **Full-Bridge** | Full Vin across transformer. 4 FETs |
| 500W+, high efficiency needed | **Phase-Shifted Full-Bridge** | ZVS operation. Lowest switching losses |

### Step 3: Validate Selection

Check these conditions:
1. **Duty cycle is reasonable** (0.2 < D < 0.8 for most topologies)
2. **Switch voltage stress** is within available FET ratings
3. **Turns ratio** (for isolated) doesn't require extreme values (1:1 to 1:10 typical)
4. **RHPZ frequency** (Boost, Buck-Boost, SEPIC, Cuk, Flyback) is > 5x crossover frequency

## Topology Selection Rules (from Maniktala)

### Why only three basic topologies?
Every inductor-based converter is fundamentally a Buck, Boost, or Buck-Boost. All isolated topologies are derived from these three by adding a transformer.

| Basic | Isolated Derivatives |
|---|---|
| Buck | Forward, Push-Pull, Half-Bridge, Full-Bridge |
| Boost | (Current-fed topologies) |
| Buck-Boost | Flyback |

### Key Trade-offs

**Flyback vs Forward:**
- Flyback: fewer components, simpler, but stores energy in transformer (air gap needed), high leakage
- Forward: better transformer utilization, needs output inductor + freewheeling diode, better for higher power

**RCD Flyback vs Active Clamp Flyback:**
- RCD flyback: simple, low cost, but leakage energy dissipated in clamp → tops out at ~85–88% efficiency
- ACF: clamp FET + Cclamp recirculate leakage energy; ZVS eliminates turn-on switching losses → 90–95% achievable
- ACF adds: 1 FET (Qa), 1 gate driver channel, 1 clamp cap, complementary gate timing
- ACF constraint: Lm must be ZVS-sized (0.4–0.6× standard r=0.4 Lm); increased magnetizing current ripple
- Use ACF when: η ≥ 90% target at 30–300W; acceptable to add complementary gate drive complexity
- Read `topologies/active-clamp-flyback.md` for complete design procedure

**Single-Switch Forward vs Two-Switch Forward:**
- Single-switch: needs reset winding, D < 0.5 (with 1:1 reset), higher switch voltage stress = Vin + Vin*N_reset/N_pri
- Two-switch: switch voltage clamped to Vin, no reset winding needed, D still < 0.5

**Active Clamp Forward:**
- D can exceed 0.5 (not limited like conventional forward)
- Magnetizing energy recycled (not dissipated)
- Better efficiency than single/two-switch forward

**Half-Bridge vs Full-Bridge:**
- Half-bridge: 2 FETs, transformer sees Vin/2, switch voltage = Vin
- Full-bridge: 4 FETs, transformer sees Vin, switch voltage = Vin, double power capability

**Phase-Shifted Full-Bridge:**
- ZVS reduces switching losses dramatically at high frequency
- Needs sufficient magnetizing/leakage inductance for ZVS
- Best for high-power, high-frequency, high-efficiency applications

### Voltage/Current Stress Summary

**Non-Isolated:**
| Topology | Max Switch Voltage | Max Diode Voltage |
|---|---|---|
| Buck | Vin + Vf | Vin |
| Boost | Vout + Vf | Vout |
| Buck-Boost | Vin + Vout + Vf | Vin + Vout |
| SEPIC | Vin + Vout + Vf | Vin + Vout |
| Cuk | Vin - Vout + Vf | Vin - Vout |
| Zeta | Vin + Vout + Vf | Vin + Vout |

**Isolated:**
| Topology | Max Switch Voltage | Notes |
|---|---|---|
| Flyback (RCD clamp) | Vin + (Vout+Vf)*np/ns + Vspike | Vspike from leakage; use 800V FETs for offline |
| Active Clamp Flyback | Vbus / (1 - D) | **Clamped** — no leakage spike; 650V FETs usually sufficient |
| Two-Switch Flyback | (Vin + (Vout+Vf)*np/ns) / 2 | Body diodes clamp each FET to Vin |
| Active Clamp Forward | Vin / (1-D) | |
| Single-Switch Forward | Vin + Vin*(N_reset/N_pri) | |
| Two-Switch Forward | Vin | |
| Push-Pull | 2 * Vin | |
| Half-Bridge | Vin | |
| Full-Bridge | Vin | |
| Phase-Shifted Full-Bridge | Vin | |

**Active Clamp Flyback (ACF) — switch voltage detail:**
```
V_clamp = Vbus / (1 - D)       ← clamp cap voltage (= main FET Vds during off-time)
V_Q1_off = V_clamp              ← Q1 sees clamp voltage, NOT Vbus + VOR spike
V_Qa_off = V_clamp - Vbus       ← Qa off-state voltage = VOR = (Vout+Vf)*Np/Ns

Design for: V_Q1_rating >= 1.5 * V_clamp
            V_Qa_rating >= 1.5 * (V_clamp - Vbus)   (usually same FET as Q1)
```

## Output Format

When recommending a topology, provide:
1. **Recommended topology** and why
2. **Alternative topology** (second choice) and when it would be better
3. **Estimated duty cycle** at nominal Vin
4. **Key voltage/current stresses** on main switch
5. **Potential issues** to watch for (RHPZ, flux balance, leakage, etc.)

## Efficiency-Indexed Topology Selection (from Competitor Challenges — validated by simulation)

Based on 5 competitive benchmarks against TI, onsemi, and Power Integrations reference designs:

| Power | Target Eff | Recommended Topology |
|---|---|---|
| <30W | >85% | DCM Flyback + RCD clamp |
| <30W | >90% | QR Flyback (valley switching) |
| 30-75W | >85% | DCM Flyback + RCD clamp |
| 30-75W | >90% | Active Clamp Flyback (ACF) or QR + SR |
| 30-75W | >93% | ACF + Synchronous Rectification |
| 75-150W | >85% | Flyback CCM/DCM transition |
| 75-150W | >90% | Two-switch forward or ACF + SR |
| 75-150W | >93% | LLC half-bridge |
| 150-500W | >90% | LLC half-bridge |
| 150-500W | >95% | LLC half-bridge + SR |
| 500W+ | >93% | Interleaved LLC or Phase-Shifted Full-Bridge |

**Key findings:**
- Basic DCM flyback + RCD clamp tops out at ~85% regardless of power level
- RCD clamp is #1 efficiency killer (4-11W lost to leakage energy dissipation)
- ACF recovers leakage energy → +5-9% efficiency vs passive flyback
- Synchronous rectification adds +2-4% at any power level
- LLC matches industry benchmarks at 300W (92.8% without SR, ~95% with SR)

## Power Level Guidelines (from Basso Appendix 1C)

| Power Range | Recommended Topologies |
|---|---|
| 0-10W | Flyback (DCM), Buck (non-isolated) |
| 10-50W | Flyback (DCM or boundary), Buck |
| 50-100W | Flyback (CCM/DCM transition), Forward |
| 100-250W | Forward (single/two-switch), Half-bridge |
| 250-500W | Half-bridge, Full-bridge |
| 500W-1kW | Full-bridge, Phase-shifted full-bridge |
| 1kW+ | Phase-shifted full-bridge, LLC, interleaved |

## Operating Mode Selection (from Basso Ch7)

When choosing between CCM and DCM for a given topology:

### Choose DCM when:
- Power < 30W
- High output voltage / low output current (e.g., LED drivers, CRT supplies)
- Simplicity of control is important (first-order system)
- Using inexpensive slow diodes is necessary
- Quasi-resonant (valley switching) operation is desired

### Choose CCM when:
- Power > 60W
- Low output voltage / high output current (e.g., 5V/10A server supplies)
- Low output ripple is critical
- RMS currents must be minimized (thermal constraints)
- Fast diodes / synchronous rectification is available

### Choose Boundary (CrCM) when:
- 20-100W range
- Want benefits of both DCM and CCM
- ZCS on secondary diode (no reverse recovery)
- Variable frequency operation is acceptable

## Control Mode Selection (from Basso Ch3)

| Topology + Mode | Recommended Control | Compensator Type |
|---|---|---|
| Buck CCM | Current mode | Type 2 |
| Buck CCM | Voltage mode | Type 3 |
| Buck DCM | Either | Type 1 or 2 |
| Boost CCM | Current mode | Type 2 (watch RHPZ) |
| Boost DCM | Either | Type 1 or 2 |
| Buck-Boost/Flyback CCM | Current mode | Type 2 (watch RHPZ) |
| Buck-Boost/Flyback DCM | Either | Type 1 or 2 |
| Forward CCM | Current mode preferred | Type 2 |
| Forward CCM | Voltage mode | Type 3 |
| PFC Boost | Average current mode | Type 1 (slow outer loop) |

### Compensator Types:
- **Type 1**: Pure integrator. Zero phase boost. Used where plant phase lag < 45° at crossover.
- **Type 2**: Origin pole + one zero-pole pair. Boosts phase up to 90°. Most common.
- **Type 3**: Origin pole + two zero-pole pairs. Boosts phase up to 180°. Needed for voltage-mode CCM with LC resonance.

### k-Factor Method (from Basso Ch3):
Quick compensator design:
1. Measure/calculate open-loop gain at desired crossover frequency fc
2. Required gain G at fc = -|plant gain at fc| (to make total = 0 dB)
3. Required phase boost = desired phase margin - plant phase at fc - (-90° from integrator)
4. k = tan(boost/2 + 45°) for Type 2, k = tan(boost/4 + 45°) for Type 3
5. Place zero at fc/k, pole at fc*k (Type 2)
6. Place zeros at fc/k, poles at fc*k (Type 3)

## Topology Derivation Principles (from Erickson Ch6)

Sources: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed (2020), Chapter 6 (pp.177-225).

### Fundamental Circuit Manipulations

Erickson shows that all basic converter topologies are related through a small set of circuit manipulations applied to the buck converter. This provides deep insight into *why* each topology has its particular properties.

#### 1. Inversion of Source and Load (p.178-179)

Interchanging the power input and output ports of a buck converter yields a boost converter. The inductor volt-second balance equation V2 = D*V1 holds regardless of power flow direction. When power flows from port 2 to port 1, solving for V1 gives V1 = V2/D'. After accounting for the switch realization (which swaps D and D'), the result is the boost conversion ratio M(D) = 1/D'.

**Key insight**: The boost converter is literally a buck converter operated in reverse. This is why the boost inherits the buck's single inductor and simple LC filter structure, but with input/output properties reversed (pulsating input current in buck becomes pulsating output current in boost, and vice versa).

#### 2. Cascade Connection of Converters (pp.180-183)

Cascading a buck (M1 = D) followed by a boost (M2 = 1/D') yields M = D/D', the buck-boost conversion ratio. The intermediate LC filter can be simplified:

1. Remove intermediate capacitor C1 (not needed for the conversion ratio)
2. Combine the two series inductors into a single inductor L
3. The resulting noninverting buck-boost has M(D) = D/D'
4. Reversing inductor polarity during one subinterval gives the inverting buck-boost M(D) = -D/D', which also reduces the SPDT switch count to one

**The Cuk converter** is derived similarly: cascade a boost (converter 1) followed by a buck (converter 2). It inherits the nonpulsating input current of the boost and the nonpulsating output current of the buck. Its equivalent circuit contains a D':1 (boost) transformer followed by a 1:D (buck) transformer.

**The SEPIC** and its inverse are also two-inductor converters with noninverting buck-boost characteristics M(D) = D/D'.

#### 3. Rotation of the Three-Terminal Cell (pp.183-184)

The inductor-SPDT-switch network forms a three-terminal cell with terminals a, b, c. There are exactly three distinct ways to connect this cell between source and load:

| Connection | Result |
|---|---|
| a-A, b-B, c-C | **Buck** converter |
| a-C, b-A, c-B (source/load inversion) | **Boost** converter |
| a-B, b-C, c-A | **Buck-Boost** converter |

A dual three-terminal cell (capacitor + SPDT switch, with inductors in series at input and output) exists. Its three rotations yield: buck with LC input filter, boost with LC output filter, and the Cuk converter.

#### 4. Differential Connection of the Load (pp.184-188)

Connecting a load differentially across the outputs of two converters driven with complementary duty cycles (D and D') produces bipolar output voltage:

- Two buck converters differentially: V = (2D - 1)*Vg. This is the **H-bridge** (bridge inverter), used in servo amplifiers and single-phase inverters. The two output inductors combine into one.
- Three buck converters with 3-phase load: the **voltage-source inverter** (buck-derived 3-phase bridge)
- Three boost converters with 3-phase load: the **current-source inverter**

### The Complete Short List of Single-Inductor Converters (pp.188-192)

Erickson proves that there are exactly **8 single-inductor converters** (Fig 6.15, p.190):

| # | Converter | M(D) | Notes |
|---|---|---|---|
| 1 | Buck | D | Step-down, unipolar |
| 2 | Boost | 1/D' | Step-up, unipolar |
| 3 | Buck-Boost (inverting) | -D/D' | Inverting, unipolar negative |
| 4 | Noninverting Buck-Boost | D/D' | Two SPDT switches required |
| 5 | H-Bridge | 2D - 1 | Bipolar output, 4 switches |
| 6 | Watkins-Johnson | (2D-1)/D | Bipolar output, nonlinear M(D), 2-winding inductor reduces switches |
| 7 | Current-fed Bridge | 1/(2D-1) | Inverse of #5, ac-input to dc-output |
| 8 | Inverse of Watkins-Johnson | D/(2D-1) | Inverse of #6, ac-input to dc-output |

Two-inductor converters (Fig 6.16, p.192) include: Cuk, SEPIC, inverse-SEPIC, and converters with biquadratic M(D) such as the "Buck-squared" with M(D) = D^2 (useful for large step-down without transformer).

### Transformer Isolation Derivation (pp.192-217)

Every isolated topology is derived from a non-isolated parent by replacing the inductor with a transformer (which adds the magnetizing inductance LM in the model). The transformer turns ratio n provides an additional design degree of freedom.

**Critical rule**: The dc component of voltage applied to the magnetizing inductance must be zero (volt-second balance), or the transformer will saturate.

#### Buck-Derived Isolated Topologies

| Topology | Derivation | M(D) | Max D | Transformer Utilization | Flux Excitation |
|---|---|---|---|---|---|
| **Full-Bridge** | 4-switch bridge drives transformer, center-tapped secondary | nDVg | 0 < D < 1 | Excellent (bidirectional flux, full B-H loop) | Bipolar |
| **Half-Bridge** | 2 switches + 2 capacitors replace lower bridge leg | 0.5*n*D*Vg | 0 < D < 1 | Same as full-bridge, but primary sees Vin/2 | Bipolar |
| **Forward** | Single switch + reset winding (n1:n2) | n3/n1 * D * Vg | D < 1/(1+n2/n1); D < 0.5 for n1=n2 | Good (unipolar flux, but modern core-loss-limited designs are comparable) | Unipolar |
| **Two-Switch Forward** | Two switches + two clamp diodes, no reset winding | n*D*Vg | D < 0.5 | Same as forward, switch voltage clamped to Vin | Unipolar |
| **Push-Pull** | Center-tapped primary, alternating switches | nDVg | 0 < D < 1 | Full B-H loop, but center-tapped windings waste copper | Bipolar |

**Full-bridge**: Transformer saturation from small volt-second imbalances can be prevented by a series dc-blocking capacitor or by current-programmed control (p.198). At ~750W and above.

**Half-bridge**: Cannot use current-programmed control. Switch current is 2x that of full-bridge. Good for moderate power where low parts count matters (p.200).

**Forward**: Switch voltage stress = Vg*(1 + n1/n2). Magnetizing inductance operates in DCM. Transformer winding utilization is better than full/half-bridge (no center tap). At lower power levels than bridge topologies (p.201-206).

**Push-Pull**: Prone to transformer saturation from switch imbalances. Must use current-programmed control; duty-cycle-only control is not recommended. Good for low-Vin applications (battery, solar) since only one switch in series with source at any time (p.206-207).

#### Buck-Boost-Derived: Flyback (pp.208-212)

The flyback is derived by splitting the buck-boost inductor into two coupled windings. The magnetic device is a "two-winding inductor" (flyback transformer), not a true transformer -- current does not flow simultaneously in both windings.

M(D) = n * D/D'

**Equivalent circuit model** (p.211): Contains a 1:D buck-type dc transformer followed by a D':1 boost-type dc transformer, plus the physical turns ratio 1:n. This confirms the flyback inherits properties of both buck and boost.

**Practical notes**: 50-100W typical. Very low parts count. Multiple outputs need only one additional winding + diode + capacitor per output. Disadvantages: high transistor voltage stress (Vg + V/n + leakage ring), poor cross-regulation, unipolar core utilization.

#### Boost-Derived Isolated Topologies (pp.212-215)

Obtained by inverting source and load of buck-derived isolated converters:

- **Full-bridge boost**: All 4 transistors on during subinterval 1 (energy storage in L). Two transistors off during subinterval 2 (energy transfer through transformer). M(D) = n/D'. Transformer saturation during subinterval 1 is non-catastrophic because inductor L limits current.
- **Push-pull boost**: Two transistors, each blocking 2V/n. Used in high-voltage supplies and low-harmonic rectifier applications.

#### Isolated SEPIC and Cuk (pp.215-217)

Inductor L2 of the SEPIC or Cuk is replaced with a two-winding device that functions as both flyback transformer and conventional transformer simultaneously. During subinterval 1, magnetizing current flows through the primary. During subinterval 2, both magnetizing current and reflected input inductor current flow through the secondary. M(D) = nD/D' for both.

### Switch Utilization and Converter Comparison

Switch utilization U = Pout / (max_switch_voltage * max_switch_current) is a figure of merit (higher is better). Non-isolated converters at a single operating point have U = 1 for the buck at D = 1. For converters with wide input voltage range, U degrades because the switches must be rated for worst-case voltage and current.

**Design implication**: When selecting between topologies for a given application, evaluate the switch utilization at the actual operating range, not just at a single nominal point. Isolated converters with wide Vin range inherently have lower switch utilization than non-isolated converters at a fixed operating point.
