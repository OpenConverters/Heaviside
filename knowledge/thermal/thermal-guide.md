# Thermal Management Guide for Switchmode Power Supplies

Source: Billings & Morey, *Switchmode Power Supply Handbook*, 3rd Ed. (2010)

Note: Imperial units are used following the source text conventions. 1 in = 25.4 mm.

---

## 1. Temperature and Reliability

### 1.1 Failure Rate vs. Temperature

From MIL-HDBK-217A:
- A transistor at Tj = 180C has 1/20 the life of one at 25C
- **General rule for complete SMPS: failure rate doubles for every 10-15C rise above 25C**
- A unit operating at 70C has approximately 10% of its MTBF at 25C
- Electrolytic capacitors and wound components typically limit the maximum temperature before semiconductors do

### 1.2 Design Implications

- Keep junction temperatures well below absolute maximum ratings
- The most critical thermal decision (cooling method) must be made BEFORE the electrical design begins
- Key questions: Contact cooled? Forced air? Free convection? Altitude? Temperature range?

---

## 2. Thermal Resistance Network (Electrical Analogue)

### 2.1 Analogy Table

| Thermal Parameter | Unit | Electrical Analogue | Unit |
|---|---|---|---|
| Temperature difference Td | C | Potential difference Vd | V |
| Thermal resistance Rth | C/W | Resistance R | ohm |
| Thermal conductivity K | W/C | Electrical conductivity | S |
| Heat flow Q | W (J/s) | Current I | A |
| Heat capacity Ch | J/C | Capacitance C | F |
| Heat source (constant power) | W | Constant current source | A |
| Ambient temperature | C | Ground potential | V |

### 2.2 Thermal Circuit Model

For a semiconductor mounted on a heat sink with insulator:

```
Junction --[Rth_jc]--> Case --[Rth_cs]--> Heat Sink --[Rth_sa]--> Ambient
   Tj                    Tc                  Ts                     Ta
```

**Total thermal resistance:**
```
Rth_total = Rth_jc + Rth_cs + Rth_sa
```

**Junction temperature:**
```
Tj = Ta + P_diss * Rth_total
Tj = Ta + P_diss * (Rth_jc + Rth_cs + Rth_sa)
```

**Temperature at any interface:**
```
T_interface = Ta + P_diss * (sum of Rth from interface to ambient)
```

### 2.3 Determining Unknown Power Dissipation

If thermal resistance of any element is known, measure temperature difference across it:
```
P_diss = delta_T / Rth_known
```

This is especially useful when actual dissipation is hard to calculate (e.g., diode reverse recovery losses).

---

## 3. Thermal Resistance Data

### 3.1 Heat Exchanger Metals

| Material | Heat Capacity (J/in^3/C) | Thermal Resistance (1" cube, C/W) |
|---|---|---|
| Aluminum (6061) | 40.5 | 0.23 |
| Copper 110 | 57.5 | 0.10 |
| Steel C1040 | 63 | 0.84 |
| Brass 360 | 50 | 0.34 |

Key insight: Copper has higher thermal capacity but this only affects transient response (time to reach steady state), NOT the final temperature. Steady-state temperature depends on surface area and heat exchange to ambient, not bulk material.

### 3.2 Common Insulating Materials

| Material | Thermal Resistance (1" block, C/W) | Max Temp (C) | Dielectric Constant |
|---|---|---|---|
| Mica | 62-91 | 550 | 6.5-8.7 |
| Aluminum oxide (Al2O3) | 1.43 | 1700 | 8.9 |
| Beryllium oxide (BeO)* | 0.15-0.27 | 2149 | 6.5 |
| Polyimide plastic | 270 | 400 | 3.5 |
| Silicone rubber | 151 | 180 | 1.6 |
| Thermal epoxy | 25-50 | ~90 | 6 |
| Still air | 1430 | -- | 1 |

*Warning: BeO is highly toxic if fragmented into small particles.

### 3.3 Typical Case-to-Heatsink Thermal Resistance (with Mounting Compound)

| Insulator | TO-3 (C/W) | TO-220 (C/W) | Max Temp (C) |
|---|---|---|---|
| Mica (0.006") | 0.4 | 1.8 | >200 |
| Aluminum oxide (0.062") | 0.34 | 1.53 | >200 |
| Beryllium oxide (0.062") | 0.2 | 1.0 | >200 |
| Polyimide (Thermofilm, 0.002") | 0.55 | 2.3 | >200 |
| Silicone rubber (0.008") | 1.0 | 4.5 | 180 |

### 3.4 Effect of Mounting Torque

Thermal resistance between TO-3 transistor and heat sink (with mica insulator) decreases significantly with proper screw torque. Using thermal compound further reduces Rth_cs.

---

## 4. Heatsink Sizing

### 4.1 Design Procedure

**Given:** Device dissipation P_diss, maximum junction temperature Tj_max, ambient temperature Ta_max.

**Step 1: Calculate required Rth_sa (heatsink-to-ambient)**

```
Rth_sa = (Tj_max - Ta_max) / P_diss - Rth_jc - Rth_cs
```

**Step 2: Select heatsink extrusion with Rth_sa <= calculated value**

### 4.2 Worked Example

TO-3 transistor, P = 20 W, Tj_max = 136C, Ta = 50C:
- Rth_jc = 1.5 C/W (from datasheet)
- Rth_cs = 0.4 C/W (mica insulator with compound)
- Rth_jc + Rth_cs = 1.9 C/W
- Delta_T from junction to heatsink = 20 * 1.9 = 38C
- Heatsink temperature = 136 - 38 = 98C
- Available delta_T for heatsink to ambient = 98 - 50 = 48C
- Required Rth_sa = 48 / 20 = 2.4 C/W

Select a heatsink extrusion rated at <= 2.4 C/W.

### 4.3 Where Thermal Compound Matters

**Rule: Identify the dominant thermal resistance and reduce THAT one.**

- Small air-cooled heatsinks: Rth_sa dominates (typically 4+ C/W) => improving mounting interface has negligible effect
- Water-cooled / forced-air heatsinks: Rth_sa is very low => mounting interface becomes the bottleneck => thermal compound and insulator selection are critical

**Example comparison:**
- Free-air cooled (Rth_sa = 4 C/W): Improving Rth_cs by 50% changes Tj by only 2.5C
- Water-cooled (Rth_sa ~ 0): Insulator Rth_cs of 1.0 C/W at 100 W => 100C rise across insulator alone

**Solutions for low-Rth_sa applications:**
- Increase insulator contact area (e.g., copper header/spreader between device and insulator)
- Example: 5x larger insulator area => Rth_cs reduced from 1.0 to 0.2 C/W
- Use lower Rth insulator material (BeO, AlN, Al2O3)
- Apply thermal compound on all interfaces; torque screws to specification

---

## 5. Natural Convection Cooling

### 5.1 Heatsink Thermal Resistance vs. Volume

For natural convection, thermal resistance of commercial finned heatsinks follows a consistent trend when plotted against enclosed volume on a log-log scale. Very few designs deviate far from this general relationship.

**Key facts:**
- Heatsinks with many closely-spaced fins are NOT significantly better than equivalent-volume heatsinks with fewer fins in free air (radiation between fins cancels; convection flow is restricted)
- Effective radiation surface = silhouette surface area (not total fin surface)
- Very little improvement in Rth beyond 12 inches of finned extrusion length
- Vertical mounting is ~10% better than horizontal (convection effect)

### 5.2 Non-Linear Thermal Resistance

Thermal resistance is NOT constant -- it decreases as temperature differential increases:
- Stefan-Boltzmann radiation increases with T^4
- Convection turbulence increases with temperature

Use manufacturer correction curves to adjust Rth for actual operating temperature differential.

### 5.3 Altitude Derating

Convection cooling efficiency decreases at high altitude:
- 20% reduction at 10,000 ft
- Must derate heatsink capacity accordingly

### 5.4 Thermal Resistance vs. Surface Area

Rth does not decrease in direct proportion to surface area because:
- Heat must conduct to remote regions of the heatsink (temperature gradient)
- Air is progressively heated passing over the surface

The relationship depends on plate material, surface finish, and mounting orientation (vertical vs. horizontal).

---

## 6. Forced-Air Cooling

### 6.1 When to Use

Power supplies > 500 W normally require forced-air cooling.

### 6.2 Required Airflow

```
Airflow (cfm) = 1.76 * Q_loss / delta_T
```

where:
- Q_loss = total internal power loss (W)
- delta_T = permitted internal temperature rise (C)
- cfm = cubic feet per minute (at sea level)

### 6.3 Dramatic Improvement

Forced air reduces heatsink Rth dramatically:
- Example: finned extrusion goes from >6 C/W (free air) to <1.5 C/W at 1000 ft/min airflow
- This is where fin design matters -- closely spaced fins are much more effective in forced air than in free convection

### 6.4 Fan Selection

- Fan must overcome back pressure from enclosure restrictions
- Typical back pressure: 0.1 to 0.3 inches of water per 100 cfm
- Measure back pressure in finished unit
- Select fan to deliver required cfm at measured back pressure
- Place hot components in exhaust stream
- Direct airflow to prevent static air pockets

---

## 7. Radiation Cooling

### 7.1 General Characteristics

- Follows Stefan-Boltzmann law: Q proportional to T^4 (absolute temperature)
- Electromagnetic wave (infrared) -- requires line-of-sight path
- Generally a nuisance inside SMPS (heat radiated by one component is absorbed by adjacent components)
- Useful only when good radiant path to cooler environment exists

### 7.2 Radiation Heat Loss

```
Q = e * 36.77e-12 * T^4  [W per square inch]
```

where:
- e = emissivity of surface
- T = temperature difference from environment (Kelvin)

### 7.3 Surface Emissivity

| Material | Surface Finish | Emissivity |
|---|---|---|
| True blackbody | True black | 1.0 |
| Aluminum | Polished | 0.04 |
| Aluminum | Painted (any color) | 0.9 |
| Aluminum | Rough | 0.06 |
| Aluminum | Matt anodized (any color) | 0.8 |
| Copper | Rolled bright | 0.03 |
| Steel | Plain | 0.5 |
| Steel | Painted (any color) | 0.8 |

**Key facts:**
- Color does not matter in IR range -- matt anodized aluminum is 0.8 emissivity in ANY color
- Glossy surfaces are poor radiators but good reflectors
- Use polished aluminum foil as a radiation shield between hot components and sensitive ones (e.g., between power resistors and electrolytic capacitors)
- Good radiators are also good absorbers -- keep radiant-cooled supplies out of direct sunlight

---

## 8. Loss Calculation per Component

### 8.1 Switching Transistor / MOSFET

```
P_total = P_conduction + P_switching + P_gate_drive

P_conduction = I_rms^2 * Rds_on(Tj)    [MOSFET]
P_conduction = I_mean * Vce_sat          [BJT]

P_switching = 0.5 * V * I * (t_rise + t_fall) * f_sw

P_gate = Qg * Vgs * f_sw
```

### 8.2 Rectifier Diode

```
P_diode = I_rms * Vf + P_reverse_recovery

P_reverse_recovery = 0.5 * Qrr * V_reverse * f_sw
```

Approximate: P_diode ~ I_rms * 0.8 V (Si) or I_rms * 0.6 V (Schottky)

### 8.3 Output Capacitor (Electrolytic)

```
P_cap = I_ripple_rms^2 * ESR
```

ESR varies with frequency and temperature (use manufacturer data).

### 8.4 Transformer

**Core loss:**
```
P_core = specific_loss(f, delta_B) * core_volume
```

Read specific loss from manufacturer core loss curves (mW/cm^3 vs. B_peak at frequency f).

For single-ended converters using push-pull core loss charts: enter with delta_B/2 as the peak value.

**Copper loss:**
```
P_copper = I_rms^2 * R_dc * Fr
```

where Fr = AC/DC resistance ratio (from skin/proximity effect analysis). Fr depends on:
- Wire diameter relative to skin depth
- Number of winding layers
- Frequency

At 50 kHz with 5 layers of #13 AWG wire, Fr can be ~80 (AC resistance 80x DC resistance).

### 8.5 Inductor / Choke

```
P_total = P_core + P_copper_dc + P_copper_ac

P_core = specific_loss(f, delta_B_ac) * volume   [based on AC ripple only]
P_copper_dc = I_dc^2 * R_dc
P_copper_ac = I_ac_rms^2 * R_dc * Fr
```

### 8.6 Snubber Resistor

```
P_snubber = 0.5 * C_snub * V_snub^2 * f_sw
```

### 8.7 Current Sense Resistor

```
P_sense = I_rms^2 * R_sense
```

---

## 9. Thermal Capacity (Transient Considerations)

### 9.1 Heat Capacity

```
Ch = specific_heat * volume  [J/C]
```

- Copper 1" cube: 57.5 J/C
- Aluminum 1" cube: 40.5 J/C

**Implication:** At 10 W input, a 10 in^3 copper heatsink takes ~57 seconds to rise 1C. Thermal steady state may take several minutes.

### 9.2 Thermal Capacity Does NOT Affect Steady-State Temperature

Thermal capacity only affects:
- Time to reach equilibrium
- Peak temperature during short transient overloads

A copper heatsink appears to "run cooler" than aluminum only because it takes longer to reach steady state. Final temperature is the same for the same surface area and conditions.

**When thermal capacity matters:**
- Pulsed/transient loads with low duty cycle
- Short-duration overload events
- Start-up thermal transients

---

## 10. PCB Thermal Design Considerations

### 10.1 Copper Area as Heatsink

- PCB copper acts as a heatsink for surface-mount components
- Thermal resistance of copper traces depends on width, thickness, and length
- Standard 1 oz copper (35 um): thermal conductivity limited for high-power components
- 2 oz or heavier copper recommended for power stages

### 10.2 Thermal Vias

- Connect component pads to inner copper planes or backside copper for improved heat spreading
- Array of vias under thermal pad of QFN/DFN packages
- Via thermal resistance approximately: Rth_via = L / (k * A * N)
  where L = board thickness, k = copper conductivity, A = via copper cross-section, N = number of vias

### 10.3 Component Placement

- Separate heat-generating components from temperature-sensitive ones (especially electrolytic capacitors)
- Place electrolytic capacitors in coolest area of board
- Use polished aluminum radiation shields between hot components and capacitors
- Ensure adequate airflow paths in enclosure design
- Avoid placing capacitors directly above hot components in vertical convection flow

---

## 11. Derating Guidelines

### 11.1 General Philosophy

- Design for lowest practical operating temperatures
- Derate all components below their absolute maximum ratings
- Temperature derating is the single most effective reliability improvement

### 11.2 Typical Derating Factors

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
| Ceramic capacitor | Voltage (Class II) | Apply at <= 50% rated voltage to account for DC bias derating |
| Film capacitor | Voltage | Use <= 80% of rated voltage |

### 11.3 Altitude Derating

- Free-air cooling efficiency reduces ~20% at 10,000 ft
- Forced-air fans deliver less air mass at altitude
- Creepage and clearance distances may need to increase at altitude (lower dielectric strength of air)

---

## 12. Temperature Rise Estimation

### 12.1 Quick Estimation for Wound Components

```
T_rise = P_total / (h * A_surface)
```

Typical h (heat transfer coefficient) for free-air convection: approximately 7-12 mW/cm^2/C.

### 12.2 For Heatsink-Mounted Components

```
T_rise_junction = P_diss * (Rth_jc + Rth_cs + Rth_sa)
```

### 12.3 For Enclosed SMPS

```
T_rise_internal = 1.76 * P_total_loss / Airflow_cfm   [forced air]
```

### 12.4 Final Verification

All thermal calculations are approximations. The only reliable method is measurement in the finished product under worst-case conditions:

1. Operate at maximum rated load
2. Operate at maximum ambient temperature
3. Wait for thermal equilibrium (can take 30+ minutes)
4. Measure critical component temperatures (junction, case, capacitor surface)
5. Verify all temperatures are within derated limits
6. Pay special attention to electrolytic capacitors (proximity heating is often greater than internal dissipation)
