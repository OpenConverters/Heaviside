# GaN Power Transistor Design Guide

Source: Lidow, Reusch, Strydom, de Rooij, Glaser -- "GaN Transistors for Efficient Power Conversion" (3rd ed, Wiley 2020).

## GaN vs Silicon vs SiC Comparison

### Material Properties

| Parameter | Silicon | GaN | SiC |
|-----------|---------|-----|-----|
| Band Gap (eV) | 1.12 | 3.39 | 3.26 |
| Critical Field (MV/cm) | 0.23 | 3.3 | 2.2 |
| Electron Mobility (cm2/V-s) | 1400 | 1500 (2DEG: 1500-2000) | 950 |
| Permittivity | 11.8 | 9.0 | 9.7 |
| Thermal Conductivity (W/cm-K) | 1.5 | 1.3 | 3.8 |

### Practical Implications

- **GaN advantage over Si**: 10x higher critical field means drift region can be 10x smaller, allowing 100x higher doping. Net result: RDS(on) vs voltage capability is 1000x better theoretically.
- **GaN vs SiC**: GaN has higher mobility (especially in the 2DEG) and higher critical field, giving better RDS(on) at low-to-medium voltages (< 650 V). SiC has 3x better thermal conductivity, advantageous at very high power. SiC excels at 650 V to 1700 V; GaN dominates below 200 V and is competitive at 650 V.
- **Current GaN voltage range**: Commercial devices available from 15 V to 650 V. Most GaN development targets <= 200 V where the advantage over Si is most dramatic (3-11x FOM improvement).
- **Cost trajectory**: GaN-on-Si uses standard silicon fab infrastructure. Cost per unit performance is already competitive with Si MOSFETs and continues to improve rapidly.

### Figure of Merit (FOM) Comparisons

**Hard-switching FOM**: (QGD + QGS2) * RDS(on) -- determines overlap switching losses.
- GaN is 3-11x better than Si across 40-600 V range.
- Advantage increases at higher voltages.

**Soft-switching FOM**: (QOSS + QG) * RDS(on) -- determines ZVS transition time and gate losses.
- GaN is 2-5x better than Si across all voltages.

**Gate charge FOM**: QG * RDS(on) -- determines gate drive losses at high frequency.
- GaN is 3-10x better than Si. Critical advantage for MHz switching.

## GaN Transistor Types

### Enhancement-Mode (e-mode) GaN FET
- Normally-off device. Positive VGS required to turn on.
- Gate drive: 4-5 V typical. Absolute max: 6 V.
- No body diode (2DEG reverse conduction instead).
- Lateral device structure (HEMT), chip-scale packaging (LGA/BGA).
- Examples: EPC eGaN FETs, GaN Systems GS66xxx, Infineon CoolGaN.

### Cascode GaN
- Depletion-mode GaN HEMT in series with a low-voltage Si MOSFET.
- Gate drive: Standard MOSFET levels (+/-18 V).
- Has a true body diode (from the Si MOSFET).
- Familiar gate drive but adds parasitic inductance from internal packaging.
- Examples: Transphorm TPH3006PD.

### Integrated GaN (Driver + FET)
- Gate driver integrated on the same die or in the same package.
- Eliminates gate loop inductance entirely.
- Examples: TI LMG341x, EPC monolithic half-bridges, Navitas NV61xx.

## Reverse Conduction (No Body Diode)

This is one of the most important practical differences between GaN and Si MOSFETs.

### The Mechanism

GaN transistors have no p-n junction body diode. Instead, reverse current flows through the 2DEG channel when the source-drain voltage exceeds VGS(th). The channel turns on in the reverse direction when the gate-to-channel potential (which is gate-to-source minus the channel-to-source voltage drop) exceeds threshold.

### Characteristics

- **Forward voltage (VSD)**: Approximately VGS(th) + I * RDS(on) = 1.5 to 4 V depending on current and device. This is 2-4x higher than a Si MOSFET body diode (0.5-1.0 V).
- **Reverse recovery charge (Qrr)**: Zero. There is no minority carrier storage. This is a major advantage -- no reverse recovery losses at any switching speed or frequency.
- **Temperature dependence**: VSD increases with temperature due to RDS(on) increase. The slope of the ISD-VSD curve follows the same temperature characteristic as the forward conduction.
- **Negative VGS effect**: Applying negative gate drive voltage increases VSD by approximately 1 V per volt of negative bias. This is why negative gate drive is not recommended for enhancement-mode GaN.

### Design Implications

1. **Dead time losses are higher per unit time** -- but dead time can be much shorter because Qrr = 0.
2. **Anti-parallel Schottky diodes** may be beneficial if dead time is long. They add ~0.3 V drop (vs 2+ V for GaN reverse conduction). But they add parasitic capacitance that reduces switching speed. Evaluate on a case-by-case basis.
3. **ZVS is even more important** -- eliminating the dead-time reverse conduction period through ZVS avoids the high VSD loss entirely.
4. **No need for body diode recovery snubbers or blanking time** -- simplifies circuit design and control.

## Hard-Switching Loss Analysis

### COSS Losses (Output Capacitance)

Total COSS energy lost per switching cycle in a half bridge:

```
E_OSS_total = V_BUS * Q_OSS_Q2 - E_OSS_Q2 + E_OSS_Q1
```

For symmetric half bridges (Q1 = Q2):

```
E_OSS_total = V_BUS * Q_OSS - 2 * E_OSS
```

Key points:
- COSS is highly nonlinear. Do not linearly scale QOSS or EOSS from one voltage to another.
- COSS losses are independent of switching speed -- they are set by bus voltage and device selection.
- COSS loss can only be eliminated by ZVS operation.
- The total COSS-related loss is always greater than 2 * EOSS; the full expression includes the energy drawn from the bus to charge Q2's COSS during Q1's turn-on.

### Turn-On Overlap Loss

For GaN transistors with strong (low-resistance) gate drive, the turn-on transition differs fundamentally from Si MOSFETs:

- **No Miller plateau**: The gate voltage continues rising through the voltage transition. The channel current overshoots IL, and excess current discharges COSS. This produces much faster transitions but requires different analytical models.
- **Current rise time**: Determined by QGS2 and gate drive current.
- **Voltage fall time**: Determined by QOSS displacement by excess channel current above IL. Depends on transconductance (gfs) and total (QOSS_Q1 + QOSS_Q2).
- **Typical GaN turn-on times**: 1-5 ns for voltage fall, 0.1-0.5 ns for current rise. Compare with 10-50 ns for Si MOSFETs.

### Turn-Off Overlap Loss

For GaN transistors with strong gate drive:

- Gate voltage falls below threshold before significant dv/dt occurs.
- Channel current falls to zero while VDS is still near zero (overlap loss is minimal).
- The remaining voltage transition is lossless -- load current charges/discharges COSS.
- Turn-off loss is typically 10-100x smaller than turn-on loss for GaN.

### Gate Charge Losses

```
P_G = Q_G * (V_drv_on - V_drv_off) * f_sw
```

At MHz frequencies, gate charge loss becomes a significant fraction of total loss. GaN's 5-10x lower QG is a critical advantage. Gate power is supplied by the bootstrap or bias supply, so high gate loss also increases bias supply losses.

### Reverse Conduction Losses

See Dead Time section in gate-drive knowledge. Loss is proportional to VSD * I * t_dead * f_sw. Minimize dead time.

## Soft-Switching Advantages with GaN

### ZVS Benefits

GaN transistors offer particular advantages in ZVS (soft-switching) topologies:

1. **Lower QOSS**: Requires less energy and time for ZVS transition. ZVS transition time: t_ZVS = Q_OSS / I_ZVS. Lower QOSS means shorter dead time needed for ZVS, giving higher effective duty cycle and lower RMS currents.

2. **Lower QG**: Reduces gate drive loss, which becomes a dominant loss at high frequency in soft-switching converters where switching losses are eliminated.

3. **Zero Qrr**: No reverse recovery current spike when body diode/channel turns off after ZVS conduction. This simplifies ZVS timing and eliminates a source of loss and ringing.

4. **Faster gate charging**: With 5-10x lower QG, the gate charges in < 10 ns vs ~100 ns for Si. This further extends the effective power delivery period.

### Quantified Advantages (48 V bus converter example from Ch8)

- ZVS transition: 42 ns (GaN) vs 87 ns (Si MOSFET) -- 2x reduction
- Effective duty cycle: 42% (GaN) vs 34% (Si) -- higher power delivery fraction
- Conduction loss reduction: ~20% due to higher effective duty cycle
- Peak efficiency: 97.2% (GaN at 1.2 MHz) vs 96.2% (Si at 1.2 MHz)
- Power loss reduction: ~25% at full load
- GaN at 1.6 MHz still outperforms Si at 800 kHz

### Soft-Switching FOM

```
FOM_SS = (Q_OSS + Q_G) * R_DS(on)
```

GaN provides 2-5x improvement across all voltage classes. A 50% FOM_SS reduction translates to approximately 50% dead time reduction in resonant converters.

## Thermal Management for Chip-Scale Packages

### Dual Heat Path

GaN chip-scale packages (LGA/BGA) have two effective cooling paths:

1. **Down through solder bumps to PCB** (RthJB): Heat flows through BGA/LGA solder connections into copper traces and thermal vias.
2. **Up through die substrate** (RthJC): With the active side facing down, the top of the die (silicon substrate) can be cooled with a heatsink + TIM.

Both paths are effective, unlike traditional PQFN packages where only bottom-side cooling works well.

### Thermal Resistance Comparison

For chip-scale GaN vs packaged Si MOSFETs of similar size:
- **RthJB** (junction to board): Similar scaling with device area for both technologies.
- **RthJC** (junction to case): Much lower for chip-scale GaN because there is no package/lead frame in the way. This makes top-side heatsinking very effective.
- **RthJA** (junction to ambient): Chip-scale GaN achieves lower total RthJA when top-side heatsinking is used.

### Practical Thermal Design

- **Top-side heatsink**: Attach heatsink to top of GaN die with TIM. This is the most effective cooling strategy for chip-scale GaN. It does not interfere with the PCB power loop layout.
- **Bottom-side cooling**: Requires thermal vias in the PCB. These vias may conflict with the optimal power loop layout. Copper inlays may be needed for high-power designs.
- **TIM selection**: Must provide electrical isolation if heatsink is shared. Thermal conductivity ranges from 0.9 W/m-K (basic pad) to 12 W/m-K (premium soft silicone pad). For a 10 mm2 device, thermal resistance ranges from 8 to 400+ degC/W depending on TIM choice.
- **Transient thermal impedance**: At switching frequencies > 100 kHz, the normalized transient thermal impedance converges to the duty cycle. Average power can be used for thermal calculations in typical DC-DC converters.
- **Mutual heating**: In compact designs, transistor and inductor/transformer temperatures are coupled. Include inductor loss in the thermal model.

### Temperature Coefficient

GaN RDS(on) increases with temperature (positive temperature coefficient), similar to Si MOSFETs. This provides natural current sharing when devices are paralleled and prevents thermal runaway. The temperature coefficient is also positive in the saturation region (transfer characteristic), unlike Si MOSFETs which can have negative tempco at low currents. This makes GaN inherently more stable for paralleling during switching transients.

## PCB Layout Critical Guidelines

### The Three Critical Loops (Priority Order)

1. **Common-Source Inductance (CSI)**: Source inductance shared between power loop and gate loop. Must be minimized. Target: < 50 pH. Achieved by using dedicated gate-return source connections.

2. **Power Loop Inductance**: The high-frequency loop carrying switched current (bus capacitor to high-side drain, through switch node, through low-side source, back to bus capacitor). Target: < 0.5 nH for high-performance designs.

3. **Gate Loop Inductance**: Loop from gate driver output through gate, through gate-return source, through bypass cap, back to driver. Target: < 1 nH.

### Power Loop Design Options

**Lateral Power Loop**:
- Components and capacitors on same PCB side.
- Uses a shield layer (first inner layer) for magnetic field cancellation.
- Independent of board thickness.
- Requires unbroken shield plane close to the power loop.

**Vertical Power Loop**:
- Capacitors on opposite side of PCB from devices (directly beneath).
- Uses opposing currents on top/bottom layers for field cancellation.
- Performance depends on board thickness (thinner = better).
- Not single-sided assembly compatible.

**Optimal Power Loop** (recommended):
- Components on same side; first inner layer used as return path directly beneath the top-layer power loop.
- Combines minimal loop area with magnetic field self-cancellation.
- Independent of board thickness.
- Single-sided PCB compatible.
- Measured loop inductance: ~250 pH for discrete, ~150 pH for monolithic half-bridge.

### Interleaved Via Technique

For LGA/BGA devices, interleave drain and source vias beneath the device:
- Creates multiple small loops with opposing currents.
- Reduces magnetic energy storage, lowering inductance.
- Also reduces AC conduction losses (lower eddy and proximity effects).
- Shorten high-frequency current paths.

### Layout Impact on Performance

Measured example (12 V to 1.2 V, 1 MHz, EPC2015C):
- Vertical power loop (1.8 nH): 3.8 W loss, 85% overshoot
- Lateral power loop (1.0 nH): 3.3 W loss, 45% overshoot
- Optimal power loop (0.5 nH): 3.1 W loss, 30% overshoot
- Optimal layout provides 500% faster switching and 40% less overshoot vs Si MOSFET in equivalent package.

## Paralleling GaN Transistors

### Single-Switch Paralleling

When paralleling GaN devices for a single switching element:

1. **CSI symmetry is paramount**: All devices must see identical common-source inductance. This is more important than power loop matching.
2. **Individual gate resistors**: Each device gets its own pull-up and pull-down resistors for independent speed tuning.
3. **Dedicated gate-return plane**: Use a full inner layer for the gate-return source connection, not connected to the power ground.
4. **Maximum 4 devices in a row** before mirroring the layout around another axis.
5. **Positive temperature coefficient aids sharing**: GaN's positive tempco in both ohmic and saturation regions naturally balances current sharing between paralleled devices.

### Half-Bridge Paralleling

Parallel complete half-bridge power loops rather than individual devices:
- Each loop is a self-contained half bridge with its own input cap.
- Low-frequency (DC) currents share between loops through wider bus connections.
- High-frequency loop currents are contained within each loop independently.
- Provides best CSI symmetry and power loop matching.
- Measured example: 48 V to 12 V with 4 parallel loops achieved 97% efficiency at 300 kHz, 30 A.

## Practical Design Considerations Unique to GaN

### Things That Catch MOSFET Designers Off Guard

1. **No avalanche rating**: Most GaN transistors are not avalanche-rated. Voltage clamping (Zener or TVS) may be needed to protect against voltage spikes. Design the power loop to keep overshoot within VDS rating.

2. **Dynamic RDS(on)**: GaN transistors can exhibit temporary RDS(on) increase after high-voltage blocking (charge trapping). Modern devices have largely mitigated this, but it still affects some devices. Verify with double-pulse testing under worst-case conditions.

3. **Gate oxide lifetime**: The gate dielectric in pGaN enhancement-mode devices degrades with voltage stress. Staying within the recommended gate voltage range is not just about preventing immediate damage -- it ensures long-term reliability.

4. **Measurement difficulty**: GaN switching events occur in 1-5 ns. Standard oscilloscope probes can create measurement artifacts larger than the actual signals. Use proper high-bandwidth measurement techniques (see gate-drive knowledge file).

5. **ESD sensitivity**: GaN transistors in chip-scale packages have lower ESD ratings than packaged MOSFETs. Follow standard ESD handling procedures. Many devices are rated to only 500 V HBM.

6. **Substrate connection**: For GaN-on-Si devices, the silicon substrate must be connected to the source (or to a voltage at or below the source). Floating substrates can cause erratic behavior and increased losses.

### Design Checklist

- [ ] Gate drive voltage regulated to 4.0-5.25 V (or per specific device datasheet)
- [ ] Gate loop critically damped on turn-on edge
- [ ] Separate turn-on and turn-off gate resistors
- [ ] CSI minimized (dedicated gate-return connection)
- [ ] Power loop inductance < 1 nH (use optimal layout)
- [ ] Dead time minimized (5-20 ns target for MHz switching)
- [ ] No negative gate drive voltage
- [ ] Voltage overshoot within device VDS rating (add clamping if needed)
- [ ] Thermal design accounts for both top-side and board-side cooling
- [ ] High-bandwidth measurement equipment available for debugging
- [ ] Bootstrap supply provides regulated 5 V with clamp

## GaN in High-Frequency Point-of-Load (from Su VT 2015)

Source: Y. Su, "High Frequency, High Current 3D Integrated Point-of-Load Module," PhD dissertation, Virginia Tech, 2015.

### Multi-MHz POL Converter with GaN

Su demonstrates GaN-enabled POL converters operating at 1-5 MHz with output currents of 15-20 A per phase. Key practical findings:

**GaN device selection for POL:**
- EPC eGaN (LGA) and IR GaN (DirectFET-style) devices evaluated.
- FOM (R_DS(on) * Q_g) of ~10 mOhm*nC for 30V GaN vs. ~45 mOhm*nC for best silicon lateral-trench MOSFETs.
- At multi-MHz, gate charge loss (Q_G * V_drv * f_sw) becomes a dominant loss term. GaN's 5-10x lower Q_G is essential.

**Parasitic management at multi-MHz:**
- At >1 MHz, packaging parasitics dominate switching loss. Even 1 nH of loop inductance causes significant ringing and voltage overshoot.
- The 3D integrated POL module eliminates wire bonds by placing active components directly on the inductor substrate. Decoupling capacitor placed directly on top of the device achieves parasitic loop inductance of only 0.82 nH.
- A conductive shielding layer between the active device layer and the magnetic substrate prevents magnetic coupling between the power loop and the inductor. Without this shield, parasitic magnetic interaction causes large ringing and efficiency loss.

### 3D Integrated POL Module Architecture

The 3D integration concept uses the inductor as a substrate (carrier board) for the active components:

**LTCC (Low Temperature Co-fired Ceramic) approach:**
- LTCC ferrite (e.g., ESL 40010, 40012) sintered at 885C to form a monolithic inductor substrate.
- Silver paste traces co-fired with the ferrite provide interconnect.
- Lateral flux inductor design: flux flows horizontally in a planar core, enabling very low profile (1-2 mm core thickness).
- Demonstrated: 5 MHz, 20 A two-phase POL at 900+ W/in3 power density with inverse-coupled LTCC inductor.

**PCB-embedded core approach (lower cost alternative):**
- NEC/Tokin SENFOLIAGE alloy flake composite laminated into standard multi-layer PCB.
- Compatible with conventional PCB manufacturing (lamination at 190C, standard FR4 prepreg).
- Winding formed by copper layers and standard PCB vias.
- Demonstrated: 1.5 MHz, 20 A integrated POL at 600 W/in3. Passed 600-cycle thermal cycling reliability test.
- Cost advantage over LTCC: uses existing PCB infrastructure, no high-temperature sintering.

**Material comparison for integrated magnetics:**

| Material | Permeability (mu_r) | Core Loss (kW/m3 at 5MHz, 10mT) | DC Bias Tolerance | Integrability |
|----------|---------------------|----------------------------------|-------------------|---------------|
| LTCC 40010 (NiZn ferrite) | 50 | ~300 | Good (flat mu vs H) | LTCC process (885C) |
| LTCC 40012 (NiZn ferrite) | 200 | ~800 | Moderate | LTCC process (885C) |
| SF flake composite (NEC/Tokin) | 30-70 | ~500 | Very good (distributed gap) | PCB lamination (190C) |

### Lateral Flux Inductor and Non-Uniform Flux Design

Su's key academic contribution challenges conventional magnetic design rules that assume uniform flux:

**Lateral flux pattern:** In a planar core with lateral flux, the flux density varies strongly across the core cross-section. Near the winding, B is high (potentially above B_sat); far from the winding, B is low. This seems wasteful by conventional design standards.

**DC-AC flux counterbalance:** In the high-B regions, the incremental permeability drops (core is partially saturated), which limits the AC flux swing. The core loss density (proportional to Delta_B^beta) is automatically limited where B_DC is high. Conversely, in low-B regions, mu is high and AC flux swing is larger, but B_DC is low and the core is well utilized.

**Practical implications:**
- The saturated region does not cause failure -- it simply stops carrying incremental flux. The AC energy storage redistributes to unsaturated regions.
- Total core loss in the variable-flux structure can be lower than in a uniform-flux design of the same volume, especially at light load (where B_DC drops and the entire core volume becomes effective).
- Thermal distribution is more uniform because loss density is spread rather than concentrated.
- This insight enables smaller core volumes by allowing the operating point to extend into partial saturation without penalty.

### Coupled Inductor for Multi-Phase POL

**Inverse coupling benefit:** Two-phase inverse-coupled inductors increase steady-state inductance L_ss (lower ripple, better efficiency) while decreasing transient inductance L_tr (faster load step response). The ratio L_ss/L_tr depends on the coupling coefficient alpha.

**Transient inductance problem and solution:** In lateral-flux coupled inductors without air gaps, the coupling coefficient is strongly load-dependent:
- At full load (20A): alpha ~ -0.6, L_ss/L_tr ~ 1.7 (good coupling).
- At no load (0A): alpha ~ -0.15, L_ss/L_tr ~ 1.2 (nearly uncoupled). L_tr increases ~13x from full to no load.
- This causes very slow transient response at light load.

**Fix: air slots in leakage path.** Adding low-permeability slots (air or magnetic slots) between the two winding regions provides a controlled leakage path:
- Reduces the load dependence of coupling coefficient.
- Stabilizes L_tr across the full load range.
- Enables 200 kHz control bandwidth for AVP (adaptive voltage positioning) in laptop VR applications.
