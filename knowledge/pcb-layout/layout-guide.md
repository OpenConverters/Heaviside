# PCB Layout Guide for Power Electronics

Sources: Lidow et al. "GaN Transistors for Efficient Power Conversion" (3rd ed, 2020); Wang 2005 (EMI Filter Parasitics, VT); Chen 2006 (Integrated EMI Filters, VT); Billings & Morey "Switchmode Power Supply Handbook" (3rd ed, 2010); IPC-2221/IPC-2152 standards.

---

## 1. Power Loop Optimization

The power loop (also called the switching loop or commutation loop) carries pulsed current during switching transitions. Its inductance directly determines voltage overshoot, ringing frequency, switching loss, and radiated EMI.

### 1.1 Why Loop Inductance Matters

Voltage overshoot during turn-off is proportional to loop inductance and current slew rate:

```
V_overshoot = L_loop * di/dt
```

For GaN transistors with di/dt of 1-5 A/ns, even 1 nH of loop inductance produces 1-5 V overshoot. Since most GaN devices lack avalanche ratings, this overshoot must stay within VDS(max) or the device is destroyed.

Radiated emission from the loop is proportional to frequency squared, current, and loop area:

```
E_radiated ~ f^2 * I * Area_loop
```

### 1.2 Power Loop Design Options

**Lateral Power Loop:**
- Components and decoupling capacitors on the same PCB side.
- Uses a shield layer (first inner layer) for magnetic field cancellation.
- Independent of board thickness.
- Requires an unbroken shield plane directly beneath the power loop traces.

**Vertical Power Loop:**
- Decoupling capacitors on the opposite side of the PCB, directly beneath the switching devices.
- Uses opposing currents on top and bottom layers for field cancellation.
- Performance depends on board thickness (thinner = lower inductance).
- Incompatible with single-sided assembly.

**Optimal Power Loop (recommended):**
- Components on the same side; first inner layer used as return path directly beneath the top-layer power loop.
- Combines minimal loop area with magnetic field self-cancellation.
- Independent of board thickness.
- Compatible with single-sided PCB assembly.
- Measured loop inductance: approximately 250 pH for discrete devices, approximately 150 pH for monolithic half-bridges.

### 1.3 Measured Impact of Loop Layout

Example: 12 V to 1.2 V buck converter, 1 MHz, EPC2015C GaN FET.

| Layout Style | Loop Inductance | Total Loss | Voltage Overshoot |
|---|---|---|---|
| Vertical power loop | 1.8 nH | 3.8 W | 85% of VDS |
| Lateral power loop | 1.0 nH | 3.3 W | 45% of VDS |
| Optimal power loop | 0.5 nH | 3.1 W | 30% of VDS |

The optimal layout provides 500% faster switching speed and 40% less voltage overshoot compared to an equivalent Si MOSFET design.

### 1.4 Design Rules

1. Place switching devices and input decoupling capacitors in the tightest possible loop.
2. Use adjacent copper layers for send and return currents to achieve magnetic field cancellation.
3. Minimize via count in the power loop path.
4. For half-bridges, the bootstrap capacitor loop is equally critical and must be kept tight.
5. Target loop inductance below 1 nH for GaN designs; below 0.5 nH for MHz switching.

### 1.5 Interleaved Via Technique (for LGA/BGA Devices)

For chip-scale GaN packages, interleave drain and source vias beneath the device:
- Creates multiple small opposing-current loops instead of one large loop.
- Reduces magnetic energy storage and AC conduction losses.
- Shortens high-frequency current paths.

---

## 2. Gate Drive Loop

The gate driver, transistor gate, bypass capacitor, and interconnect form a series LCR resonant circuit. Underdamped ringing on the gate can exceed absolute maximum gate voltage ratings, especially for GaN devices with only 1 V of margin between recommended drive and maximum.

### 2.1 Critical Damping Requirement

The gate loop must be at least critically damped on the turn-on edge:

```
R_G(eq) >= sqrt(4 * L_G / C_GS)
```

where R_G(eq) = R_G(internal) + R_source(driver) + R_external.

Lower gate loop inductance L_G allows lower R_G(eq) while maintaining critical damping, enabling faster switching.

### 2.2 Gate Loop Layout Rules

1. **Place the gate driver as close as possible to the transistor.** Every millimeter of trace adds inductance.
2. **Route gate and gate-return on adjacent PCB layers** with currents flowing in opposite directions for magnetic field cancellation.
3. **Place the gate driver bypass capacitor (CVDD) within the turn-on loop**, as close to the driver as possible. The turn-off loop is naturally smaller (does not include CVDD) and has lower inductance.
4. **Use dedicated gate-return source connections.** For devices with Kelvin source pins, connect the driver return directly to the Kelvin source. For LGA/BGA devices, allocate the source pads closest to the gate as the gate-return path.
5. **Each paralleled device gets its own gate resistors** (separate pull-up and pull-down) for independent speed adjustment.
6. **Isolate the gate-return plane.** The gate-return source plane should be a dedicated copper area, not connected to the power ground at any other point. This prevents power loop transients from coupling into the gate loop.

### 2.3 Common-Source Inductance (CSI)

CSI is inductance on the source side shared between the power loop and the gate loop. It is the single most critical parasitic for GaN circuits.

**Effects:**
- During turn-on: rising drain current creates a voltage across CSI that opposes the gate drive, slowing the current rise and increasing switching loss.
- During turn-off: falling drain current enhances the gate drive, accelerating turn-off (beneficial but less significant than the turn-on penalty).

**Target:** CSI below 50 pH.

**How to minimize:**
- Use devices with dedicated Kelvin source pins.
- Route gate loop and power loop currents in opposite or orthogonal directions from the source connection.
- When paralleling GaN devices, CSI symmetry is the highest layout priority.

### 2.4 dv/dt Induced Turn-On Prevention

When the complementary device in a half-bridge switches, the high dv/dt couples through CGD (Miller capacitance) and can falsely turn on the off-state device. Layout countermeasures:

- Keep the gate pulled low with a low-impedance path (strong pull-down) when the device is off.
- Minimize CSI so that the power loop di/dt does not reinforce the Miller coupling.
- Use under-damped turn-off so the gate is driven slightly negative at the moment the complementary device creates the dv/dt.

### 2.5 Transient Immunity and Ground Bounce

High dv/dt switching generates large transient currents through parasitic capacitances. These currents flowing through layout inductances create ground bounce voltage pulses that can change the logic state of gate driver inputs.

**Best practice:** Place the controller on the same ground as the gate driver source return. Use the transistor source as the local ground reference for the gate driver. Accept that the controller ground will bounce relative to the system ground rather than trying to enforce a single quiet ground.

---

## 3. Grounding Strategy

### 3.1 Ground Plane Under Power Stage

A solid ground plane beneath the power stage switching loop is beneficial:
- Provides a low-impedance return path for high-frequency currents.
- Reduces radiated emissions from power traces through image-current cancellation.
- Shields lower layers from high dv/dt coupling.

### 3.2 Ground Plane Under EMI Filter

A ground plane under the EMI filter has both beneficial and detrimental effects.

**Detrimental:**
- Creates parasitic capacitance between input and output traces through the ground plane (measured: 3.8 pF through ground plane versus 2 pF direct coupling).
- This capacitance bypasses the filter inductor at high frequency.
- Reduces DM inductance via eddy current cancellation in the ground plane (measured: 0.81 uH reduction).

**Recommendations:**
- Do NOT run a continuous ground plane directly under the EMI filter inductor.
- Use slotted or partial ground planes that break the parasitic coupling path.
- Keep input and output filter traces on opposite sides of the ground plane slot.

### 3.3 Ground Plane Splits and Star Grounding

**When to split grounds:**
- Separate analog signal ground from power ground to prevent power stage switching noise from corrupting sense and feedback signals.
- Connect the two grounds at a single point (star connection) near the controller IC ground pin.
- Never route high-current power return paths through the analog ground region.

**When NOT to split grounds:**
- Do not split the ground plane under high-frequency switching loops. The current will find a path through parasitic capacitances across the split, and the increased loop area will worsen EMI.
- High-speed digital signals need a continuous ground return plane.

### 3.4 General Rules

1. Keep power ground and signal ground separate in the layout but connected at one point.
2. Route return currents to minimize loop area. High-frequency return current follows the path of least inductance (directly beneath the signal trace), not the path of least resistance.
3. Never route sensitive signal traces across a ground plane split or slot.
4. Place decoupling capacitors at each IC supply pin with the shortest possible loop to the local ground.

---

## 4. Layer Stackup

### 4.1 Two-Layer Boards

Acceptable for low-power converters (under approximately 10 W) operating at moderate switching frequencies (under 500 kHz).

**Limitations:**
- No dedicated return plane for field cancellation beneath the power loop.
- Limited current capacity without wide traces.
- Poor thermal performance (only two copper surfaces for heat spreading).

**Guidelines for 2-layer:**
- Use a ground pour on the bottom layer beneath the power loop.
- Keep power loop components on the top layer with the tightest possible routing.
- Use 2 oz (70 um) copper for power traces.
- Replicate the power loop outline on the bottom layer with copper fill to create a partial return path.

### 4.2 Four-Layer Boards (Recommended)

The standard for most power electronics designs. A typical stackup for a power converter:

```
Layer 1 (Top):     Power components, switching loop, gate drive
Layer 2 (Inner 1): Ground plane (unbroken under power loop)
Layer 3 (Inner 2): Signal routing, feedback traces, control
Layer 4 (Bottom):  Power plane, auxiliary components, thermal pads
```

**Key points:**
- Layer 2 should be an unbroken ground plane directly beneath the power loop. This provides the return path for the optimal power loop design and shields signal layers from switching noise.
- The spacing between Layer 1 and Layer 2 should be minimized (typically 5-8 mil prepreg) to minimize loop inductance.
- Signal routing on Layer 3 is shielded from the power stage by the Layer 2 ground plane.
- Layer 4 can carry power bus connections and provide bottom-side thermal pads.

### 4.3 Copper Weight

| Application | Recommended Copper Weight | Thickness |
|---|---|---|
| Signal layers | 0.5-1 oz | 17-35 um |
| Power stages under 5 A | 1 oz | 35 um |
| Power stages 5-20 A | 2 oz | 70 um |
| High-current power stages | 3-4 oz | 105-140 um |

Heavier copper increases minimum trace width and spacing requirements. Coordinate with the PCB fabricator for design rules on heavy copper layers.

---

## 5. Current Carrying Capacity

### 5.1 IPC-2152 Guidelines

IPC-2152 supersedes the older IPC-2221 current-capacity charts. The key difference: IPC-2152 accounts for the board's total copper area acting as a heatsink, not just the trace width.

**External (outer layer) traces** dissipate heat more effectively than internal traces due to convection and radiation. Internal traces rely on conduction through the dielectric to reach the surface.

### 5.2 Trace Width Estimation

Approximate trace width for a given current and temperature rise (external layer, 1 oz copper, still air):

| Current (A) | 10C Rise (mil) | 20C Rise (mil) | 30C Rise (mil) |
|---|---|---|---|
| 1 | 25 | 15 | 10 |
| 3 | 100 | 60 | 45 |
| 5 | 200 | 120 | 90 |
| 10 | 500 | 300 | 220 |
| 15 | 900 | 550 | 400 |
| 20 | -- | 800 | 600 |

These values are approximate starting points. For internal layers, increase trace width by approximately 2x for the same temperature rise.

### 5.3 Practical Considerations

- **DC versus AC current:** The above values apply to DC or low-frequency current. At switching frequencies above 100 kHz, skin effect and proximity effect increase AC resistance. Use wider traces or multiple parallel traces for high-frequency current paths.
- **Copper pours:** Large copper fills carry current much more effectively than narrow traces. Use polygon pours for power connections wherever possible.
- **Via current capacity:** A single 12 mil drill via in 1 oz copper carries approximately 1 A continuously. Use via arrays for high-current connections.
- **Thermal relief:** Thermal relief pads on power connections slow heat flow and can cause solder joint issues during assembly. Use solid (no thermal relief) connections on power pads, especially for high-current components.

---

## 6. Thermal Management via PCB

### 6.1 PCB Copper as Heatsink

PCB copper acts as a heatsink for surface-mount components. For chip-scale packages (GaN LGA/BGA, exposed-pad QFN), the PCB is often the primary thermal path.

- Standard 1 oz copper (35 um) has limited thermal conductivity for high-power components.
- 2 oz or heavier copper is recommended for power stages.
- The effective thermal resistance depends on trace width, thickness, length, and the total connected copper area.

### 6.2 Thermal Vias

Thermal vias connect component pads to inner copper planes or backside copper for improved heat spreading.

**Design rules for thermal via arrays:**
- Place a dense array of vias under the thermal pad of QFN/DFN/LGA packages.
- Via thermal resistance (approximate):
  ```
  Rth_via_array ~ L / (k_copper * A_copper * N)
  ```
  where L = board thickness, k_copper = 385 W/m-K, A_copper = copper cross-section per via, N = number of vias.
- Typical via parameters: 12 mil drill, 1 oz plating, 0.7 mil copper wall thickness.
- Via pitch: 40-50 mil is typical; tighter pitch improves thermal performance but may conflict with manufacturing limits.
- Fill or plug vias under BGA/LGA pads to prevent solder wicking during reflow (see Section 11).

### 6.3 GaN Chip-Scale Package Thermal Paths

GaN chip-scale packages have two effective cooling paths:

1. **Down through solder bumps to PCB (RthJB):** Heat flows through BGA/LGA solder connections into copper traces and thermal vias. May conflict with optimal power loop layout if thermal vias are placed in the switching loop area.
2. **Up through die substrate (RthJC):** With the active side facing down, the top of the die (silicon substrate) can be cooled with a heatsink and TIM. This is the most effective cooling strategy because it does not interfere with the PCB power loop layout.

**TIM selection for top-side heatsinking:**
- Must provide electrical isolation if heatsink is shared across multiple devices.
- Thermal conductivity ranges from 0.9 W/m-K (basic pad) to 12 W/m-K (premium soft silicone).
- For a 10 mm2 device, thermal resistance ranges from 8 to 400+ C/W depending on TIM.

### 6.4 Component Placement for Thermal

- Separate heat-generating components from temperature-sensitive ones, especially electrolytic capacitors.
- Place electrolytic capacitors in the coolest area of the board.
- Avoid placing capacitors directly above hot components in a vertical convection flow path.
- Use polished aluminum foil as a radiation shield between hot components and sensitive ones. Matt finish of any color has emissivity of approximately 0.8 in the IR range; color does not matter.
- Ensure adequate airflow paths in the enclosure design. In forced-air systems, place hot components in the exhaust stream and direct airflow to prevent static air pockets.

### 6.5 Mutual Heating

In compact designs, transistor and inductor/transformer temperatures are thermally coupled. Include inductor losses in the thermal model of nearby semiconductors. Transient thermal impedance at switching frequencies above 100 kHz converges to the duty cycle, so average power can be used for steady-state thermal calculations.

---

## 7. Creepage and Clearance

### 7.1 Definitions

- **Clearance:** The shortest distance through air between two conductive parts.
- **Creepage:** The shortest distance along the surface of the insulating material between two conductive parts.

Creepage is always greater than or equal to clearance. Creepage matters more at high voltages because surface tracking (carbon deposits from contamination) can create conductive paths along the PCB surface.

### 7.2 IPC-2221 Minimum Spacing

For coated assemblies (conformal coating), internal conductors, and sea-level operation:

| Voltage (DC or peak AC) | Minimum Spacing (mil) | Minimum Spacing (mm) |
|---|---|---|
| 0-15 V | 5 | 0.13 |
| 16-30 V | 10 | 0.25 |
| 31-50 V | 15 | 0.38 |
| 51-100 V | 25 | 0.64 |
| 101-150 V | 50 | 1.27 |
| 151-170 V | 75 | 1.91 |
| 171-250 V | 100 | 2.54 |
| 251-500 V | 150 | 3.81 |
| 501-750 V | 200 | 5.08 |
| 751-1000 V | 300 | 7.62 |

For uncoated assemblies at sea level, spacing requirements are approximately 2x the coated values above 100 V.

### 7.3 IEC 62368-1 Safety Standard (Replaces IEC 60950-1)

For isolated power supplies, creepage and clearance depend on:

| Factor | Categories |
|---|---|
| Insulation type | MAS 1.0 enum: `functional`, `basic`, `supplementary`, `reinforced`, `doubleInsulation` |
| Pollution degree | MAS 1.0 enum: `PD1` (sealed), `PD2` (normal indoor), `PD3` (conductive pollution), `PD4` (persistent conductivity) |
| Material group | MAS 1.0 enum: `groupI` (CTI >= 600), `groupII` (400 <= CTI < 600), `groupIIIA` (175 <= CTI < 400), `groupIIIB` (CTI < 175) |
| Overvoltage category | MAS 1.0 enum: `I`, `II`, `III`, `IV` (bare Roman numerals; no `OVC-` prefix) |
| Altitude | Correction factors for > 2000 m |

**Typical creepage for reinforced insulation (PD2, Material Group groupIIIB, sea level):**

| Working Voltage (RMS) | Creepage (mm) |
|---|---|
| 120 V | 5.0 |
| 240 V | 10.0 |
| 400 V | 16.0 |

**Clearance for reinforced insulation** is based on the peak working voltage plus any transient overvoltage (e.g., mains surge per overvoltage category). For 240 VAC mains with overvoltage category II:
- Peak working voltage: 340 V
- Transient voltage: 2500 V (category II)
- Required clearance: approximately 5.0 mm

### 7.4 Practical Layout Rules

1. **Mark isolation boundaries on the PCB.** Draw a keep-out zone (slot or routed groove) between primary and secondary sides of isolated converters. This groove increases both creepage and clearance.
2. **Route slots under isolation transformers and optocouplers** to increase creepage distance.
3. **No copper pour across isolation boundaries.** Remove all copper from the isolation gap, including inner layers.
4. **Use wider spacing for high-voltage switching nodes** (drain of top-side FET in a half-bridge), even within the primary side. dv/dt transients can arc across narrow gaps.
5. **Altitude derating:** At altitudes above 2000 m, the dielectric strength of air decreases. Multiply clearance requirements by a correction factor (approximately 1.14x at 3000 m, 1.29x at 4000 m, 1.48x at 5000 m).

---

## 8. EMC-Aware Layout

### 8.1 Minimizing the Switching Loop (Primary EMI Source)

The switching loop is the primary source of both conducted DM noise and radiated emissions.

1. Place MOSFET(s), diode/synchronous rectifier, and input decoupling capacitor in the tightest possible loop.
2. Use adjacent copper layers for send and return currents to achieve magnetic field cancellation.
3. Minimize via count in the power loop.
4. For half-bridges, the bootstrap capacitor loop is equally critical.
5. The loop area directly determines radiated emissions:
   ```
   L_loop ~ mu_0 * Area / perimeter
   E_radiated ~ f^2 * I * Area
   ```

### 8.2 EMI Filter Placement

Filter component placement rules derived from Wang's parasitic coupling analysis:

1. **Separate input and output filter capacitors.** Even 3% coupling coefficient between capacitor branches degrades the filter by more than 10 dB at high frequencies.
2. **Orient filter inductors at 90 degrees to each other** when multiple filter stages are used. Mutual inductance drops from 1.79 uH (aligned) to near-zero (orthogonal).
3. **Keep capacitor leads short.** ESL of 14 nH is already significant; PCB trace length adds more.
4. **Place magnetic shielding between closely spaced filter inductors.** 3 mil nickel foil is effective.
5. **Minimize the area enclosed by capacitor branch traces.** This area determines susceptibility to inductive coupling from the filter inductor.
6. **Mount filter inductors on the opposite PCB side from filter capacitors** when possible. Distance and ground plane shielding reduce coupling.

### 8.3 Shielding Strategies

- **Nickel foil (3 mil) around film capacitors:** 6 dB improvement in filter insertion loss from 1-30 MHz.
- **Magnetic shield plate between capacitors:** Additional 10 dB improvement.
- **Guard traces:** Grounded traces between sensitive signal lines and noise sources.
- **Faraday shield in transformers:** Reduces CM noise coupling from primary to secondary.
- **Heatsink bonding:** Bond heatsinks to ground at HF with a bypass capacitor from drain to heatsink to prevent heatsink radiation.

### 8.4 Snubber and Clamp Placement

RC snubbers across switching nodes reduce dv/dt ringing, which is often the dominant source of radiated emission in the 30-200 MHz range. Place snubber components as close to the switching node as possible with the shortest possible loop to the decoupling point.

---

## 9. Signal Integrity for Power Electronics

### 9.1 Feedback Network Placement

- Place the voltage divider for the feedback network as close as possible to the controller IC, not at the output capacitor. Route the sense trace from the output as a Kelvin connection (dedicated trace pair directly from the output capacitor terminal).
- Keep the compensation network (Rcomp, Ccomp) immediately adjacent to the controller error amplifier pins.
- Route the feedback trace away from switching nodes and power inductors. Use the ground plane as a shield between the feedback trace and the power stage.

### 9.2 Current Sense Resistor Placement and Routing

- Place the current sense resistor in the power path with a Kelvin connection: two dedicated sense traces routed as a differential pair from the resistor pads to the controller sense pins.
- Route both sense traces together on the same layer, parallel and adjacent, to ensure equal common-mode noise pickup that the differential amplifier will reject.
- Do not route sense traces through or near the switching loop area.
- Keep sense traces short and away from high dv/dt nodes.

### 9.3 Voltage Sense Traces

- Use dedicated Kelvin sense traces from the point of regulation (output capacitor or load terminals) back to the controller.
- Do not share power copper with sense connections. The IR drop in shared copper creates a DC offset error.
- For remote sense, route the sense pair as a twisted or closely coupled differential pair.

### 9.4 Oscilloscope Probing Considerations

When designing the PCB, include probe points for debugging:

- **Gate-source voltage:** Provide a pair of small pads (50 mil pitch) at the gate and Kelvin source for a differential probe or probe tip ground spring.
- **Drain-source voltage:** Provide pads at drain and source.
- **Switch node:** Include a test pad at the switching node with a nearby ground pad.
- **Current measurement:** Include a footprint for a coaxial shunt resistor or Rogowski coil access point in the power loop.

For GaN circuits, standard probe ground clips add 5-15 nH of inductance that creates measurement artifacts. Design in solder-in probe points with ground springs.

---

## 10. GaN/SiC Specific Layout

### 10.1 High dv/dt Considerations

GaN transistors switch in 1-5 ns, producing dv/dt rates of 50-200 V/ns. At these rates:

- Parasitic capacitances of even a few pF inject significant displacement currents (I = C * dv/dt). A 2 pF parasitic with 100 V/ns dv/dt produces 200 mA of transient current.
- Ground bounce from even 100 pH of shared inductance creates millivolts to volts of noise.
- Probe artifacts can be larger than actual signals if proper high-bandwidth measurement techniques are not used (minimum 500 MHz probe bandwidth; use active or differential probes).

### 10.2 Kelvin Source Connection

Devices with a dedicated Kelvin source pin allow the gate loop and power loop to share the source connection with negligible common-source inductance. This is the most important single feature for high-performance GaN layouts.

**Layout rule:** Connect the gate driver return exclusively to the Kelvin source pin. Do not connect the Kelvin source to the power ground at any other point.

For LGA/BGA devices without a Kelvin source pin, allocate the source pads closest to the gate as the gate-return path. Keep the gate loop current path physically separate from the power loop current path through the source connection.

### 10.3 Via-in-Pad

Chip-scale GaN packages (LGA and BGA) require via-in-pad to route signals and power through the PCB beneath the device.

**Requirements:**
- Vias must be filled (copper-filled or epoxy-filled) and planarized to provide a flat surface for solder attachment.
- Unfilled vias under BGA/LGA pads cause solder wicking during reflow, resulting in insufficient solder on the pad and poor joints.
- Via diameter: typically 8-12 mil drill for signal pads, 12-16 mil for power/thermal pads.
- Via fill specification must be communicated to the PCB fabricator as a separate requirement.

### 10.4 Power Loop Priority for GaN

The three critical loops, in priority order:

1. **Common-source inductance (CSI):** Must be below 50 pH. Achieved with dedicated gate-return source connections.
2. **Power loop inductance:** Must be below 0.5 nH for high-performance designs. Use the optimal power loop layout with shield plane on the first inner layer.
3. **Gate loop inductance:** Must be below 1 nH. Achieved by placing the gate driver immediately adjacent to the device.

### 10.5 Paralleling GaN Devices

When paralleling GaN transistors:

1. **CSI symmetry is paramount.** All devices must see identical common-source inductance. This is more important than power loop matching.
2. **Individual gate resistors per device** (both pull-up and pull-down) for independent speed tuning.
3. **Dedicated gate-return plane** on a full inner layer, not connected to power ground.
4. **Maximum 4 devices in a row** before mirroring the layout around another axis.
5. **Parallel complete half-bridge loops** rather than individual devices when possible. Each loop is self-contained with its own input capacitor. Low-frequency currents share through wider bus connections; high-frequency loop currents are contained independently.

### 10.6 SiC-Specific Notes

SiC MOSFETs operate at higher voltages (650-1700 V) with switching speeds between Si and GaN. Key layout differences from GaN:

- SiC packages (TO-247, D2PAK) have higher package inductance than chip-scale GaN. The package itself often limits achievable loop inductance.
- Gate drive voltage is typically 15-18 V with a wider margin than GaN (absolute max usually +25 V / -10 V). Layout is less forgiving of gate overshoot.
- SiC benefits from negative gate drive voltage (-2 to -5 V) to prevent parasitic turn-on, unlike e-mode GaN where negative gate drive is not recommended.
- Higher voltage operation demands larger creepage and clearance spacing.
- Kelvin source packages (e.g., TO-247-4) are available and strongly recommended for the same CSI reasons as GaN.

---

## 11. Manufacturing Considerations

### 11.1 Via Specifications

| Parameter | Standard | HDI / Fine-pitch |
|---|---|---|
| Minimum drill diameter | 10-12 mil (0.25-0.3 mm) | 4-6 mil (0.1-0.15 mm, laser drill) |
| Annular ring (pad to drill) | 5 mil minimum (0.13 mm) | 3 mil minimum (0.075 mm) |
| Via-to-via pitch | 40-50 mil (1.0-1.25 mm) | 20-30 mil (0.5-0.75 mm) |
| Via-to-trace clearance | 8-10 mil (0.2-0.25 mm) | 5 mil (0.13 mm) |

- **Via plating thickness:** Standard electroplating deposits 0.7-1.0 mil (18-25 um) of copper on via walls.
- **Filled vias:** Required for via-in-pad designs. Specify copper-filled or conductive-epoxy-filled vias. Non-conductive epoxy fill is acceptable if the via is capped and plated over.
- **Plugged vias:** A lower-cost alternative to filled vias where solder mask is used to tent or plug the via. Not suitable for BGA/LGA pad connections.

### 11.2 Solder Paste and Stencil

- **Stencil thickness:** 4-5 mil (0.1-0.12 mm) for standard SMD components. Reduce to 3-4 mil for fine-pitch (0.4-0.5 mm pitch) components.
- **Aperture reduction:** For large thermal pads, use a stencil aperture that is 50-80% of the pad area, divided into a grid of smaller openings. This prevents excess solder that causes the component to float or tilt during reflow.
- **Solder paste volume for chip-scale GaN:** Follow the device manufacturer's recommended stencil design. Too much solder causes shorts between adjacent pads; too little causes opens and high thermal resistance.

### 11.3 Thermal Relief

- **Power connections:** Use solid (no thermal relief) pad connections to power planes and pours. Thermal relief patterns restrict current flow and heat transfer.
- **Signal connections:** Use thermal relief (2 or 4 spoke pattern) on signal pads connected to large copper pours to enable reliable hand soldering and rework.
- **Ground pad connections:** For through-hole components on a ground plane, use thermal relief to allow solder to flow during wave soldering or hand soldering.

### 11.4 DFM Rules Summary

| Rule | Minimum Value |
|---|---|
| Trace width (outer layer) | 5 mil (0.13 mm) standard; 3.5 mil fine-line |
| Trace spacing (outer layer) | 5 mil (0.13 mm) standard; 3.5 mil fine-line |
| Pad-to-pad spacing | 5 mil (0.13 mm) |
| Solder mask dam between pads | 4 mil (0.1 mm) |
| Copper to board edge | 10-15 mil (0.25-0.38 mm) |
| Minimum through-hole drill | 10 mil (0.25 mm) |
| Board thickness tolerance | +/- 10% |

These are typical capabilities of mid-tier PCB fabricators. Always confirm design rules with the specific fabrication house before finalizing the layout. High-density designs (laser vias, sequential lamination, embedded components) require advanced fabrication capabilities and should be discussed with the vendor early in the design process.

### 11.5 Panelization and Test Points

- Include fiducial marks for automated pick-and-place alignment (minimum 3 fiducials per panel).
- Provide bed-of-nails test points for in-circuit test (ICT) if required. Test points should be on one side of the board (typically bottom) with 50 mil minimum pitch.
- Allow adequate panel borders (5 mm minimum) for handling by assembly equipment.

## Comprehensive PCB EMC Design (from Montrose, 2nd ed)

Source: Mark I. Montrose, "Printed Circuit Board Design Techniques for EMC Compliance," 2nd edition, IEEE Press, 2000/2008. 340 pages, 8 chapters + appendices. THE dedicated PCB-for-EMC reference.

### 12.1 Hidden RF Characteristics of Passive Components (Ch2)

At high frequencies, passive components change their behavior:
- A **resistor** acts as a series inductor (from leads) in parallel with a capacitor (between terminals)
- A **capacitor** acts as an inductor above its self-resonant frequency, due to lead inductance
- An **inductor** develops parasitic capacitance between windings and terminals

Key insight: parasitic capacitance exists between ANY two conductors separated by a dielectric (even air). Component lead-bond wires inside IC packages can be long enough to create significant RF potentials.

Design rule: never select passive components based solely on DC or low-frequency behavior. Always verify behavior at the actual operating frequency and its harmonics.

### 12.2 How RF Energy Develops in PCBs (Ch2)

Maxwell simplified via Ohm's law: V_rf = I_rf * Z. If RF current flows through a trace with impedance Z, an RF voltage is created. At frequencies above a few kHz, the impedance is dominated by inductive reactance (j*2*pi*f*L), not resistance.

Key numbers:
- Impedance of free space = 377 ohms
- At frequencies between 100 kHz and 1 MHz, very little trace inductance is needed to exceed 377 ohms
- When the return path impedance exceeds 377 ohms, free space becomes the return path => observed as radiated EMI
- PCB trace inductance: approximately 12-20 nH per inch (varies with width and thickness)

Fundamental rule: for every signal trace, a low-impedance return path MUST exist. If a conductive return path is absent, free space becomes the return path, creating radiated emissions.

### 12.3 Magnetic Flux Cancellation (Ch2)

When RF current travels through a trace, magnetic flux encircles it. To cancel this flux:
- Place the return path physically adjacent and parallel to the source trace
- The clockwise field of the return path cancels the counterclockwise field of the source path
- If flux is canceled, RF radiation cannot occur except within the minuscule boundary of the transmission line

Implementation techniques:
- Use image planes (solid ground or power planes adjacent to signal layers)
- Route clock traces adjacent to ground planes, ground grids, or guard traces
- Reduce RF drive voltage where possible (e.g., TTL vs CMOS voltage swing)

### 12.4 Layer Stackup Assignments (Ch2)

**Fundamental rule**: every signal routing layer must be adjacent to a reference (image) plane. The outer microstrip layer must contain only slower-speed traces.

**Where 3+ reference planes exist**: route high-speed traces adjacent to 0V-reference (ground) planes rather than power planes. Ground planes are more stable because they are tied to chassis ground and do not modulate with switching currents.

**Layer 2 should be ground** (not power): a ground plane as the first internal layer reduces parasitic capacitive coupling to the enclosure, providing enhanced RF suppression.

#### 12.4.1 Single-sided PCBs
- Reserve for circuits below a few hundred kHz
- Use radial routing: all power/ground traces emanate from a single source location
- Route all power and ground traces adjacent (parallel) to each other
- Never connect different branches of a radial tree to create loops
- Route high-threat signals adjacent to ground traces

#### 12.4.2 Double-sided PCBs
- Standard 0.062 in (1.6 mm) thickness is too large for effective flux cancellation between top and bottom
- Treat as two single-sided designs
- Use gridded power and ground with loop area per grid square not exceeding 1.5 sq in (3.8 sq cm)
- Run power traces on one layer, ground on other, at 90 degrees to each other
- Use ground fill on unused areas, connected to 0V-reference at as many points as possible

#### 12.4.3 Four-layer stackup
Primary configuration: S1 / Ground / Power / S2
- Signal traces still far from reference planes (moderate flux cancellation)
- Impedance approximately 105-130 ohms (symmetrical spacing)
- RF return currents on the layer adjacent to power plane need a ground trace routed alongside

Alternate: Ground / S1 / S2 / Power (planes as outer layers)
- Stripline routing prevents radiation but component radiation still occurs
- Difficult to manufacture (planes act as heatsink during wave solder)
- Impossible to repair/debug

#### 12.4.4 Six-layer stackup configurations

**Config 1** (4 routing, 2 planes): S1/S2/Ground/Power/S3/S4
- Layer adjacent to ground (S2) preferred for high-threat signals
- Typical impedance (10 mil spacing, er=4.3): outer microstrip ~90-110 ohm, embedded microstrip ~60-79 ohm

**Config 2** (4 routing, 2 planes): S1/Ground/S2/S3/Power/S4
- Good for signal integrity (lower impedance, closer to planes)
- Stripline layers: ~52-68 ohm, outer microstrip: ~65-84 ohm
- Practically no planar decoupling (power/ground far apart)

**Config 3** (3 routing, 3 planes): S1/Power/Ground/S2/Ground/S3
- Optimal for high-threat signals on S2 (coaxial structure between two ground planes)
- Stripline impedance: ~44-59 ohm

#### 12.4.5 Eight-layer stackup
**Config 1** (6 routing, 2 planes): S1/S2/Ground/S3/S4/Power/S5/S6
- Poor decoupling; route clocks on S2 and S3 (adjacent to ground plane)
- Impedance: outer microstrip ~79-99 ohm, embedded ~50-68 ohm, stripline ~43-58 ohm

**Config 2** (4 routing, 4 planes): S1/Ground/S2/Ground/Power/S3/Ground/S4
- Excellent decoupling and flux cancellation
- Best configuration for signal integrity and EMC
- S2 and S3 have nearly matched impedance (~35-54 ohm)

#### 12.4.6 Ten-layer stackup
Two sample configurations with 6 routing / 4 reference planes. Outer microstrip: ~69-99 ohm, embedded: ~41-68 ohm, stripline: ~35-58 ohm depending on configuration.

Key principle for 10+ layers: locate power and ground planes as a pair in the center to create a coaxial structure for the innermost routing layers.

### 12.5 RF Current Density Distribution (Ch2)

Peak current density in the return plane lies directly beneath the signal trace and falls off sharply. The current spreads approximately one trace width away from the centerline.

Design rule: if an adjacent trace is closer than one trace width, flux coupling (crosstalk) will occur. This is the physical basis for the 3-W rule (Section 12.10).

### 12.6 Grounding Methodologies (Ch2)

- **Single-point grounding**: use only below 1 MHz (audio, analog instrumentation, DC power)
- **Multipoint grounding**: required above 1 MHz; minimizes ground impedance by shunting RF currents from planes to chassis ground
- Copper plane impedance (10x10 in plane): 0.00026 ohm at 1 MHz, 0.0026 ohm at 100 MHz, 0.0082 ohm at 1 GHz

Ground stitch spacing (multipoint to chassis): distance between ground connections must not exceed lambda/20 of the highest frequency or harmonic of concern.

Example: for a 64 MHz oscillator, lambda/20 = 9.2 in (23.4 cm). If straight-line distance between ground stitches exceeds this, an efficient RF loop antenna exists.

### 12.7 Image Planes (Ch2)

An image plane is a solid copper layer (power or ground) adjacent to a signal routing layer. Functions:
- Provides low-impedance RF return path (flux cancellation)
- Reduces ground-noise voltage
- RF return coupling approaches 100% but never reaches it (physical spacing)

Critical rules:
- No traces or routes within an image plane (splits it, creates loops)
- Vias do not degrade imaging EXCEPT where continuous slots form
- Skin effect: above 30 MHz, current flows only in the first skin depth of copper (e.g., 0.0000066 in at 100 MHz); RF current cannot penetrate 1 oz copper
- A second reference plane adjacent to an existing one provides decoupling but no additional EMI reduction (skin effect prevents current penetration)

### 12.8 Slots in Image Planes -- Why They Are Deadly (Ch2)

Through-hole components create the "Swiss cheese syndrome": overlapping drill holes remove copper from planes, creating discontinuous slots.

Effects:
- RF return current cannot mirror-image the signal trace across the slot
- Return current must travel around the slot, creating a loop antenna and magnetic field
- Additional inductance in return path reduces flux cancellation
- Performance improvement of up to 20 dB has been observed when capacitors bridge slots

Design rules:
- When routing between through-hole pins, maintain at least 3x trace width clearance from through-hole locations
- Use capacitors to bridge moats or slots (AC shunt for RF currents)
- A continuous image plane is always preferred over split planes
- If multiple signals cross a split simultaneously, significant common-mode energy develops

### 12.9 Functional Partitioning (Ch2)

Group components by functional subsection to:
- Minimize signal trace lengths and loop areas
- Prevent RF coupling between different bandwidth areas
- Separate high-bandwidth (CPU, clocks) from low-bandwidth (I/O) areas

Partitioning rules:
- Products with clocks above 50 MHz generally require frequent ground stitch connections to chassis ground
- At least four ground points should surround each functional section
- Chassis bond connections on both ends of DC power connectors
- Use decoupling capacitors to reduce coupling of power-supply RF currents into signal lines

### 12.10 Critical Frequency lambda/20 Rule (Ch2)

An efficient antenna can exist with dimensions down to lambda/20 of the highest frequency or harmonic. Use this to calculate:
- Maximum ground stitch spacing
- Maximum unterminated trace lengths
- Guard band intervals

Reference table (lambda/20 wavelength distances):
| Frequency | lambda | lambda/20 |
|-----------|--------|-----------|
| 10 MHz | 30 m | 1.5 m (5 ft) |
| 50 MHz | 6 m | 0.3 m (12 in) |
| 100 MHz | 3 m | 15 cm (6 in) |
| 200 MHz | 1.5 m | 7.5 cm (3 in) |
| 400 MHz | 75 cm | 3.75 cm (1.5 in) |
| 600 MHz | 50 cm | 2.5 cm (1 in) |
| 1000 MHz | 30 cm | 1.5 cm (0.6 in) |

### 12.11 Logic Family Selection and Edge Rates (Ch2)

The greatest contributor to RF energy is the edge rate transition, NOT the operating frequency. A 5 MHz oscillator driving a 74F04 (1 ns edge) generates more RF spectral energy than a 50 MHz oscillator driving a 74ALS04 (4 ns edge).

Key rules:
- Use the slowest logic family that meets timing requirements
- Devices with edge times > 5 ns preferred when timing permits
- Minimum edge rate (not published by most manufacturers) determines worst-case EMI
- Peak inrush surge current during switching can be 10x quiescent levels

Reference edge rates and EMI bandwidth:
| Logic Family | Edge Rate | Principal Harmonic | EMI Observed To |
|---|---|---|---|
| 74HC | 13-15 ns | 24 MHz | 240 MHz |
| 74LS | 9.5 ns | 34 MHz | 340 MHz |
| 74HCT | 5-15 ns | 64 MHz | 640 MHz |
| 74ALS | 2-10 ns | 160 MHz | 1.6 GHz |
| 74ACT | 2-5 ns | 160 MHz | 1.6 GHz |
| 74F | 1.5 ns | 212 MHz | 2.1 GHz |
| ECL 100K | 0.75 ns | 424 MHz | 4.2 GHz |
| LVDS | 0.3 ns | 1.1 GHz | 11 GHz |

### 12.12 Bypassing and Decoupling (Ch3)

Three distinct uses of capacitors:
1. **Decoupling**: removes RF energy from power distribution network during component switching
2. **Bypassing**: diverts common-mode RF noise from coupling between areas (AC shunt)
3. **Bulk**: maintains DC voltage/current during simultaneous switching under max load

#### 12.12.1 Capacitor Self-Resonant Frequencies

Below self-resonance: capacitive. Above self-resonance: inductive (useless for decoupling).

| Capacitor Value | Through-Hole (0.25 in leads) | Surface Mount (0805) |
|---|---|---|
| 1.0 uF | 2.6 MHz | 5 MHz |
| 0.1 uF | 8.2 MHz | 16 MHz |
| 0.01 uF | 26 MHz | 50 MHz |
| 1000 pF | 82 MHz | 159 MHz |
| 100 pF | 260 MHz | 503 MHz |
| 10 pF | 821 MHz | 1.6 GHz |

Through-hole: ~2.5 nH per 0.10 in lead length. Surface mount: ~1 nH total.

#### 12.12.2 Anti-Resonance from Parallel Capacitors

CAUTION: Two capacitors in parallel create an anti-resonant peak between their self-resonant frequencies. At this anti-resonant frequency, impedance is HIGHER than either capacitor alone.

Example: 0.01 uF (resonant at 14.85 MHz) in parallel with 100 pF (resonant at 148.5 MHz) creates anti-resonance at ~110 MHz with high impedance. If a clock harmonic falls at this frequency, the board becomes an unintentional transmitter.

Rule: when using parallel capacitors, values must differ by at least 100x (two orders of magnitude) to push the anti-resonant frequency away from clock harmonics.

Above the anti-resonant frequency, parallel capacitors provide only ~6 dB improvement over a single capacitor.

#### 12.12.3 Power and Ground Plane Capacitance

Two parallel copper planes separated by dielectric form a built-in capacitor:
- C_pp = k * (epsilon_r * A) / d, where k = 0.2249 (in inches), A = plate area, d = spacing
- For 10 sq in board, 1 mil spacing, FR-4 (er=4.5): C = 45 nF
- Typical buried capacitance: 506 pF/sq in
- Effective decoupling up to 200-300 MHz when spacing < 0.010 in (0.25 mm), preferred 0.005 in (0.13 mm)
- PCBs generally self-resonate at 200-400 MHz

WARNING: if the self-resonant frequency of all discrete capacitors matches the PCB plane resonance, a sharp combined resonance with no decoupling results. Change plane spacing or add capacitors with different resonant frequencies.

#### 12.12.4 Capacitor Placement Rules

- Decoupling loop impedance MUST be much lower than rest of power distribution system
- Minimize lead inductance: this is the single most important parameter
- Route capacitor directly to power/ground planes via vias -- do NOT run traces between capacitor and component
- Inductance budget: pair of surface traces = 10-15 nH/in; pair of vias = 0.4-1 nH; plane inductance = 0.1 nH
- Place via inside SMT pad for best performance (microvia technology)
- Place 1 nF capacitors on a 1-inch grid for additional high-frequency decoupling
- For edge rates < 2 ns: provide decoupling for EVERY component
- Total ESL of decoupling circuit must stay below 10 nH (challenging with traces)

#### 12.12.5 Dielectric Material Selection

- Z5U (barium titanate): high dielectric constant, self-resonant 1-20 MHz, effective to ~50 MHz. Best for low-frequency decoupling
- NPO (strontium titanate): low dielectric constant, better high-frequency performance, more temperature stable. Unsuitable below 10 MHz
- CAUTION: Z5U in parallel with NPO can damp the NPO resonance. For problems below 50 MHz, use only Z5U

#### 12.12.6 Calculating Bypass Capacitor Value

For a transient surge:
- C = (delta_I * delta_t) / delta_V
- Example: 74HC with 20 mA surge for 10 ns, 100 mV max drop => C = 2000 pF

Maximum series inductance for a given noise spike:
- L = (V * delta_t) / delta_I
- Example: 20 mA, 2 ns edge, 100 mV spike => L_max = 10 nH

#### 12.12.7 Bulk Capacitor Placement

One bulk capacitor per two LSI/VLSI components, plus at:
- Power entry connectors
- Power terminals on daughter cards/peripherals
- Furthest location from input power connector
- Adjacent to clock generation circuits
- Voltage rating: nominal voltage = 50% of capacitor rating (e.g., use 10V rated for 5V rail)
- Typical bulk values: 4.7-100 uF

### 12.13 Transmission Lines, Impedance, and Termination (Ch4)

#### 12.13.1 Microstrip vs Stripline

**Microstrip** (outer layers):
- Faster propagation: 1.68 ns/ft (140 ps/in) for FR-4 (er=4.3)
- Less capacitive coupling (one side is air)
- Can radiate RF energy to environment
- Less distributed capacitance

**Stripline** (internal layers):
- Slower propagation: 2.11 ns/ft (176 ps/in) for FR-4
- Complete shielding of RF energy between reference planes
- Enhanced noise immunity
- Higher capacitive loading

Impedance sensitivity:
- Trace thickness change: ~2 ohm/mil (0.5-1 oz copper)
- Soldermask effect: ~3 ohm/mil (reduces microstrip impedance by 0.5-1 ohm per mil of coating)
- Side wall etch: ~1% impedance change

#### 12.13.2 Propagation Delay

| Topology | Velocity (FR-4, er=4.3) |
|---|---|
| Microstrip | 1.68 ns/ft (140 ps/in, 55 ps/cm) |
| Embedded microstrip / Stripline | 2.11 ns/ft (176 ps/in, 69 ps/cm) |

#### 12.13.3 Electrically Long Traces

A trace is electrically long when 2 * propagation_delay * length >= edge_rate.

Maximum unterminated trace length:
- Microstrip: l_max = 9 * tr (cm), or 3.49 * tr (inches), where tr in ns
- Stripline: l_max = 7 * tr (cm), or 2.75 * tr (inches)

Example: 2 ns edge rate => max unterminated microstrip = 18 cm (7 in), stripline = 14 cm (5.5 in)

If trace exceeds l_max, termination is required.

#### 12.13.4 Capacitive Loading Effects

Adding load capacitance to a trace:
- Slows propagation: t'pd = tpd * sqrt(1 + Cd/Co)
- Lowers impedance: Z'o = Zo / sqrt(1 + Cd/Co)
- Typical input capacitance: ECL ~5 pF, CMOS ~10 pF, TTL ~10-15 pF per device
- Vias add ~0.3-0.8 pF each; sockets ~2 pF each
- Typical trace capacitance: 2-2.5 pF/inch

#### 12.13.5 Component Placement for Clock Circuits

- Clock circuits near center of board and/or ground stitch location, NOT near I/O
- Never use sockets for oscillators (sockets add inductance => common-mode energy)
- Only clock-related traces in the clock generation area; no other traces near, under, or through
- Allow for possible Faraday shield (metal enclosure) around clock circuits
- Vias add 1-3 nH each to trace route
- Clock traces within 2 in (5 cm) of I/O: edge rates must be slower than 10 ns
- Clock traces within 3 in (7.6 cm) of I/O: edge rates between 5-10 ns

#### 12.13.6 Termination Methods

| Method | Parts | Power | Best For |
|---|---|---|---|
| Series (source) | 1 resistor (Rs = Zo - Ro) | Low | Single load at end |
| Parallel (end) | 1 resistor (R = Zo) | High | Multiple loads |
| Thevenin | 2 resistors (R = 2*Zo) | High | Bus logic |
| AC (RC) | R + C (R = Zo, C = 20-600 pF) | Medium | Clocks only (NOT data) |
| Diode | 2 diodes | Low | Limits overshoot |

Series termination: resistor directly at driver output pin, NO via between component and resistor. Typical values: 15-75 ohm (commonly 33 ohm).

T-stubs: avoid if possible. If required, both legs must be EXACTLY identical length. Max stub length = Ld/(2*tr).

### 12.14 Crosstalk and the 3-W Rule (Ch4)

#### 12.14.1 Crosstalk Mechanisms
- **Capacitive crosstalk**: trace above/below another trace; function of distance and overlap area
- **Inductive crosstalk**: traces in close proximity; from expanding/contracting magnetic fields
- **Backward crosstalk** (at source end of victim) is worse than **forward crosstalk** (at load end)

Crosstalk equation: Crosstalk = K * H^2 / D^2, where H = height above reference plane, D = center-to-center trace distance, K < 1 (depends on rise time and parallel length)

To minimize crosstalk: minimize H (bring traces closer to reference plane) and maximize D (separate traces).

#### 12.14.2 The 3-W Rule
- Distance between trace centerlines must be >= 3x trace width
- Equivalently, edge-to-edge spacing >= 2x trace width
- Example: 6-mil clock line => no trace within 12 mils edge-to-edge
- 3-W represents the ~70% flux boundary
- For ~98% flux boundary, use 10-W
- Mandatory only for high-threat signals: clocks, differential pairs, video, audio, reset, system-critical nets
- Differential pairs: 1-W between pair, 3-W from pair to adjacent traces
- Via keep-outs must also respect 3-W from high-threat traces

### 12.15 Guard and Shunt Traces (Ch4)

**Guard traces**: at 0V-potential, surround high-threat traces on the same layer.
**Shunt traces**: at 0V-potential, directly above/below high-threat traces on adjacent layer.

When guard traces work:
- On 1- or 2-layer boards (no solid reference planes)
- When trace-to-guard distance < trace-to-image-plane distance

When guard traces are USELESS:
- Stripline where H (trace-to-plane) < W (trace-to-guard spacing)
- The image plane captures flux before the guard trace does

Guard trace rules:
- Ground at both source and destination
- For long routes, add multiple ground vias along the guard trace at irregular intervals (avoid creating tuned circuits)
- Never route two unrelated signals between guard traces
- Width of shunt trace must be at least 3x signal trace width

### 12.16 Routing Layers and Layer Jumping (Ch4)

Rules for routing high-threat signals:
1. Route on one layer only if possible (x- and y-axis on same plane)
2. Always adjacent to solid reference plane with no discontinuities
3. Maintain constant impedance; minimize vias

When layer jumping is unavoidable:
- Place ground vias at EVERY layer jump location for clock/high-threat signals
- These ground vias connect all 0V-reference planes together at the jump point
- Alternative: share the ground pin via of a nearby component

Three EMI mechanisms from planes:
1. Image plane discontinuities from vias and layer jumps create loop antennas
2. Peak surge currents propagate through power/ground planes
3. Flux loss into via annular keep-out regions (respect 3-W from vias)

### 12.17 I/O and Moating Techniques (Ch5)

#### 12.17.1 I/O Partition Rules
- I/O generates as many EMI problems as clock signals
- All metal connectors: 360-degree solid bond to chassis ground (never pigtails)
- I/O logic as close to connector as possible
- Filter components between driver and connector
- Clock signals within 2 in of I/O must have edge rates > 10 ns

#### 12.17.2 Moating (Isolation)
Moating = absence of copper on all plane layers to isolate functional areas.

Implementation steps:
1. Partition PCB into analog, digital, and I/O regions
2. Isolate all plane layers with minimum 0.010 in (0.25 mm) wide moat
3. Tie analog and digital ground at ONE point only (the bridge)
4. Place analog portion of mixed-signal components exactly at the bridge
5. NO signals may cross the moat except through the bridge
6. Signals through the bridge must be on a layer adjacent to the bridge plane

Two methods:
- **Method 1**: Complete isolation using isolation transformers, optical isolators, or data line filters. Remove all copper between filter and I/O connector
- **Method 2**: Bridge (single break in moat). Ground both ends of bridge to chassis. Route all inter-area traces through the bridge only

#### 12.17.3 I/O Filtering
- Filter components must be EXACTLY at the connector entry point (1 inch may be too far)
- Capacitive bypass: 100 pF typical (larger values round signal edges too much)
- Ferrite beads/data line filters: optimal device for filtering (absorbs RF while passing DC)
- Ferrites effective only above ~10 MHz
- Filter order (from controller): controller -> data line filter -> bypass capacitor -> I/O connector
- For ESD-prone circuits: place bypass capacitor on controller side of data line filter (protects cap from ESD)

### 12.18 ESD Protection on PCBs (Ch6)

#### 12.18.1 ESD Fundamentals
- Rise times: 200 ps to >10 ns; peak currents: few amps to >30 A
- Spectral content: hundreds of MHz to beyond 1 GHz
- ESD at ~300 MHz equivalent frequency (1 ns rise time)

Four failure modes:
1. Direct current through vulnerable circuit pins
2. Current in ground circuit causing ground bounce
3. Electromagnetic field coupling (indirect discharge)
4. Pre-discharged static electric field (rare)

#### 12.18.2 ESD Design Rules for PCBs

**Single/double-sided boards**:
- Extremely vulnerable; ground impedance too high
- Provide auxiliary ground plane (aluminum-backed mylar) directly adjacent
- Can improve ESD threshold from 2 kV to 15 kV
- Grid power/ground connections at critical points

**Multilayer boards**:
- 10-100x improvement over 2-layer boards for indirect ESD
- Locate first ground plane as close to signal routing as possible

ESD protection components:
- High-voltage capacitors (1500V minimum rating) directly at I/O connector
- Avalanche diodes (Tranzorbs): fast clamping, short leads mandatory
- Ferrite beads: dual benefit -- emissions AND immunity
- Series resistors for CMOS inputs: up to 1 kohm acceptable

Circuit layout rules:
- Fill top and bottom layers with ground copper (ground fill)
- Connect transient protection devices to CHASSIS ground, not circuit ground
- Ground connections: width-to-length ratio of 5:1 or less (3:1 acceptable)
- Keep traces as short as possible; do not route critical signals near board edges

#### 12.18.3 Guard Band Implementation
- 1/8 in (3.2 mm) guard band around all edges, top and bottom layers
- Minimum 0.020 in (0.50 mm) from components/traces
- Connect top to bottom band with vias every 1/2 in (1.3 cm)
- NO soldermask on guard band
- Do NOT make guard band a complete circle (becomes loop antenna) -- break into segments
- In metal enclosure with multipoint ground: connect guard band to ground planes
- In plastic enclosure or single-point ground: do NOT connect to ground planes (energy has nowhere to go, will bounce and destroy components)

### 12.19 20-H Rule for Plane Edges (Ch8)

RF current radiates from PCB edges due to interplane coupling (fringing) between power and ground planes.

**Rule**: all power planes must be physically smaller than their nearest ground plane by 20*H, where H is the distance spacing between power and ground planes.

Example: H = 0.006 in between planes => power plane 0.120 in (3.0 mm) smaller on each edge.

Key details:
- 10-H: impedance change threshold first noticed
- 20-H: approximately 70% flux boundary
- 100-H: approximately 98% flux boundary (diminishing returns beyond 20-H)
- Any traces on adjacent routing layer over the absence-of-copper area MUST be rerouted inward
- Implement only in high-bandwidth areas (CPU, video, Ethernet, SCSI)
- NOT required when board physical dimensions are smaller than lambda/4 of all clock frequencies

The 20-H rule works because power and ground planes form z-axis transmission lines. Unterminated plane edges are "stubs" that create reflections and resonances. Undercutting the power plane removes these stubs.

### 12.20 Trace Corner Routing (Ch8)

#### 12.20.1 Time Domain (Signal Integrity)
- 90-degree corner: trace width increases to W*sqrt(2) at the corner
- Parasitic capacitance at corner: C = 0.014 pF for typical 7 mil trace (14 femtofarads!)
- Impedance discontinuity: ~15-20% decrease for only ~15 ps
- Only affects signals with edge rates faster than ~50 ps (>33 GHz range)
- For designs below mid-GHz range: 90-degree corners have negligible signal integrity impact

#### 12.20.2 Frequency Domain (EMI)
- Radiated emissions from right-angle corners: approximately +2 to +5 dB maximum
- Components driving the trace generate thousands of times more RF energy
- Stripline corners cannot radiate (captured between planes)
- Measurable effects only above 700-750 MHz

#### 12.20.3 Manufacturing -- The Real Reason
- Chemical etchant starts at corners, etching back the trace
- For 5 mil traces, finished width may drop to 3 mils (fusing risk, delamination)
- For 20 mil traces, etch-back is minimal
- Always use 45-degree chamfer or rounded corners for manufacturability
- 45-degree chamfer reduces corner capacitance by ~57%

### 12.21 Ferrite Selection for PCB Use (Ch8)

Three applications:
1. Shield (isolate from stray fields)
2. With capacitor, forms low-pass filter (LC at low freq, dissipative at high freq)
3. Lossy element on leads/traces (absorbs RF energy as heat)

Selection criteria:
- **Permeability 2500 mu**: effective at 30 MHz and below
- **Permeability 850 mu**: effective 25-250 MHz
- **Permeability 125 mu**: effective 200 MHz and above

Impedance reference (mu=850, 11.1x5.1x1.5 mm bead):
| Frequency | Impedance |
|---|---|
| 1 MHz | 14 ohm |
| 10 MHz | 66 ohm |
| 30 MHz | 110 ohm |
| 50 MHz | 115 ohm |

Key considerations:
- Elevated temperature decreases impedance
- DC bias current decreases impedance significantly (most critical parameter)
- Additional wire turns: impedance increases as turns^2 but bandwidth decreases
- Core with built-in airgap: better DC current handling

### 12.22 Grounded Heatsinks as Shields (Ch8)

For VLSI processors at 100 MHz+, metal heatsinks can become monotonic antennas radiating clock harmonics.

Solution: ground the heatsink to create three functions:
1. **Thermal**: remove heat
2. **Faraday shield**: prevent RF radiation from processor die
3. **Common-mode decoupling capacitor**: heatsink (ground) + thermal compound (dielectric) + component case (voltage) = capacitor that AC-couples common-mode RF to ground

Implementation:
- Bond heatsink to ground planes via fence (vertical bus bar)
- Fence ground posts on 1/4 in (6.4 mm) centers around processor
- Alternating parallel decoupling at each ground pin: 0.1 uF || 0.001 uF, and 0.01 uF || 100 pF
- RISC processors require multipoint grounding around all four sides

### 12.23 Creepage and Clearance (Ch8)

Safety-critical spacing requirements (IEC/EN harmonized standards):

| Working Voltage (Vrms) | Pollution Degree 2, Basic/Supp Clearance | Creepage (Mat. Group II) |
|---|---|---|
| 50V | 1.0 mm | 0.9 mm |
| 100V | 1.3 mm | 1.0 mm |
| 150V | 1.3-2.0 mm | 1.1 mm |
| 300V | 1.9-3.2 mm | 2.2 mm |
| 600V | 3.2 mm | 4.5 mm |

Notes:
- Creepage = shortest path along surface of insulation
- Clearance = shortest distance through air
- Reinforced insulation = 2x basic/supplementary values
- Pollution Degree 2 applies to most electronic equipment
- Values are minimums AFTER accounting for manufacturing tolerances

### 12.24 Current-Carrying Capacity of Copper Traces (Ch8)

Current capacity is based on cross-sectional area and allowable temperature rise. Conservative design rule: temperature rise should not exceed 10 degrees C above ambient.

Approximate current capacity (1 oz copper, 10 deg C rise, external layer):
| Trace Width | Current Capacity |
|---|---|
| 10 mil (0.25 mm) | ~0.5 A |
| 20 mil (0.50 mm) | ~1.0 A |
| 50 mil (1.27 mm) | ~2.0 A |
| 100 mil (2.54 mm) | ~3.5 A |
| 200 mil (5.08 mm) | ~6.0 A |

Internal traces carry approximately 50% of external trace current for the same temperature rise (less heat dissipation path).

### 12.25 Complete Design Checklist (from Appendix A)

This checklist consolidates the complete summary from Montrose's Appendix A. Every rule below is an actionable design requirement.

#### PCB Basics
1. Never assume passive components behave ideally at RF frequencies
2. Every signal must have a low-impedance return path (closed loop required)
3. Minimize distance between signal trace and its return path for flux cancellation
4. Use appropriate stackup topology (microstrip vs stripline) for the application
5. Route high-speed traces adjacent to 0V-reference planes (not power planes)
6. Ground plane as Layer 2 preferred for RF suppression
7. Each routing layer must be adjacent to a solid reference plane
8. Never place 3+ routing layers adjacent to each other
9. Never violate image planes with trace routes
10. Ground stitch spacing <= lambda/20 of highest frequency/harmonic
11. Use slowest logic family that meets timing requirements
12. Know the MINIMUM edge rate (not published) -- it determines EMI

#### Bypassing and Decoupling
13. Calculate capacitor values for the application -- do not blindly use 0.1 uF
14. Include lead inductance and ESR in self-resonant frequency calculations
15. Parallel capacitors must differ by 100x to avoid anti-resonance
16. Utilize power/ground plane capacitance (effective to 200-400 MHz)
17. Decoupling capacitors for every device with edges faster than 2 ns
18. Minimize decoupling loop area and lead inductance (most critical parameter)
19. Place 1 nF caps on 1-inch grid for supplementary high-frequency decoupling
20. Bulk capacitors at power entry, far corners, and high-current devices

#### Traces and Termination
21. Calculate trace impedance for each routing topology used
22. Determine if traces are electrically long (2*tpd*length >= tr)
23. Terminate all electrically long traces in their characteristic impedance
24. Never daisy-chain high-speed signals -- use radial connections
25. Avoid T-stubs; if unavoidable, legs must be exactly equal length
26. Apply 3-W rule to clocks, differential pairs, video, audio, reset
27. Route adjacent layers orthogonally to minimize capacitive coupling
28. Use ground vias at every layer jump for high-threat signals
29. Route clock traces on one layer only, manually, before autorouting

#### I/O and Interconnects
30. Bond all metal I/O connectors 360 degrees to chassis ground
31. Partition I/O from high-bandwidth areas using moats or bridges
32. Filter every I/O trace: ferrite beads preferred (effective for both emissions and immunity)
33. Filter components EXACTLY at connector entry point
34. Remove copper between data line filter and I/O connector
35. For analog/digital partitioning: one bridge only, with analog component centered on it

#### ESD Protection
36. Multilayer boards provide 10-100x improvement over 2-layer for indirect ESD
37. Connect transient protection devices to chassis ground, NOT circuit ground
38. Guard band on all edges: 1/8 in wide, vias every 1/2 in, no soldermask, NOT a complete loop
39. Ground fill on outer layers connected to chassis ground at frequent intervals
40. Ground connection width-to-length ratio: 5:1 maximum (3:1 preferred)

#### Additional Techniques
41. Localized ground planes under oscillators/clock circuits with vias to main ground
42. Apply 20-H rule to power planes in high-bandwidth sections
43. 90-degree corners: negligible EMI/SI impact below GHz; avoid for manufacturing reasons
44. Ferrite selection: match permeability to target frequency range
45. Ground heatsinks on high-speed processors (creates Faraday shield + CM decoupling cap)
46. Verify creepage and clearance distances per safety standards
47. Limit trace temperature rise to 10 deg C above ambient

---

## Ott PCB Layout and Grounding (from Henry Ott Ch10, 16, 17)

> Source: Ott, Henry W. "Electromagnetic Compatibility Engineering" (Wiley, 2009), Chapters 10,
> 12, 16, and 17. System-level PCB design for EMC: ground plane current distribution, return
> path analysis, layer stackup, and mixed-signal layout.

---

### O1. Digital Circuit Ground Noise (Ch10)

#### O1.1 Why Ground Inductance Matters

When a digital gate switches, transient current flows through the ground system. The noise
voltage generated is:

```
V_noise = L_ground * di/dt
```

Example: 50 nH ground inductance, 50 mA transient, 1 ns switching time -> V_noise = 2.5 V.
With 3.3 V supply, this is a major noise source. Digital bandwidth relates to rise time:

```
BW = 1 / (pi * t_r)
```

1 ns rise time = 318 MHz bandwidth. Sub-nanosecond rise times (LVDS: 300 ps) -> 1 GHz BW.

#### O1.2 Internal Noise Sources in Digital Logic

**Source 1 -- Capacitive discharge:** When output switches low, stray wiring capacitance
discharges through ground, creating ground bounce noise that couples to adjacent quiet gates.

**Source 2 -- Shoot-through current:** During switching, both pull-up and pull-down transistors
in a totem-pole output are briefly ON simultaneously, creating a 50-100 mA spike per gate.
Microprocessors can have > 10 A transient supply current.

**Two mandatory requirements for all digital circuits:**
1. Low-impedance (low-inductance) ground system
2. Local charge source (decoupling capacitors) near every IC

#### O1.3 Minimizing Ground Inductance

**PCB trace inductance:**

Round conductor over ground plane:
```
L = 0.005 * ln(4*h/d)    [uH/in]
```

Flat trace over ground plane:
```
L = 0.005 * ln(2*pi*h/w)    [uH/in]    (for h >> w)
```

Typical 6-mil trace, 20 mil above plane: L ~ 15 nH/in, R = 82 mohm/in.
At all frequencies above 1 MHz, inductive reactance exceeds resistance.
At 318 MHz (1 ns rise time): X_L ~ 30 ohm/inch.

**Inductance is proportional to conductor length** -- keep high-speed leads short.
**Inductance depends logarithmically on width** -- doubling width barely helps.
The dominant factor is **loop area** (trace height above return plane).

#### O1.4 Mutual Inductance Benefit

When two parallel ground conductors carry current in the same direction, mutual inductance
reduces the total loop inductance:

```
L_total = L1 + L2 - 2*M12
```

This is why a ground plane works: many parallel paths with high mutual coupling.
Multiple ground vias near an IC reduce effective ground inductance more than a single via.

#### O1.5 Practical Digital Ground Systems

**Two-layer boards:** Use a ground grid (not a single trace). A ground grid provides 10-12 dB
less emission than a board without one.

**Multilayer boards:** Full ground plane is essential. The ground plane provides:
- Low-inductance return for all signal currents
- Controlled-impedance transmission lines
- Electromagnetic shielding between layers

---

### O2. Ground Plane Current Distribution (Ch10)

#### O2.1 How Return Current Flows in a Ground Plane

**Low frequency:** Return current takes the path of least RESISTANCE -- spreads out broadly
in the plane, taking the shortest geometric path between two points.

**High frequency:** Return current takes the path of least INDUCTANCE -- flows directly
underneath the signal trace, because this gives the smallest loop area.

**Transition frequency:** Typically a few hundred kHz. Above this, current concentrates under
the signal trace.

**Key insight:** At high frequency, ground currents do what we want (small loops). The
designer's job is not to interrupt them.

#### O2.2 Current Spreading Under a Trace

For a microstrip trace at height h above a ground plane, the return current density in the
plane is concentrated under the trace with a distribution:

```
J(x) proportional to 1 / (1 + (x/h)^2)
```

where x is the lateral distance from directly under the trace.

**Practical rule:** 80% of the return current flows within a strip 3*h wide on either side of
the trace centerline (total width = 6*h).

**Keep-out zone implication:** No critical traces within 20*h of board edge, to allow return
current to spread naturally.

#### O2.3 Ground Plane Impedance

The impedance between two points on a ground plane is very low (milliohms) but NOT zero.
The ground plane voltage between two points 1 inch apart under a signal trace is measurable
and increases with signal frequency and rise time.

**Plane thickness effect:** Increasing copper weight does NOT reduce HF impedance because
of skin effect. Current only flows on the surface. At 30 MHz, skin depth in 1-oz copper =
full thickness; above 30 MHz, current is a true surface current.

**Multiple vias:** Using 3 ground vias reduces the ground voltage near the vias to about half
of what 1 via produces. Always use multiple ground vias for ICs that source large transient
currents.

---

### O3. Digital Circuit Radiation (Ch12)

#### O3.1 Differential-Mode Radiation

DM radiation comes from current loops on the PCB. The radiated electric field at distance r:

```
E_DM = 1.32e-14 * f^2 * I * A / r    [V/m]
```

where f = frequency [Hz], I = current [A], A = loop area [m^2], r = distance [m].

**Controlling DM radiation:**
- Minimize loop area (signal trace close to return plane)
- Use ground/power planes (microstrip or stripline gives smallest loops)
- Reduce signal current (use series damping resistors on clock outputs, typically 33 ohm)
- Cancel loops: use two decoupling capacitors on opposite sides of IC to create opposing
  current loops that partially cancel

#### O3.2 Common-Mode Radiation

CM radiation comes from cables acting as antennas, driven by CM voltage on the PCB ground.

```
E_CM = 1.26e-6 * f * I_CM * L_cable / r    [V/m]
```

where L_cable = cable length [m], I_CM = common-mode current [A].

**CM radiation is typically 100-1000x (40-60 dB) more important than DM radiation** for
regulatory compliance in products with cables.

**Controlling CM radiation:**
- Connect circuit ground to chassis at I/O connector area (minimizes V_CM driving cable)
- Filter all I/O lines with CM filters at enclosure boundary
- Use shielded cables with 360-degree shield terminations
- Add ferrite cores on cables at enclosure exit
- Separate I/O grounds: use separate return for I/O signals, joined to main ground only at
  one point, to prevent digital ground noise from reaching cables

#### O3.3 Dithered (Spread Spectrum) Clocks

Spread spectrum clocking modulates the clock frequency to spread harmonic energy over a
wider bandwidth, reducing peak emission amplitude.

**Typical reduction:** 6-12 dB for 1-2% frequency modulation.

**Use dithered clocks on 1- and 2-layer boards** where other EMC techniques are limited.
On multilayer boards, dithering is usually not needed if layout is done properly.

**Limitation:** Some systems (e.g., video, precision timing) cannot tolerate jitter from
dithering. Check system requirements before applying.

---

### O4. PCB Layer Stackup (Ch16)

#### O4.1 General Layout Rules

**Partitioning:** Group components into functional blocks:
1. High-speed logic, clocks, clock drivers
2. Memory
3. Medium/low-speed logic
4. Video
5. Audio and low-frequency analog
6. I/O drivers
7. I/O connectors and CM filters

Keep high-speed logic and memory AWAY from I/O area. Place I/O drivers CLOSE to connectors.
Crystals/oscillators close to their ICs, away from I/O area.

**Keep-out zones:**
- High-frequency circuits: >= 0.5 in (13 mm) from I/O area
- Critical signal traces: >= 20*h from board edge (h = trace-to-plane spacing)

**Clock paranoia rules:**
- Route clock traces FIRST (shortest possible)
- Add ground plane on component side under crystal/oscillator
- Connect this plane to main ground with multiple vias
- Series damping resistor (33 ohm) on all clock outputs >= 20 MHz
- Ferrite bead in series with Vcc to clock IC

**Signal speed metric for identifying critical signals:**
```
Signal_Speed = (F_0 * I_0) / t_r    [A/s^2]
```

Higher signal speed = more radiation potential = more layout attention needed.

#### O4.2 PCB-to-Chassis Ground Connection

Connect PCB ground to chassis at I/O connector area with multiple low-impedance connections.
This minimizes CM voltage that drives cables as antennas.

If metallic backshell connectors are used, make 360-degree contact to chassis via EMC gasket.
The connector backshell becomes part of the low-impedance PCB-to-chassis path.

#### O4.3 Return Path Discontinuities

**CRITICAL: Do not create slots or splits in ground/power planes that traces cross over.**

**Slots in planes:**
- Return current must detour around slot, creating large loop
- 1.5-in slot increases ground plane voltage by 5x (14 dB)
- Non-overlapping via holes do NOT create slots and are benign

**Split planes:**
- Return current must find alternate path (usually through nearest decoupling capacitor)
- If traces MUST cross a split power plane: place stitching capacitors (0.001-0.01 uF)
  within 0.1 in of trace on each side of split
- Stitching caps add ~5 nH (3 ohm at 100 MHz) -- far from ideal but ~28-32 dB better
  than doing nothing

**Layer transitions (via between signal layers):**
- When signal changes reference planes, return current must also change planes
- If both planes are same type (both ground): use plane-to-plane via adjacent to signal via
  (low inductance, much better than capacitor)
- If planes are different type (power and ground): use stitching capacitor adjacent to via
  (adds ~5 nH but necessary)
- Measured: single layer transition can increase emission by 30 dB vs single-layer routing

**Referencing same plane from both sides (top and bottom):**
- Via clearance hole provides surface connecting top and bottom of plane
- Return current flows on inner surface of clearance hole
- This is the PREFERRED way to route critical signals on two layers

#### O4.4 Critical Signal Routing Priority

Route critical signals (in order of preference):
1. On one layer only, adjacent to a plane
2. On two layers adjacent to the SAME plane (via passes through it)
3. On two layers adjacent to two SAME-TYPE planes (add ground-to-ground vias)
4. On two layers adjacent to different-type planes (add stitching capacitors)
5. On more than two layers (avoid this)

#### O4.5 Ground Fill (Copper Pour)

- Must be connected to ground at MANY points (not left floating)
- Ungrounded fill increases emissions, susceptibility, and crosstalk
- Small isolated fill areas: avoid completely (do no good, can make things worse)
- Not recommended for high-speed digital on multilayer boards (impedance discontinuities)
- On multilayer boards, apply fill only to surface layers

#### O4.6 Layer Stackup: Design Objectives

Six objectives for multilayer boards (rarely can all be met simultaneously):
1. **Every signal layer adjacent to a plane** (ALWAYS meet this)
2. **Signal layers tightly coupled to adjacent plane** (ALWAYS meet this)
3. **Power and ground planes closely coupled together**
4. **High-speed signals on buried layers between planes** (planes act as shields)
5. **Multiple ground planes** (lower ground impedance, reduce CM radiation)
6. **Critical signals confined to two layers adjacent to same plane**

An 8-layer board can meet 5 of 6. Four- and six-layer boards can meet 4 of 6.
Board cross-section should be symmetrical (balanced) to prevent warping.

#### O4.7 Recommended Stackups

**4-layer board** (most common upgrade from 2-layer):

Preferred stackup (tight signal-to-plane coupling):
```
Layer 1: Signal (components)     ---|
Layer 2: Ground plane               |-- thin dielectric (5-8 mil)
Layer 3: Power plane               |-- thick core (40+ mil)
Layer 4: Signal (solder side)    ---|-- thin dielectric (5-8 mil)
```

The thin dielectric between signal and adjacent plane minimizes loop area.
Satisfies objectives #1 and #2. The large power-ground spacing means
decoupling must come from discrete capacitors, not interplane capacitance.

Alternative (better interplane capacitance, worse signal coupling):
```
Layer 1: Signal         -- thick
Layer 2: Ground plane   -- thin (tight P/G coupling)
Layer 3: Power plane    -- thick
Layer 4: Signal
```

This is NOT recommended -- signal-to-plane coupling is poor.

**6-layer boards:**

Preferred stackup A (best for EMC):
```
Layer 1: Signal
Layer 2: Ground plane     (thin dielectric to L1)
Layer 3: Signal
Layer 4: Power plane
Layer 5: Ground plane
Layer 6: Signal           (thin dielectric to L5)
```

Two ground planes reduce ground impedance. Route high-speed signals on L3 (buried between
planes, shielded). Route non-critical signals on L1 and L6.

**8-layer boards** (first to meet 5 of 6 objectives):

Recommended stackup:
```
Layer 1: Signal
Layer 2: Ground           (tight coupling to L1)
Layer 3: Signal
Layer 4: Power            (tight coupling to L5 ground)
Layer 5: Ground
Layer 6: Signal
Layer 7: Ground           (tight coupling to L8)
Layer 8: Signal
```

Three ground planes. High-speed signals on L3 and L6 (buried between planes).
This provides excellent shielding, low ground impedance, and good power-ground decoupling.

**10- and 12-layer boards:** Add more signal layers between ground planes. Use additional
power planes as needed for multiple voltages. Keep power planes adjacent to ground planes
for good decoupling. Maintain symmetry.

**General rules for all stackups:**
- Minimum dielectric thickness between signal layer and adjacent plane: 5 mil preferred
- For controlled impedance: adjust trace width for target Z_0 given dielectric thickness
- Power plane adjacent to ground plane: use thinnest available dielectric (2-4 mil ideal)
  for maximum interplane capacitance (embedded capacitance helps above 500 MHz)

**1- and 2-layer board EMC survival guide:**
- Only use when clock frequencies < 10 MHz
- Route all critical signals first with adjacent ground return traces
- Clocks: ground return trace on BOTH sides of signal
- Use ground and power grid (10-12 dB emission reduction vs no grid)
- Minimum 2 decoupling capacitors per IC (4 for square packages, on opposite sides)
- Ferrite bead in series with Vcc to each clocked IC
- Consider dithered clock
- Consider image plane (metal sheet close to board)
- Fill unused areas with grounded copper

---

### O5. Mixed-Signal PCB Layout (Ch17)

#### O5.1 The Split Ground Plane Myth

**Conventional wisdom says:** Split the ground plane into separate analog and digital sections,
connected at one point (usually at the power supply or A/D converter).

**Ott's position:** In almost all cases, a single unified ground plane performs better than
a split ground plane, both functionally and for EMC.

**Why split planes cause problems:**
- Two separate planes connected at one point create an efficient dipole antenna
- Any RF voltage between the planes drives radiation
- The single connection point has high impedance at high frequency
- Digital return currents cannot find a low-impedance path if they must cross the split
- If ANY trace crosses the split, the loop area increases dramatically

**"Thou shalt have but one ground before thee."** -- Terrell and Keenan, Digital Design for
Interference Specifications.

#### O5.2 Why a Unified Ground Plane Works

Microstrip ground plane return current distribution is concentrated directly under the trace.
At 10 MHz and above, 95% of the return current flows within a strip only a few trace-heights
wide under the trace.

**Implication:** Digital return currents do not spread across the entire plane. They stay under
their own traces. Therefore, digital noise does not significantly contaminate the analog
ground IF the analog and digital sections are physically separated on the board.

**The solution is partitioning, not splitting:**
- Place analog circuits in one area, digital in another
- Use a SINGLE continuous ground plane under both
- Do not route digital traces through the analog area
- Do not route analog traces through the digital area
- The unified plane provides a low-impedance return for both

#### O5.3 Analog and Digital Ground Pins on Mixed-Signal ICs

Most A/D and D/A converters have separate AGND and DGND pins.

**Common misconception:** AGND goes to analog ground plane, DGND goes to digital ground plane.

**Correct approach (per Ott and most IC manufacturers):**
- Connect BOTH AGND and DGND pins to the same unified ground plane
- The separate pins exist to provide separate INTERNAL ground paths within the IC
- They must be connected to ground at the same point (directly under the IC)
- Do NOT route a trace from the DGND pin across the board to a "digital ground"

**If using split planes (not recommended but sometimes required by legacy designs):**
- Place the A/D or D/A converter so it straddles the split
- Connect AGND to analog plane, DGND to digital plane
- The IC itself becomes the single connection point between the planes

#### O5.4 When Split Ground Planes ARE Appropriate

Split planes may be justified when:
- Very high resolution (>16-bit) A/D or D/A converters are used
- The analog section has signals at the microvolt level
- The digital section has very high transient currents (large FPGAs, DSPs)
- Stripline construction is used (return current stays on nearest plane surface, providing
  natural isolation between top and bottom of the same plane)
- Each section has its own connector (no cables crossing the split)

Even in these cases, the split should be bridged at one point with a very low-impedance
connection (wide copper strap or multiple vias, not a thin trace).

#### O5.5 Mixed-Signal Power Distribution

**Separate analog and digital power supplies** (or at minimum, separate regulators/LDOs).
Each supply feeds only its own section.

**Decoupling:** Each analog IC should have its own local decoupling. Use low-ESR ceramic
capacitors (100 nF MLCC typical) plus bulk tantalum or electrolytic where needed.

**Ferrite bead isolation:** A ferrite bead between the digital and analog power rails provides
HF isolation while allowing DC to pass. This is often more practical than separate regulators.

#### O5.6 High-Resolution Converter Layout Rules

For 16+ bit converters with sampling rates above 1 MSPS:

1. Place converter IC as close as possible to its analog input connector
2. Keep analog input traces as short as possible
3. Use a solid ground plane under the converter -- no splits, no vias carrying digital signals
4. Route sampling clock as a controlled-impedance trace, keep it short and away from
   analog inputs
5. Use separate analog and digital power with ferrite bead isolation
6. Place all analog support circuitry (references, filters, buffers) on the analog side
7. Digital data bus exits the converter on the digital side away from analog inputs

#### O5.7 Vertical Isolation with Stripline

In multilayer boards, stripline (signal between two planes) provides natural vertical
isolation. The planes confine the fields from the trace to the region between the two planes.

**Application to mixed signal:** Route analog signals as stripline on inner layers. The bounding
planes prevent digital noise on outer layers from coupling into the analog signals. This
provides 40-60 dB of isolation between the analog stripline and traces on the outer layers.

#### O5.8 The IPC Problem

IPC-2141 (controlled impedance design) and other IPC standards do not adequately address
EMC considerations in their stackup recommendations. Following IPC recommendations alone
does not guarantee good EMC performance. The designer must overlay EMC requirements
(return path continuity, plane coupling, signal layer placement) on top of IPC mechanical
and impedance requirements.
