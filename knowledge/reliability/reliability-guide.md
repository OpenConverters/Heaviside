# Reliability Guide for Power Electronics

Consolidated from existing Heaviside knowledge files:
- Billings & Morey, *Switchmode Power Supply Handbook*, 3rd Ed. (thermal, components, protection)
- McLyman, *High Reliability Magnetic Devices: Design & Fabrication* (magnetics reliability)

This guide covers failure mechanisms, derating, lifetime estimation, and design-for-reliability practices for switchmode power supplies.

---

## 1. Failure Rate and Temperature

### 1.1 Arrhenius Relationship

Component failure rates follow the Arrhenius model: the rate of chemical degradation (and therefore failure) approximately doubles for every 10-15C increase in temperature.

From MIL-HDBK-217A:
- A transistor at Tj = 180C has 1/20 the life of one at 25C
- **General rule for complete SMPS: failure rate doubles for every 10-15C rise above 25C**
- A unit operating at 70C has approximately 10% of its MTBF at 25C

### 1.2 Practical Implications

- Temperature derating is the single most effective reliability improvement available to the designer
- Electrolytic capacitors and wound components typically reach their thermal limits before semiconductors do
- Proximity heating from adjacent components often exceeds a component's own internal dissipation
- The most critical thermal decision (cooling method) must be made BEFORE the electrical design begins

### 1.3 MTBF Estimation

For a system of N components, assuming independent exponential failure distributions:

```
lambda_system = sum(lambda_i)          [total failure rate]
MTBF_system = 1 / lambda_system        [mean time between failures]
```

where lambda_i is the failure rate of each component, obtained from MIL-HDBK-217 or manufacturer data, adjusted for operating stress and temperature.

**Rule of thumb:** Reducing the hottest component temperature by 10C roughly doubles the system MTBF.

---

## 2. Component Derating Guidelines

### 2.1 Derating Table

| Component | Parameter | Recommended Derating |
|---|---|---|
| MOSFET/BJT | Vds/Vce | Use <= 80% of rated voltage |
| MOSFET/BJT | Tj | Design for Tj <= 100-110C (rated 150-175C) |
| Electrolytic capacitor | Voltage | Use <= 80% of rated voltage |
| Electrolytic capacitor | Temperature | Operate >= 10C below rated max |
| Electrolytic capacitor | Ripple current | Use <= 80% of rated ripple |
| Diode | Vrrm | Use <= 80% of rated reverse voltage |
| Diode | If_avg | Use <= 70% of rated average current |
| Transformer/inductor | Core B | Operate at <= 70-80% of Bsat |
| Ceramic capacitor (Class II) | Voltage | Apply at <= 50% rated voltage (DC bias derating) |
| Film capacitor | Voltage | Use <= 80% of rated voltage |
| Resistor (power) | Power | Use <= 50-60% of rated power |
| Fuse | Current | Select rating >= 1.25x steady-state current |

### 2.2 Voltage Derating Rationale

- Voltage stress accelerates dielectric degradation and increases the probability of avalanche breakdown
- Electrolytic capacitors: voltage overstress causes electrolyte gas generation, seal failure, and eventual venting
- Ceramic capacitors (Class II): actual capacitance drops 50-80% at rated DC voltage; derating to 50% preserves usable capacitance and reduces stress
- MOSFETs: voltage spikes from parasitic inductance can exceed steady-state predictions by 20-50%; derating provides margin for these transients

### 2.3 Current Derating Rationale

- Current stress causes I^2*R heating in all resistive elements (traces, leads, bond wires, windings)
- Diode current derating accounts for non-uniform current distribution across the junction at high currents
- Inductor/transformer current derating prevents core saturation under transient overload

### 2.4 Altitude Derating

- Free-air convection cooling efficiency reduces ~20% at 10,000 ft (3,050 m)
- Forced-air fans deliver less air mass at altitude (lower air density)
- Creepage and clearance distances may need to increase at altitude (lower dielectric strength of air)
- Corona onset voltage decreases with altitude (relevant for HV magnetics)

---

## 3. Electrolytic Capacitor Lifetime

### 3.1 Arrhenius Lifetime Model

Electrolytic capacitor lifetime follows an Arrhenius relationship driven by internal temperature:

```
L = L0 * 2^((T0 - T_actual) / 10)
```

where:
- L0 = rated lifetime at maximum rated temperature T0 (from datasheet, typically 2000-10000 hours at 105C)
- T_actual = actual capacitor core temperature (C)
- Factor of 10 = temperature acceleration constant (degrees per lifetime doubling)

**Example:** A capacitor rated 5000 hours at 105C, operating at 75C:
```
L = 5000 * 2^((105 - 75) / 10) = 5000 * 2^3 = 40,000 hours (~4.6 years)
```

### 3.2 Ripple Current Correction

Internal heating from ripple current reduces effective lifetime. The corrected model:

```
L = L0 * 2^((T0 - T_ambient) / 10) * K_ripple
```

where:
```
K_ripple = sqrt(I_rated / I_actual)     [for I_actual <= I_rated]
K_ripple = 1                            [if ripple current is within rating]
```

Some manufacturers use:
```
delta_T_ripple = (I_ripple / I_rated)^2 * delta_T_max
T_actual = T_ambient + delta_T_ripple
```

and then apply T_actual in the standard Arrhenius formula.

### 3.3 Thermal Hazards for Electrolytics

- Maximum permitted internal temperature rise from ripple current: typically 5-10C
- Absolute case temperature limit (typical): 93C (85C rated + 8C ripple rise)
- Electrolytic capacitors become more lossy at high temperatures, creating a risk of thermal runaway
- Proximity heating from adjacent hot components (power resistors, diodes, MOSFETs) often exceeds the capacitor's own internal dissipation

### 3.4 Verification Procedure

1. Measure temperature rise from ripple current alone (isolate from other heat sources)
2. Confirm rise < manufacturer's limit (typically 5-10C)
3. Mount in final position; measure case temperature at max load, max ambient
4. Confirm case temperature < absolute maximum rating
5. Use polished aluminum foil as a radiation shield between hot components and electrolytic capacitors

### 3.5 Design Rules for Long Capacitor Life

- Place electrolytic capacitors in the coolest area of the board
- Never place capacitors directly above hot components in vertical convection flow
- Derate voltage to <= 80% of rated value
- Derate ripple current to <= 80% of rated value
- Operate at least 10C below maximum rated temperature
- Use parallel capacitors to share ripple current (reduces I^2*R heating per unit)
- Consider film capacitors in parallel to divert high-frequency ripple away from electrolytics

---

## 4. MOSFET Reliability

### 4.1 Safe Operating Area (SOA)

- MOSFETs have no secondary breakdown risk (positive temperature coefficient distributes current uniformly across the die)
- Must satisfy maximum dV/dt rating to prevent parasitic BJT latch-up (body diode reverse recovery can trigger the parasitic NPN)
- Snubber networks may be required to limit dV/dt

For bipolar transistors (BJTs):
- Secondary breakdown is a critical concern: current concentrates in a small area during simultaneous high V and high I
- Must ensure collector current reaches zero BEFORE collector voltage reaches Vceo
- Snubber networks are mandatory to shape the turn-off load line

### 4.2 Gate Oxide Lifetime

- Gate oxide breakdown is a wear-out mechanism driven by electric field stress across the oxide
- Maximum rated Vgs (typically +/-20V) includes margin; exceeding this even briefly can cause cumulative oxide damage
- Transient gate voltage spikes from parasitic inductance in the gate drive loop can exceed Vgs_max
- Design gate drive with clamping (back-to-back zeners or TVS at the gate) to prevent overshoot
- ESD damage during handling can create latent gate oxide defects that fail later under operating stress

### 4.3 Thermal Cycling

- Repeated heating and cooling causes mechanical stress at interfaces between materials with different coefficients of thermal expansion (CTE)
- Bond wire lift-off: wire bonds on the die flex with thermal cycling; typical rated for 20,000-100,000 power cycles depending on delta_Tj
- Die attach fatigue: solder or epoxy between die and leadframe degrades, increasing Rth_jc over time
- Package cracking: encapsulant stress from CTE mismatch between die, leadframe, and mold compound
- **Power cycling lifetime** (Coffin-Manson relationship):

```
N_f = A * (delta_Tj)^(-n)
```

where delta_Tj is the junction temperature swing per cycle, A and n are device-specific constants (n typically 4-6 for wire bond failure).

**Design implication:** Minimizing junction temperature swing is more important than minimizing absolute junction temperature for thermal cycling reliability.

### 4.4 Avalanche Energy

- Avalanche occurs when drain voltage exceeds the breakdown voltage (Vdss) and the body diode enters avalanche
- Single-pulse avalanche energy (PEAS) and repetitive avalanche energy (EAR) are rated in datasheets
- Unclamped inductive switching (UIS) generates avalanche events; design snubbers/clamps to prevent repetitive avalanche stress
- Repetitive avalanche causes cumulative damage to the body diode junction

---

## 5. Magnetic Component Reliability

Source: McLyman, *High Reliability Magnetic Devices: Design & Fabrication* (2002).

### 5.1 Common Failure Modes

1. **Turn-to-turn shorts:** Caused by crossed wires, insufficient interlayer insulation, thermal cycling fatigue, or corona erosion. Prevention: strict winding process control, adequate insulation margins.

2. **Winding-to-core shorts:** Caused by inadequate clearance between winding and core edges, especially at corners of rectangular bobbins. Prevention: margin tape, adequate creepage distance, rounded core edges.

3. **Lead breakage:** Caused by insufficient strain relief, thermal cycling, or vibration. Prevention: minimum 3-turn strain relief at every lead exit, flexible lead wire for external connections.

4. **Saturation drift:** In push-pull topologies, small volt-second imbalances cause DC flux walking toward saturation. Prevention: current-mode control, or add a small air gap.

5. **Thermal degradation:** Insulation breakdown from sustained operation above rated temperature. Prevention: design for worst-case ambient + worst-case internal temperature rise. Use insulation materials rated for the actual operating temperature class.

6. **Corona failure (HV only):** Progressive insulation erosion from partial discharge in air voids. Prevention: complete vacuum impregnation, spherical electrode shapes, adequate creepage distances.

### 5.2 Fabrication Process Control

High-reliability magnetics require documented, step-by-step fabrication procedures with in-process inspection at every stage:

- **Winding tension:** Must be controlled per wire gauge. Excess tension causes wire stretching (diameter reduction, resistance increase) or breakage. Insufficient tension causes loose windings with poor thermal coupling and vibration-induced failures.
- **Crossed wires:** Strictly prohibited. Crossed wires create localized pressure points that damage insulation under thermal cycling, leading to turn-to-turn shorts. Each layer must be inspected before applying interlayer insulation.
- **Soldering:** Joints must be continuous, smooth, shiny (no cold joints, no porosity). No solder wicking under wire insulation (creates rigid stress-concentrating points). Minimum 3-turn strain relief before every solder joint.
- **Impregnation:** Two-step vacuum impregnation + embedment removes trapped air, provides moisture barrier, and improves thermal coupling. Cure must be verified with proof-of-cure samples.

### 5.3 Testing and Quality Assurance

**Electrical tests:**
- Turns ratio, primary inductance, leakage inductance
- DC resistance of each winding
- Hipot (dielectric withstand): 2x rated voltage + 1000V for 60 seconds
- Insulation resistance: minimum 100 Mohm at 500V DC
- Self-resonant frequency

**Environmental tests (qualification):**
- Thermal shock: -55C to +125C, 100 cycles minimum
- Humidity: 240 hours at 95% RH, 65C
- Vibration: random vibration per MIL-STD-810
- Altitude: operation at reduced pressure (corona test for HV units)

---

## 6. FMEA Methodology

### 6.1 Purpose

Failure Mode and Effects Analysis (FMEA) is a systematic technique for identifying potential failure modes in a design, assessing their consequences, and prioritizing corrective actions. For power converters, FMEA should be performed at the schematic design stage, before layout.

### 6.2 FMEA Table Structure

| Column | Description |
|---|---|
| Component | Part reference (e.g., Q1, C5, T1) |
| Function | What the component does in the circuit |
| Failure Mode | How the component can fail (open, short, drift, intermittent) |
| Failure Effect (Local) | Immediate effect on the surrounding subcircuit |
| Failure Effect (System) | Effect on the overall power supply output and safety |
| Severity (S) | 1-10 scale (10 = catastrophic: fire, shock hazard, damage to load) |
| Probability (P) | 1-10 scale (10 = very likely given the stress conditions) |
| Detection (D) | 1-10 scale (10 = undetectable by any protection circuit) |
| RPN | Risk Priority Number = S x P x D (higher = more urgent) |
| Recommended Action | Design change, added protection, or test to reduce RPN |

### 6.3 Procedure

1. List every component in the power stage, control loop, and protection circuits
2. For each component, identify all credible failure modes (open, short, parametric drift)
3. Trace the effect of each failure through the circuit to the output
4. Score Severity, Probability, and Detection
5. Calculate RPN = S x P x D
6. Address all items with RPN > 100 (or all items with Severity >= 8, regardless of RPN)
7. After design changes, rescore to verify RPN reduction

### 6.4 Common Component Failure Modes for FMEA

| Component | Typical Failure Modes | Notes |
|---|---|---|
| MOSFET | Short (drain-source), open (gate oxide rupture), Rds_on drift | Short D-S is the most common; causes shoot-through in half-bridge |
| Electrolytic capacitor | Open (dry-out), ESR increase, reduced capacitance | Gradual wear-out; ESR increase causes ripple and thermal runaway |
| Ceramic capacitor | Short (crack), open (flex crack on PCB) | Cracking from mechanical stress is common; shorts can cause fires |
| Diode | Short, open, increased Vf | Short is most common in rectifiers under surge |
| Transformer | Turn-to-turn short, open winding, saturation | Shorts cause localized heating; can lead to fire |
| Inductor | Saturation (effective open), shorted turns, open | Saturation causes current runaway |
| Resistor | Open, drift high | Open is dominant; rarely shorts |
| IC (controller) | Latch-up, output stuck high/low | Must not defeat both regulation and protection |
| Optocoupler | CTR degradation, open | CTR drops with age; can lose regulation |
| Solder joint | Open (crack), intermittent | Thermal cycling and vibration are primary drivers |
| PCB trace | Open (crack), short (contamination) | Current density and thermal stress cause failures |

---

## 7. Design for Reliability Checklist

### 7.1 Thermal

- [ ] All component temperatures measured at maximum load, maximum ambient, thermal equilibrium
- [ ] Electrolytic capacitors placed in coolest area of board; not in convection path of hot components
- [ ] Junction temperatures derated to <= 100-110C (MOSFETs, diodes)
- [ ] Heatsink thermal resistance verified (calculation + measurement)
- [ ] Radiation shields used between hot components and temperature-sensitive parts
- [ ] Altitude derating applied if operating above 5,000 ft

### 7.2 Electrical Stress

- [ ] All voltage ratings derated per Section 2 table
- [ ] All current ratings derated per Section 2 table
- [ ] Snubber circuits sized to limit dV/dt within device ratings
- [ ] Gate drive clamped to prevent Vgs overshoot
- [ ] Transformer/inductor operating flux density <= 70-80% of Bsat at worst-case temperature
- [ ] Electrolytic capacitor ripple current <= 80% of rated value (adjusted for frequency and temperature)

### 7.3 Protection

- [ ] Overcurrent protection present (pulse-by-pulse current limiting at minimum)
- [ ] Overvoltage protection present and independent of main control loop
- [ ] Single component failure cannot defeat both regulation and OVP simultaneously
- [ ] Soft-start circuit prevents inrush stress on capacitors and transformer
- [ ] Inrush current limiting present (NTC, active circuit, or start resistor with bypass)
- [ ] Thermal shutdown present with hysteresis (10-20C)
- [ ] UVLO prevents operation with ill-defined drive waveforms

### 7.4 Magnetics

- [ ] No crossed wires in layer-wound coils
- [ ] Interlayer insulation applied with >= 50% overlap
- [ ] Strain relief (minimum 3 turns) on all lead exits
- [ ] Hipot test passed: 2x rated voltage + 1000V for 60 seconds
- [ ] Insulation resistance >= 100 Mohm at 500V DC
- [ ] Vacuum impregnation performed (for high-reliability applications)

### 7.5 System Level

- [ ] FMEA completed; all items with RPN > 100 addressed
- [ ] Worst-case analysis performed at Vin_min and Vin_max, full load and no load
- [ ] Prototype thermal survey completed
- [ ] Input surge protection (MOV + TVS) rated for installation environment
- [ ] Fuse/circuit breaker coordination verified (fuse I^2t < SCR I^2t if crowbar used)

---

## 8. Common Failure Modes in Power Supplies

### 8.1 Top Failure Modes and Root Causes

| Rank | Failure Mode | Root Cause | Prevention |
|---|---|---|---|
| 1 | Electrolytic capacitor dry-out | Sustained high temperature; electrolyte evaporation through seal | Derate temperature and ripple; place in cool zone |
| 2 | Solder joint cracking | Thermal cycling fatigue; CTE mismatch | Use compliant leads; minimize temperature swings; proper solder process |
| 3 | MOSFET drain-source short | Voltage overshoot exceeding Vdss; repetitive avalanche | Derate voltage; add snubber/clamp; verify with oscilloscope at worst case |
| 4 | Transformer turn-to-turn short | Insulation degradation from overtemperature or corona | Control winding process; derate temperature; vacuum impregnate |
| 5 | Diode short from surge | Reverse recovery stress; voltage spike exceeding Vrrm | Use ultrafast or Schottky diodes; add snubber; derate Vrrm |
| 6 | IC latch-up or failure | ESD, supply voltage transients, thermal stress | Add decoupling; ESD protection; keep Tj within limits |
| 7 | Optocoupler CTR degradation | LED degradation with age and temperature | Derate LED current; design loop gain to tolerate 50% CTR reduction |
| 8 | Inductor saturation | Overcurrent transient; core permeability drop at high temperature | Design for worst-case current + margin; use soft-saturation core materials |
| 9 | PCB trace burnout | Insufficient trace width for current; poor thermal vias | Follow IPC-2152 for trace sizing; use 2 oz copper for power paths |
| 10 | Fan failure (forced-air systems) | Bearing wear-out; dust accumulation | Use ball-bearing fans; add fan-fail detection with thermal shutdown |

### 8.2 Failure Mode Categories by Mechanism

**Wear-out failures (time/temperature dependent):**
- Electrolytic capacitor dry-out
- Optocoupler CTR degradation
- Fan bearing wear
- Solder joint fatigue
- Insulation aging in magnetics

**Overstress failures (event-driven):**
- MOSFET/diode breakdown from voltage spikes
- Transformer saturation from transient overcurrent
- IC latch-up from ESD or supply transients
- PCB trace fusing from overcurrent

**Design margin failures (latent):**
- Inadequate snubbing revealed only at certain load/temperature combinations
- Thermal runaway in electrolytic capacitors at end of life (ESR increases with age, causing more heating, which accelerates aging)
- Magnetic saturation at high temperature (Bsat decreases with temperature for ferrites, ~0.3%/C)

### 8.3 Critical Design Rule

From the protection circuits knowledge: **The protection loop must be completely independent of the main control loop.** A single component failure must not defeat both voltage regulation and overvoltage protection. Never use the same IC for both functions.

---

## Comprehensive Reliability Engineering (from Chung, Wang, Blaabjerg, Pecht -- IET 2015)

Source: *Reliability of Power Electronic Converter Systems*, IET Power and Energy Series 80 (2015). Editors: H.S.H. Chung, H. Wang, F. Blaabjerg, M. Pecht. This is the definitive multi-author reference on reliability engineering for power electronics, covering physics-of-failure methodology, component-level failure mechanisms, lifetime models, mission profile analysis, and practical qualification testing.

---

### 9. Physics-of-Failure vs Parts-Count Approach

#### 9.1 The Paradigm Shift

Traditional reliability prediction (MIL-HDBK-217, Telcordia) uses constant failure rates (exponential distribution) and parts-count methods. This assumes failures are random and independent -- the "flat" portion of the bathtub curve. However, power electronic components are dominated by **wear-out** mechanisms (bond wire fatigue, solder fatigue, electrolyte evaporation, capacitor dielectric degradation) that produce **increasing** failure rates over time.

The Physics-of-Failure (PoF) approach replaces statistical curve-fitting with root-cause understanding:

1. **Identify failure mechanisms** specific to each component under actual operating stresses
2. **Model the degradation physics** (thermal cycling fatigue, electrochemical degradation, dielectric breakdown)
3. **Predict lifetime** from the actual mission profile, not generic tables
4. **Design to prevent** the dominant failure mechanisms, not just achieve an arbitrary MTBF number

**Key insight:** A constant failure rate (MTBF) can be dangerously misleading. An instantaneous failure rate of 10 FIT does not imply a lifetime of 11,415 years -- the device can fail much earlier due to wear-out mechanisms that accelerate with time. At t = MTBF for an exponential distribution, 63% of devices have already failed.

#### 9.2 Load-Strength Interference Model

A component fails when the applied load L (voltage, thermal cycling, current) exceeds the design strength S (dielectric breakdown voltage, fatigue limit, melting point). Both L and S are distributed quantities. Moreover, strength degrades with time (wear-out), so the overlap between load and strength distributions increases over the component's life. Failure can be reduced by:
- Increasing strength (higher design margin, better materials)
- Reducing load (derating, active thermal control, load management)

#### 9.3 Critical Stressors by Component Type (Focus Points Matrix)

| Component | Temperature Swing dT | Avg Temperature T | Humidity | Voltage | Vibration |
|---|---|---|---|---|---|
| Semiconductor die | HIGH | HIGH | -- | moderate | -- |
| Die solder joint | HIGH | HIGH | -- | -- | -- |
| Bond wires | HIGH | HIGH | -- | -- | moderate |
| Capacitors | moderate | HIGH | HIGH (film) | HIGH | HIGH (MLCC) |
| Inductors | moderate | moderate | -- | -- | moderate |
| Solder joints (PCB) | moderate | moderate | moderate | -- | HIGH |
| MLCC | moderate | moderate | moderate | HIGH | HIGH |
| IC/PCB/Connectors | low | moderate | moderate | moderate | moderate |

**Most important stressors overall:** Temperature cycling and steady-state temperature together account for ~55% of field failures. Humidity accounts for ~20%, vibration/shock ~20%, and contamination ~6%.

#### 9.4 Design for Reliability (DFR) Process

The systematic DFR procedure for power electronic converters:

1. **Mission profile definition** -- characterize all environmental and electrical loads over the intended product lifetime (temperature profiles, load cycling, humidity, vibration)
2. **Component selection and stress analysis** -- calculate electrical and thermal stresses on every component under worst-case mission profile conditions
3. **Reliability prediction** -- use PoF-based lifetime models for wear-out components (capacitors, power modules) and handbook failure rates for random-failure components
4. **Design margin (robustness) analysis** -- verify sufficient margin between operating stress and component capability
5. **Accelerated testing** -- validate predictions via HALT, CALT, or power cycling tests
6. **Production validation** -- HASS/ORT to catch manufacturing-induced latent defects

**System-level reliability methods:**

| Method | Best For | Limitation |
|---|---|---|
| Reliability Block Diagram (RBD) | Non-repairable series/parallel systems | Does not handle dependencies between components |
| Fault Tree Analysis (FTA) | Identifying all possible failure causes | Dependencies not well treated |
| Markov Analysis (MA) | Repairable systems with redundancy | State explosion with many components; assumes constant failure rates |

#### 9.5 Typical Lifetime Targets by Application

| Application | Design Lifetime Target |
|---|---|
| Aircraft | 24 years (100,000 flight hours) |
| Automotive | 15 years (10,000 operating hours, 300,000 km) |
| Industry motor drives | 5-20 years (60,000 operating hours) |
| Railway traction | 20-30 years (73,000-110,000 hours) |
| Wind turbines | 20 years (175,000 hours) |
| Photovoltaic inverters | 5-20 years (90,000-175,000 hours) |

**Reality check:** Power electronic converters are usually one of the weakest links limiting system lifetime. Field data: frequency converters caused 13% of failures and 18.4% of downtime in 350 onshore wind turbines. PV inverters were responsible for 37% of unscheduled maintenance and 59% of maintenance cost in a 3.5-MW PV plant over 5 years.

---

### 10. FMMEA Methodology (Failure Modes, Mechanisms, and Effects Analysis)

#### 10.1 FMMEA vs Traditional FMEA

Traditional FMEA identifies failure modes and effects but does not require understanding the physics behind the failure. FMMEA extends FMEA by:
- Identifying the **failure mechanism** (the physical, chemical, or electrical process) for each failure mode
- Linking mechanisms to **specific stressors** from the life-cycle profile
- Selecting **physics-based failure models** for each mechanism
- Prioritizing mechanisms by Risk Priority Number (RPN = Severity x Occurrence x Detection)

#### 10.2 FMMEA Procedure

1. Define system and identify elements and functions
2. Identify potential failure modes for each element
3. Identify potential failure causes
4. Identify potential **failure mechanisms** (the physical root cause)
5. Identify life-cycle profile (all environmental and operational loads)
6. Identify failure models for each mechanism
7. Prioritize failure mechanisms by RPN
8. Document the process

#### 10.3 Failure Precursors for Power Electronics

Measurable parameters that indicate impending failure (for condition monitoring):

| Subsystem | Failure Precursor Parameters |
|---|---|
| Switching power supply | DC output level, ripple, duty cycle, efficiency, feedback voltage, leakage current, RF noise |
| Electrolytic capacitors | Leakage current, dissipation factor, RF noise |
| Ceramic capacitors | Leakage current, dissipation factor, RF noise |
| MOSFETs/FETs | Gate leakage current, drain-source leakage current |
| Diodes | Reverse leakage current, forward voltage drop, thermal resistance |
| IGBTs | On-state voltage Vce,on (increases with bond wire degradation and solder fatigue) |
| Cables/connectors | Impedance changes, physical damage |

#### 10.4 Key Failure Models

**Arrhenius model** (temperature-accelerated chemical degradation):
```
t_fail = A * exp(Ea / (k*T))
```
where Ea = activation energy (eV), k = Boltzmann constant (8.62e-5 eV/K), T = absolute temperature (K). Used for electrolyte evaporation, oxide growth, insulation aging.

**Coffin-Manson model** (thermal cycling fatigue):
```
Nf = C * (dT)^(-n)
```
where dT = temperature swing, C and n are material constants. Used for solder joint fatigue, bond wire fatigue. Typical n = 4-6 for bond wires, 1.9-2.0 for solder.

**Modified Coffin-Manson (Norris-Landzberg) model** (solder fatigue with frequency and Tmax effects):
```
Nf = C * f^(-a) * dT^(-b) * exp(Ea / (k*Tmax))
```
where f = cycling frequency, a ~ 1/3, b ~ 1.9-2.0, Tmax = maximum temperature in the cycle. This is the most widely used model for solder interconnection fatigue.

**Time-Dependent Dielectric Breakdown (TDDB)** for MOSFET gate oxide:
```
t_fail = A * exp(Ea/(k*T)) * exp(-gamma * E_field)     [E-model]
t_fail = A * exp(Ea/(k*T)) * exp(-gamma/E_field) / E^2  [1/E-model]
```
where E_field = electric field across the oxide. TDDB is cumulative -- even brief gate overvoltage events contribute to oxide degradation.

**Energy-based solder fatigue model** (Morrow's law):
```
Nf = Wcrit * (dW_hys)^(-n)
```
where dW_hys = inelastic strain energy density per cycle (area enclosed by stress-strain hysteresis loop), Wcrit and n are material/geometry constants. More accurate than Coffin-Manson for complex loading profiles.

---

### 11. DC-Link Capacitor Reliability (CRITICAL)

#### 11.1 Capacitor Type Comparison for DC-Links

| Property | Al-Electrolytic (Al-Cap) | Metallized Film (MPPF-Cap) | MLCC |
|---|---|---|---|
| Capacitance | +++ (highest) | ++ | + |
| Voltage rating | ++ | +++ (highest) | + |
| Ripple current capability | + (lowest) | +++ | +++ |
| ESR | + (highest) | +++ (lowest) | +++ |
| Frequency range | + (lowest) | ++ | +++ (highest) |
| Temperature range | ++ (up to 105C typ) | + (up to 85-105C) | +++ (up to 200C) |
| Energy density | +++ (highest) | + | ++ |
| Cost per joule | +++ (cheapest) | ++ | + (most expensive) |
| Reliability | + (wear-out limited) | +++ | +++ (but catastrophic short failure) |
| Self-healing | Moderate | Good | None |
| Dominant failure mode | Wear-out (ESR rise) | Open circuit | Short circuit |

#### 11.2 Failure Mechanisms by Capacitor Type

**Aluminum Electrolytic Capacitors (Al-Caps):**
- **Electrolyte evaporation** (dominant wear-out for small/snap-in types): ESR increases, capacitance drops. Driven by temperature and ripple current heating. End-of-life criteria: ESR > 2x initial, or C < 80% initial.
- **Electrochemical degradation of oxide layer** (dominant for large screw-terminal types): leakage current increases. Driven by voltage stress.
- **Dielectric breakdown** of oxide layer (catastrophic): caused by voltage overstress, reverse voltage, or severe overtemperature.
- **Terminal disconnection**: caused by vibration stress.

**Metallized Polypropylene Film Capacitors (MPPF-Caps):**
- **Self-healing dielectric breakdown**: local weak spots clear via vaporization of thin metallization (~100nm). Each clearing event causes negligible capacitance loss; accumulated clearings gradually reduce capacitance. End-of-life: C < 95% initial.
- **Moisture corrosion of metallized electrodes**: atmospheric moisture ingress corrodes the thin metal layer, increasing ESR and eventually causing open circuit. This is the dominant field failure mechanism. **Humidity is a critical stressor for film capacitors** -- testing at 85C/85% RH showed catastrophic acceleration (100,000-hour rated life reduced to <2,000 hours under humidity).
- **Connection instability**: heat contraction of dielectric film under high ripple current can loosen internal connections.

**Multi-Layer Ceramic Capacitors (MLCCs):**
- **Flex cracking**: mechanical stress from PCB bending cracks the ceramic body, creating a short circuit. This is the most common field failure cause. Prevention: use flexible termination MLCCs, place away from board flex zones, limit PCB panel size during reflow.
- **Dielectric breakdown / insulation degradation**: oxide vacancy migration under DC bias causes progressive decrease in insulation resistance. Under high voltage and temperature, leads to avalanche breakdown (sudden) or thermal runaway (gradual). Modern thin-dielectric MLCCs can wear out within 10 years due to the amplifying effect of many layers.
- **Short circuit is the dominant MLCC failure mode** -- no self-healing capability. A shorted MLCC can cause fire or cascading damage to the converter.

#### 11.3 Capacitor Lifetime Models

**General lifetime model (voltage + temperature acceleration):**
```
L = L0 * (V/V0)^(-n) * exp((Ea/kB) * (1/T - 1/T0))
```
where:
- L0 = rated lifetime at test conditions (V0, T0)
- V, V0 = operating and rated voltage
- T, T0 = operating and rated temperature (Kelvin)
- n = voltage stress exponent
- Ea = activation energy (eV)
- kB = Boltzmann constant (8.62e-5 eV/K)

**Simplified model for Al-Caps and film capacitors:**
```
L = L0 * (V/V0)^(-n) * 2^((T0-T)/10)
```
This is a special case of the general model when Ea = 0.94 eV and temperatures are near 125C (398K).

**Parameter values by capacitor type:**

| Capacitor Type | Voltage Exponent n | Activation Energy Ea | Notes |
|---|---|---|---|
| Al-Electrolytic | 3-5 | 0.94 eV (typical) | Voltage dependence may be linear rather than power-law at low stress |
| MPPF Film | 7-9.4 | 0.94 eV (typical) | Very sensitive to voltage; humidity not captured by standard model |
| MLCC (high-K ceramic) | 1.5-2.5 | 1.19-1.5 eV | n increases as dielectric layers get thinner; model parameters vary significantly |

**End-of-life criteria for condition monitoring:**

| Type | Capacitance | ESR | Other |
|---|---|---|---|
| Al-Electrolytic | C drops > 20% | ESR rises > 2x | -- |
| MPPF Film | C drops > 5% | Dissipation factor > 3x | -- |
| MLCC | C drops > 10% | Dissipation factor > 2x | Insulation resistance < 10 MOhm |

#### 11.4 Reliability-Oriented DC-Link Design

**Design procedure:**

1. **System-level definition**: converter specs, lifetime target, topology, environmental conditions (temperature profile, humidity)
2. **Ripple current stress analysis**: accurately calculate the ripple current spectrum flowing through DC-link capacitors -- this is crucial for both capacitor selection and lifetime prediction
3. **Capacitor type selection**: match capacitor characteristics to application needs (voltage, capacitance, ripple current, temperature, cost, volume, lifetime)
4. **Electrical analysis**: design capacitor bank with low parasitic inductance; account for parameter variation with temperature, frequency, and aging
5. **Thermal analysis**: calculate hot-spot temperature from ambient temperature + self-heating from ripple current + proximity heating from nearby components
6. **Lifetime prediction**: apply lifetime model with calculated stresses
7. **Robustness optimization**: verify adequate margin; consider alternative DC-link topologies

**DC-link design options to improve reliability:**
- **Hybrid bank** (Al-Cap + MPPF-Cap in parallel): film cap handles high-frequency ripple, reducing heating in electrolytics
- **Synchronized converter control**: reduce ripple current flowing through DC-link by coordinating input and output converter switching
- **Active ripple reduction**: parallel or series active circuits to reduce capacitor stress
- **Replacement by active energy buffer**: use switched-capacitor circuits with >90% energy buffering ratio to replace bulk electrolytics

#### 11.5 Condition Monitoring Methods

**Online monitoring (preferred for critical applications):**
- ESR estimation from capacitor voltage and current: ESR = Vc_rms / Ic_rms (in the ohmic frequency region f1 < f < f2)
- ESR from power dissipation: ESR = Pc / Ic_rms^2 (avoids band-pass filters)
- Capacitance estimation: C = integral(ic dt) / delta_Vc (from ripple current and voltage ripple)
- Use existing feedback signals in the control loop to avoid additional sensor hardware

**Offline monitoring (sufficient for most applications since degradation is slow):**
- Measure capacitance and ESR during scheduled maintenance or start-up sequences
- Track insulation resistance for MLCCs

---

### 12. Power Module Reliability

#### 12.1 Power Module Structure and Failure Mechanisms

A standard power module is a multi-layer assembly:
```
Bond wires --> Die (Si/SiC) --> Die attach (solder or sinter) --> DBC substrate (Cu-ceramic-Cu)
--> Substrate solder --> Baseplate (Cu or AlSiC) --> Thermal grease --> Heatsink
```

Each interface between materials with different coefficients of thermal expansion (CTE) is a potential fatigue failure site under thermal cycling.

**Bond wire fatigue** (most common failure mechanism):
- Crack initiates at the tail of the bond wire and propagates along internal grain boundaries until complete lift-off
- Detected by increase in forward voltage drop Vce,on (5-20% increase = end-of-life)
- Emitter bonds on IGBTs are most vulnerable (largest temperature swings)
- Typical wire: 300-500um diameter aluminum, <10A per wire, 100-400mW self-heating

**Bond wire heel cracking:**
- Caused by flexure fatigue as wire expands/contracts with temperature
- For 1cm wire length and 50C temperature swing: ~10um displacement at loop top, ~0.05 degree bending angle change at heel
- Occurs mainly with non-optimized ultrasonic bonding process

**Aluminum metallization reconstruction:**
- Thermal cycling above 110C causes plastic deformation at grain boundaries
- Results in aluminum grain extrusion (hillocks) and cavitation (voids)
- Increases sheet resistance, causing steady linear increase in Vce,on
- Can lead to complete metallization depletion at emitter contact vias

**Solder joint fatigue:**
- Most critical interface: substrate-to-baseplate solder (worst CTE mismatch + large area + high temperature swing)
- Cracks initiate at solder fillet edges and propagate along intermetallic phases or precipitates
- Process-induced voids accelerate crack propagation
- Detected by thermal resistance increase (20-50% increase = end-of-life)
- Degraded thermal resistance increases junction temperature, which accelerates bond wire fatigue (coupled failure modes)

**Burnout failures:**
- Short-circuit condition: large current at full blocking voltage causes thermal runaway
- Current can reach 100kA peaks in microseconds, peak power up to 100MW
- Bond wires evaporate, creating conductive path for arcing; shock wave destroys module
- Causes: operation outside SOA, gate malfunction, current crowding from uneven sharing, Rth degradation, cosmic ray events

**Cosmic ray failures:**
- Neutron-induced single-event burnout in high-voltage devices biased in blocking state
- Failure rate increases exponentially with DC voltage, temperature, and altitude
- Can produce >10,000 FIT if DC voltage is not properly derated
- Modeled by exponential distribution (constant failure rate) due to stochastic nature

#### 12.2 Empirical Lifetime Models for Power Modules

**Basic Coffin-Manson (LESIT model, 1990s):**
```
Nf = a * (dTj)^(-n) * exp(Ea / (kB * Tj_mean))
```
where dTj = junction temperature swing, Tj_mean = mean junction temperature (K), Ea = activation energy, kB = Boltzmann constant.

**INFINEON CIPS2008 model (extended Coffin-Manson):**
```
Nf = K * (dTj)^(-b1) * exp(b2/Tj_max) * ton^b3 * I^b4 * V^b5 * D^b6
```
Including effects of: heating time ton, maximum junction temperature Tj_max, current per bond wire I, chip voltage class V, bond wire diameter D. All dependencies are power-law.

**SEMIKRON SKiM model (for sintered modules):**
```
Nf = A * (dTj)^(-alpha) * ar^(b1*dTj+b0) * ((C + ton^gamma)/(C + 1)) * exp(Ea/(kB*Tj_mean)) * f_Diode
```
Including bond wire aspect ratio (ar), load pulse duration (ton), and a diode de-rating factor. Based on 97 power cycling tests over 5 years.

**Key findings from power cycling tests:**
- Longer heating time (ton) has a severe impact on lifetime -- it is an important aging accelerator
- Bond wire lifetime is less affected by mean junction temperature than solder lifetime (lower Ea)
- Solder degradation dominates at high temperatures; bond wire lift-off dominates at moderate temperatures
- At intermediate temperatures, both mechanisms interact and gradually lead to end-of-life

#### 12.3 Physics-Based Lifetime Models

**Energy-based solder fatigue (ETHZ-PES model):**
```
Nf = Wcrit * (dW_hys)^(-n)
```
where dW_hys = inelastic strain energy density per cycle from the stress-strain hysteresis loop. The hysteresis loop is calculated from constitutive solder equations (elastic + plastic + creep deformation) using Clech's algorithm.

Constitutive equations for SnAg3.5 solder:
- Elastic: gamma_elastic = tau / G(T), where G(T) = G0 - G1*(T-273K), G0 = 19,310 MPa, G1 = 68.9 MPa/K
- Plastic: gamma_plastic = Cp * (tau/G)^mp, Cp = 2e-11, mp = 6.6 (Darveaux model)
- Creep: steady-state strain rate = C1 * (G(T)/T) * [sinh(alpha * tau/G(T))]^n * exp(-Q/(kT))

**Crack propagation model (Darveaux):**
```
N0 = K1 * (dW)^K2          [cycles to crack initiation]
da/dN = K3 * (dW)^K4       [crack growth rate per cycle]
```
where dW = plastic energy density per thermal cycle. End-of-life when Rth increases >20%.

**Damage accumulation (Miner's rule):**
```
Q_total = sum(ni / Nf_i) for all temperature swing bins i
End-of-life when Q_total = 1
```
where ni = number of cycles at temperature swing dTi (from mission profile), Nf_i = cycles to failure at that swing level (from lifetime model). Temperature cycles are extracted from mission profiles using the **Rainflow counting algorithm**.

#### 12.4 Accelerated Testing Methods

**Power cycling (PC) test:**
- Device actively heated by switching current on/off
- Tests bond wires and chip solder (closest to heat source)
- Typical failure criteria: Vce,on increase >5-20% (bond wire), Rth increase >20% (solder)

**Thermal cycling (TC) test:**
- Passive temperature cycling in environmental chamber
- Tests large-area solder joints (substrate-to-baseplate)
- Temperature range typically -40C to +125C or wider

**HALT (Highly Accelerated Limit Testing):**
- Qualitative test to find design limits, not predict lifetime
- Step-stress with increasing temperature and vibration until failure
- Identifies operating limit and destruct limit
- Temperature steps: 10C increments from 0C to -60C (cold) and 60C to 120C (hot)
- Vibration steps: 10 Grms increments from 10 to 50 Grms
- Combined stress: thermal cycling at 60C/min ramp rate with vibration at 25% and 50% of UDL

**CALT (Calibrated Accelerated Lifetime Testing):**
- Quantitative ALT with minimum 6 samples
- Three stress levels at 90%, 81%, and lowest feasible % of destruct limit
- Two samples per stress level tested to failure
- Extrapolates to use-condition lifetime via acceleration models

---

### 13. Mission Profile-Based Reliability Prediction

#### 13.1 Mission Profile Concept

A mission profile is the complete representation of all relevant conditions a system experiences throughout its intended life: temperature profiles (ambient + self-heating), electrical loading patterns, humidity, vibration, on/off cycling, and seasonal/diurnal variations.

**Why mission profiles matter:**
- Power electronic components experience vastly different stress levels depending on the application
- A power module in a wind turbine sees irregular thermal cycling from 15C to 90C driven by wind speed variations
- A PV inverter sees daily thermal cycling driven by solar irradiance plus seasonal ambient temperature variation
- Thermal cycling accounts for >55% of power electronics failure probability; humidity ~20%, vibration ~19%

#### 13.2 Translating Mission Profiles to Lifetime Predictions

**Step-by-step procedure:**

1. **Acquire mission profile data**: real-world operating data (wind speed, solar irradiance, ambient temperature, load profile) at appropriate time resolution (minutes to hours for long-term cycling, milliseconds for short-term cycling)

2. **Electrical-thermal simulation**: translate mission profile into junction temperature profile Tj(t) using:
   - Electrical model: calculate power losses Ploss(t) from operating point
   - Thermal model: calculate Tj(t) from Ploss(t) using Foster/Cauer thermal networks
   - Account for electro-thermal coupling (temperature affects switching losses)

3. **Cycle counting with Rainflow algorithm**: extract individual thermal cycles from the complex Tj(t) waveform, characterized by:
   - Junction temperature swing dTj
   - Mean junction temperature Tj_mean
   - Cycle period / heating time ton

4. **Lifetime model evaluation**: for each cycle bin i, calculate Nf_i using the appropriate Coffin-Manson or extended lifetime model

5. **Damage accumulation via Miner's rule**:
```
CL_i = n_i / N_i                    [consumed lifetime fraction from cycle bin i]
TCL = sum(CL_i)                     [total consumed lifetime per year]
B10_lifetime = 1 / TCL  (years)     [years until 10% of population fails]
```

#### 13.3 Two Time Scales of Thermal Cycling

**Short-term (milliseconds to seconds):**
- Caused by AC current alternation at fundamental frequency (50/60 Hz)
- Junction temperature oscillates at fundamental frequency with constant amplitude
- Case temperature remains nearly constant (filtered by thermal capacitance)
- Contributes many small-amplitude cycles per year

**Long-term (minutes to seasons):**
- Caused by variations in input power (wind speed, solar irradiance), ambient temperature, and load changes
- Junction AND case temperature both vary significantly
- Produces fewer but larger-amplitude cycles
- Often the dominant contributor to lifetime consumption

**Both time scales must be evaluated** for accurate lifetime prediction. Short-term cycling is counted using annual load distribution; long-term cycling is extracted via Rainflow counting.

#### 13.4 Practical Findings from Mission Profile Studies

**Wind turbine converters:**
- Power converter failures account for 13% of all wind turbine failures and 18.4% of downtime
- The IGBT is most stressed in the grid-side converter; the diode is most stressed in the rotor-side/generator-side converter
- Reactive power delivery per grid codes (overexcited operation) can reduce power module lifetime to 1/4 of normal operation
- Higher wind classes (I > II > III) result in shorter converter lifetime
- PMSG full-scale converter: more balanced lifetime between grid-side and generator-side vs DFIG partial-scale

**PV inverters:**
- PV inverter failures account for 37% of all PV system failures
- Daily thermal cycling from solar irradiance variation is a major lifetime driver
- Seasonal ambient temperature variation adds long-term cycling
- Thermal-optimized control (adjusting switching frequency or reactive power to reduce temperature swings) can significantly extend lifetime

---

### 14. Practical Power Supply Reliability Qualification (Ch. 15)

#### 14.1 Reliability Qualification Plan

A complete power supply reliability qualification includes these activities in sequence:

| Activity | Timing | Purpose |
|---|---|---|
| Reliability qualification schedule | 45 days before EVT | Plan all activities |
| DFMEA | Before design freeze | Identify and eliminate design weaknesses |
| Thermal profile analysis | EVT and DVT | Measure worst-case component temperatures |
| De-rating analysis | EVT and DVT | Verify stress margins |
| Capacitor life analysis | EVT and DVT | Predict capacitor lifetime under operating conditions |
| Fan life analysis | DVT | Validate fan bearing and lubricant lifetime |
| HALT | EVT and DVT | Find operating and destruct limits |
| Vibration/shock/drop test | DVT | Verify mechanical robustness |
| Manufacturing conformance (ORT) | After first shipment | Ongoing production quality assurance |

(EVT = engineering verification test; DVT = design verification test)

#### 14.2 DFMEA Ranking Chart

| Ranking | Severity | Occurrence | Detection |
|---|---|---|---|
| 5 | Safety-related catastrophic failure | Almost inevitable (<1 in 3) | Undetectable until catastrophic failure |
| 4 | Product totally inoperable | Repeated failure (<1 in 8) | Remote chance of detection |
| 3 | Operable at reduced performance | Occasional failure (<1 in 80) | Low chance of detection |
| 2 | Comfort/convenience items degraded | Relatively few (<1 in 150,000) | Moderate chance of detection |
| 1 | No effect | Unlikely (<1 in 1,500,000) | High chance of detection |

**RPN = Severity x Occurrence x Detection.** Any failure mode with severity = 5 (safety hazard) must be eliminated regardless of RPN.

#### 14.3 Thermal Profile Analysis Methodology

1. Attach thermocouples to all critical components (semiconductors, capacitors, magnetics, resistors) at the points specified in datasheets
2. Operate at worst-case conditions: maximum load, maximum ambient temperature, minimum airflow, all component suppliers
3. Allow thermal equilibrium (minimum 30 minutes with <1C change)
4. Record temperatures continuously with data loggers
5. Capture thermal images for spatial temperature mapping
6. Use thermal data as input for de-rating analysis and capacitor life calculations

#### 14.4 De-Rating Analysis with Worked Examples

The stress factor quantifies how close a component operates to its rating:
```
Stress Factor (%) = (Applied Level / Rating) * 100
```

**Worked de-rating examples:**

**Example 1 (capacitor voltage):** 12Vdc output with +3% tolerance needs capacitor with <80% stress factor.
Required: (12 * 1.03) / Vrated < 80%, so Vrated > 15.45V. Select >= 16V rated capacitor.

**Example 2 (capacitor temperature):** 105C rated capacitor with de-rating guideline "operate 25C below max."
Maximum operating temperature = 105 - 25 = 80C.

**Example 3 (diode junction temperature):** Diode rated Tj_max = 130C, Rth_jc = 3C/W, Pd = 2.5W, Tcase = 80C.
Tj = 80 + (3 * 2.5) = 87.5C. Stress factor = 87.5/130 = 67.3%. Passes.

**De-rating chart for 10-year and 5-year designs:**

| Component | Parameter | 10-Year Stress Factor | 5-Year Stress Factor |
|---|---|---|---|
| Carbon composition resistor | Power dissipation | 60% | 70% |
| Fixed film resistor | Power dissipation | 60% | 70% |
| Fixed film resistor | Max working voltage | 70% | 70% |
| Resistor (all) | Below max temp limit | 25C margin | 25C margin |
| MLCC | DC voltage | 80% | 80% |
| MLCC | Below max temp limit | 10C margin | 10C margin |
| Al electrolytic | DC voltage | 80% | 85% |
| Al electrolytic | Ripple current | 70% | -- |
| Al electrolytic | Below max temp limit | 10C margin | 10C margin |

#### 14.5 Capacitor Life Analysis with Equations

**Aluminum electrolytic capacitor lifetime:**
```
L_cap = L_base * pi_T_ext * pi_T_int * pi_V * pi_Q
```
where:
- L_base = base lifetime from capacitor datasheet
- pi_T_ext = 2^((Tmax - Ta) / 10) -- external temperature factor (Tmax = rated temp, Ta = case temp)
- pi_T_int = 2^((dTo - dTi) / dTo) -- internal temperature factor
  - dTo = core temperature rise at rated ripple current
  - dTi = dTo * (I_ripple_actual / I_ripple_rated)^2
- pi_V = 1.05 - ((Va / (Vr * 0.6))^2) / 2 -- voltage stress factor (Va = applied, Vr = rated)
- pi_Q = ln(L_base * Tmax) / 13.1 -- quality factor

**MIL-HDBK-217 comprehensive capacitor failure rate:**
```
lambda_c = lambda_b * pi_T * pi_C * pi_V * pi_SR * pi_Q * pi_E  (failures/10^6 hours)
```
where pi_T = ambient temperature, pi_C = capacitance, pi_V = voltage stress, pi_SR = series resistance, pi_Q = quality, pi_E = environment.

**Os-Con (organic semiconductor electrolyte) capacitor lifetime:**
```
L_oscon = L_base * 10^((Tmax - Ta) / 20) * Fd
```
where Fd = frequency de-rating factor (Fd = 1 at >100kHz, Fd = 0.05 at 50Hz). Os-Con capacitors are excellent for high-frequency switching but not suitable for line-frequency filtering.

#### 14.6 Fan Life Analysis

- Fan lifetime is limited by bearing wear-out (L10 / B10 life) and lubricant evaporation
- **Four-corner cycling test** required for both storage and operation:
  - Storage: cycle through (-40C, 5%RH) to (70C, 95%RH) in specified pattern
  - Operation: cycle through (0C, 20%RH) to (70C, 95%RH)
- Thermal shock: -40C to 70C, minimum 30-minute dwell, multiple cycles
- Fan failure criteria: fail to spin, RPM out of tolerance, excess current, abnormal noise, grease leakage, impeller touching housing

#### 14.7 HALT Methodology for Power Supplies

**Test sequence:**
1. **Low temperature step stress**: start at 0C, decrease by 10C steps to -60C. At each step: power on, run full diagnostic, hold for Thold minutes. Find low operating limit.
2. **High temperature step stress**: start at 60C, increase by 10C steps to 120C. Same procedure. Find high operating limit.
3. **Vibration step stress**: at 25C, increase from 10 Grms in 10 Grms steps to 50 Grms while running diagnostics. Find upper destruct limit (UDL).
4. **Combined thermal + vibration**: thermal cycling at 60C/min ramp rate between (low limit + 10C) and (high limit - 10C), with vibration at 25% and 50% of UDL.

**Common HALT failure modes:**
- Mechanical: loose screws/rivets, plastic cracks, misalignment, permanent component deformation
- Functional: diagnostic test failures, abnormal output behavior

#### 14.8 Manufacturing Conformance Testing (ORT)

**Ongoing Reliability Testing (ORT)** is performed on production samples after first customer shipment:

- **Burn-in conditions:** 50-55C chamber, 95% rated power, split population between 100-120Vac and 220-240Vac
- **Power cycling:** 45 min on, 5 min off, then 10 min of quick power cycling (30s on / 30s off, 10 cycles)
- **Duration:** 14 days (or as specified)
- **Sample size:** depends on production volume and risk level (4-26 units/week for high risk, 2-20 for low risk)
- **Risk assessment:** "high risk" for first 6 months after first shipment, or if any ORT failure in previous 20 weeks

---

### 15. Weibull Distribution for Wear-Out Analysis

The Weibull distribution is the best-suited model for wear-out failure mechanisms in power modules and capacitors:

```
F(t) = 1 - exp(-(lambda*t)^beta)
```
where:
- lambda = scale parameter (1/characteristic life eta, where eta is the time at which 63.2% have failed)
- beta = shape parameter:
  - beta < 1: decreasing failure rate (infant mortality)
  - beta = 1: constant failure rate (random, reduces to exponential)
  - beta = 3.5: approximates normal distribution
  - beta > 1: increasing failure rate (wear-out)

**Failure rate:**
```
h(t) = beta * lambda * (lambda*t)^(beta-1)
```

**Competing failure mechanisms:** When two mechanisms with parameters (lambda1, beta1) and (lambda2, beta2) can both cause failure:
```
h(t) = beta1*lambda1*(lambda1*t)^(beta1-1) + beta2*lambda2*(lambda2*t)^(beta2-1)
```

**Redundancy for reliability improvement:**
- Series system (no redundancy): R_system = product(Ri)
- k-out-of-n hot redundancy: system works if at least k of n identical units survive
- Hot redundancy: all units loaded from start; cold redundancy: standby units unloaded until needed (requires switching unit)
- Example: Six modules at 100 FIT each, 30-year operation: survival probability ~85%. At 400 FIT each: survival probability ~50%.

---

### 16. System-Level Reliability Design Rules (Summary)

Based on all sources including Chung/Wang/Blaabjerg/Pecht:

1. **Temperature is the dominant killer.** Thermal cycling causes >55% of power electronics failures. Minimize both steady-state temperature (derating) and temperature swings (thermal design, active control).

2. **Design for the mission profile, not a single worst case.** Use actual operating profiles with Rainflow counting and Miner's rule for accurate lifetime prediction.

3. **Separate failure mechanisms require separate lifetime models.** Bond wire fatigue and solder fatigue have different physics and different sensitivities to temperature, time, and stress.

4. **Capacitors and power modules are the reliability-critical components.** Focus design effort, derating, and condition monitoring on these components first.

5. **Film capacitors are far more reliable than electrolytics** under electro-thermal stress, but are vulnerable to humidity. MLCCs are reliable but fail short (catastrophic).

6. **Use FMMEA, not just FMEA.** Understanding the failure mechanism enables selection of appropriate physics-based models and meaningful condition monitoring.

7. **HALT finds design limits; CALT/ALT predicts lifetime.** HALT is qualitative (test-fail-fix); CALT is quantitative (extrapolate to use conditions).

8. **Condition monitoring can extend useful life.** Track ESR and capacitance for capacitors; Vce,on and Rth for power modules. Degradation is usually slow enough for offline monitoring.

9. **Active thermal control is a reliability tool.** Controlling switching frequency, reactive power, or modulation strategy based on estimated junction temperature can reduce thermal cycling and extend lifetime.

10. **Verify with production testing.** ORT after first shipment catches manufacturing-induced defects that design analysis cannot predict.
