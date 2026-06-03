# Modern AC-DC Converter Design Guide

> Compiled from: Jitaru 2026 (Modern Technologies for Medium and Low Power AC-DC Converters,
> APEC S.08, Rompower Energy Systems)

---

## 1. Conventional Flyback Limitations

The conventional quasi-resonant (QR) flyback topology has three fundamental limitations that
degrade efficiency, especially at higher input voltages:

### 1.1 Leakage Inductance Energy

Leakage inductance stores energy that is not transferred to the secondary. In a conventional
flyback, this energy is dissipated in a clamp circuit.

```
E_leak = 0.5 * L_leak * I_peak^2

Example (65 W, L_leak = 4 uH, f_sw = 150 kHz):
  E_leak = 8.8 uJ per cycle
  P_leak = E_leak * f_sw = 1.32 W
```

### 1.2 Dead-Time Oscillation

During the dead time, the magnetizing inductance resonates with the parasitic capacitance
reflected across the primary switch. This low-frequency oscillation wastes energy.

```
Example (65 W, Vo = 20 V, N = 5:1):
  E_osc = 1.8 uJ per cycle
  P_osc = 0.27 W
```

### 1.3 Switching Losses

Hard switching of the primary MOSFET dissipates the energy stored in its output capacitance:

```
E_sw = 0.5 * Ceq * Vds^2

Example (65 W, Ceq = 330 pF, f_sw = 150 kHz):
  At 115 Vac (valley switching at 63 Vdc):  E_sw = 0.65 uJ,  P_sw = 0.09 W
  At 115 Vac (hill):                        E_sw = 1.7 uJ,    P_sw = 0.25 W
  At 230 Vac (valley at 220 Vdc):           E_sw = 7.98 uJ,   P_sw = 1.19 W
  At 230 Vac (hill at 426 Vdc):             E_sw = 29.9 uJ,   P_sw = 4.49 W
```

At high line, switching losses dominate. This motivates the pursuit of zero-voltage switching
(ZVS) in flyback converters.

---

## 2. ZVS Methods for Flyback Topology

Seven distinct ZVS methods have been identified for flyback converters. Each addresses the
hard-switching problem differently.

### 2.1 ZVS_01 & ZVS_02: Magnetizing Current Manipulation

**Principle:** An additional current pulse is injected into the magnetizing inductance before
the main switch turns on. This current discharges the parasitic capacitance of the main switch
toward zero voltage.

**ZVS_01 (sharp injection):** A dedicated injection switch (Minj) is turned on briefly to
build up magnetizing current. Synchronizing injection with the "hill" of the parasitic ringing
improves performance.

**ZVS_02 (refresh pulse):** The synchronous rectifier (SR) is turned on for a short interval
before the main switch. This builds negative magnetizing current that discharges the primary
switch capacitance.

**Trade-offs:**
- The additional flux excursion (delta_B2) increases core losses
- "Initial transition losses" occur from the voltage transition at the start of injection
- These losses are more pronounced at lighter loads (longer dead time)

**Quantitative example (60 W flyback, 800 V DC input, 24 V output, EQ25/3C95 core):**
```
Without ZVS:  B_peak = 220 mT,  P_core = 0.6 W
With ZVS_01/02:  B_peak = 310 mT,  P_core = 1.2 W  (2x increase)
```

**Conclusion:** ZVS through magnetizing current manipulation works but carries a magnetic
penalty of approximately 2x core loss increase.

### 2.2 ZVS_03: Soft Energy Injection (Trapped Energy)

**Principle:** Preserve the energy in the dead-time resonant circuit rather than letting it
decay. Just before the main switch turns on, the trapped energy (a small current still
circulating in the resonant circuit) is used to discharge the switch capacitance.

A shorting switch (M2) and blocking diode (D1) trap the resonant energy at the moment when
all energy is in the magnetizing inductance. The trapped current amplitude is small, so losses
in the shorting switch are minimal.

An optional "push-back" current can be created by extending the SR conduction time, providing
additional energy for ZVS when the trapped energy alone is insufficient.

### 2.3 ZVS_04: Energy Injection via Auxiliary Winding

**Principle:** Energy is injected into the magnetizing current through an auxiliary transformer
winding. An injection voltage (Vinj) supplements the resonant energy to achieve full ZVS.

**Implementation options:**
- Energy injection winding using 1/4 turn on the transformer
- Harvesting leakage inductance energy and feeding it back as Vinj (US Patent 9,774,270 B2)

**Measured results (65 W adapter):**
```
Vin = 127 Vdc (90 Vac):  Eff = 93.75%,  f_sw = 135 kHz,  ZVS = 40 V
Vin = 325 Vdc (230 Vac):  Eff = 94.84%,  f_sw = 150 kHz,  ZVS = 100 V
```

**Disadvantage:** Increases flux swing and core loss.

### 2.4 ZVS_05: Rompower Self-Adjusting Current Injection

**Principle:** A sinusoidal pulse of current is injected into an auxiliary transformer winding
via a current injection switch (Minj). The injected current amplitude self-adjusts as a function
of the voltage across the main switch when Minj is activated.

**Self-adjusting properties:**
1. Higher Vds at injection activation --> larger injection current amplitude (automatically
   provides more energy when more is needed)
2. Larger Ceq --> larger injection current (adapts to device capacitance)

Operating with valley detection creates highly efficient ZVS operation.

**Key advantage:** True ZVS without detrimental side effects. The current injection is
proportional to actual need, avoiding over-injection penalties.

### 2.5 ZVS_06: Conventional Active Clamp Flyback

**Principle:** An active clamp switch across the primary winding captures leakage inductance
energy and returns it, while also providing ZVS.

**Drawbacks of conventional active clamp:**
1. High RMS current through the clamp switch (requires low Rds_on device)
2. Operates only in critical conduction mode (conventionally)
3. Complex control: requires dedicated driver for the clamp switch
4. At lighter loads, burst-mode operation is necessary for good efficiency
   (approach used by TI controllers)

### 2.6 ZVS_07: Tail-End Active Clamp

**Principle:** The active clamp switch (M2) is placed on the "tail end." Via M2's body diode,
leakage inductance energy is stored in a resonant capacitor (Cr). Before the main switch
turns on, M2 is activated to use this stored energy for ZVS and to transfer additional energy
to the secondary.

Synchronizing the turn-on of the active clamp with the "hill" of the drain voltage ringing
significantly improves operation. Works with both continuous and discontinuous mode.

**Drawback:** Higher flux swing and higher core loss.

---

## 3. Leakage Inductance Energy Solutions

### 3.1 Partial Active Clamp

The clamp switch M2 turns off during the positive current flow. Current continues through
M2's body diode until decay to zero. This method does not impact the natural ringing during
dead time.

**Drawback:** 100% of leakage energy goes to the output, but no ZVS is achieved.

### 3.2 High-Efficiency Active Clamp (Rompower Clamp)

An "energy extraction circuit" (D1, D2, VB) is added to the partial active clamp:
- Reduces the RMS current through the clamp switch by several times
- Eliminates ringing in the clamp circuit
- Allows a smaller, lower-cost clamp switch device
- Extracted leakage energy feeds into primary bias (VB) for housekeeping or ZVS

**Clamp current comparison:**
```
Conventional active clamp dissipation: 2.4x Rompower clamp dissipation
```

**Effect of VB voltage on clamp behavior:**
| VB (V) | Avg D1 Current (A) | Extracted Power (W) |
|--------|--------------------|--------------------|
| 0      | 0.258              | 0                  |
| 5      | 0.162              | 0.81               |
| 10     | 0.115              | 1.15               |
| 20     | 0.072              | 1.44               |
| 30     | 0.053              | 1.59               |
| 50     | 0.036              | 1.80               |

Higher VB voltages extract more leakage energy but with diminishing returns above ~20 V.

### 3.3 High-Efficiency Passive Clamp (Rompower Clamp)

A passive clamp circuit that achieves active-clamp-like characteristics:
- Leakage energy is extracted and used for bias and ZVS via current injection
- Reduces forward and reverse charge through Cr to levels comparable with diode reverse
  recovery charge
- Achieves key features of an active clamp without the active switch complexity

**Standard RCD clamp vs Rompower passive clamp:**
- RCD clamp: voltage spike of ~150 V overshoot for 2.5 uH leakage at Vin = 327 Vdc
- Rompower passive clamp: controlled clamping with energy recovery

---

## 4. The "Ideal Flyback" Topology

The "Ideal Flyback" combines leakage energy harvesting with ZVS current injection to
eliminate both major drawbacks of the conventional flyback:

1. **No energy lost to leakage inductance** -- extracted and reused
2. **True ZVS on all switching elements** -- no spikes or ringing

**Comparison at Vin = 1000 V (800 V bus application), 1700 V SiC MOSFET:**

| Parameter | Reference #1 (QR + valley) | Reference #2 (ZVS_02 + GaN) | Ideal Flyback |
|-----------|---------------------------|------------------------------|---------------|
| Vds peak | 1450 V | 1360 V | 1290 V |
| Voltage spike | 200 V | 120 V | 40 V |
| Derating | 85.3% | 80% | 75% |
| Core temp (Vin=1000V) | 79.8 C | -- | 46.2 C |
| Winding temp | 90.7 C | -- | 68.3 C |
| SR/diode temp | 91.4 C | -- | 46.9 C |

**Efficiency comparison (65 W adapter):**
- Rompower Ideal Flyback achieves 94% efficiency at 90 Vac input
- Outperforms competing solutions (Apple 67W, Anker 65W) across the full voltage range

**GaN vs Silicon in flyback converters:**
- QR flyback with valley detection: GaN provides ~0.2-0.3% efficiency improvement
- ZVS flyback with current injection: GaN provides ~0.4-0.5% efficiency improvement
- The benefit of GaN is more pronounced with ZVS due to elimination of switching losses,
  leaving only conduction losses where GaN's lower Rds_on helps

---

## 5. Medium-Power Topologies for PD 3.1 (100-250 W)

### 5.1 Hybrid Flyback Topology

A flyback-forward topology combining key features of both:

**Operating intervals:**
- t0-t1: Energy from Vin stored in magnetizing inductance and charges capacitor C1
- t1-t2: Energy from Vin continues charging C1; half-sinusoidal current injected into secondary
- t2-t3: Leakage between L1 & L2 resonates with C1; magnetizing energy + C1 energy
  transferred to secondary
- t3-t4: C1 charge generates negative current for ZVS across M1 at turn-on
- t4-t5: Negative current through L1 flows back to input, ensuring ZVS
- t5-t6: Current through L1 reaches zero, preparing for new cycle

**Key advantages:**
- Lower voltage stress on primary switches
- Resonance shapes secondary current into half-sinusoidal form
- Primary capacitor (HB) reduces transformer flux swing
- Very high efficiency for medium power
- Suitable for Power Delivery 5 V to 48 V
- Low di/dt in secondary

**Measured efficiency (240 W AC-DC):**
| Vin (Vac) | Efficiency |
|-----------|------------|
| 80        | 94.2%      |
| 130       | 95.3%      |
| 180       | 95.6%      |
| 230       | 96.5%      |
| 280       | 97.1%      |

### 5.2 ZVS Two-Transistor Flyback

The conventional two-transistor flyback reduces voltage stress on primary switches (each sees
Vin instead of 2*Vin) and recycles leakage inductance energy back to the primary.

**ZVS version adds a current injection circuit:**
- ZVS on all switching elements in any operating mode, including burst mode
- Simple control algorithm implementable in analog controller
- Most leakage energy transferred to secondary; portion energizes current injection
- No spikes or glitches on any switching element
- No parasitic oscillations during dead time
- Constant frequency operation

### 5.3 Single-Switch Flyback with High-Efficiency Clamp (PD 3.1)

Combines the single-switch flyback with Rompower passive or active clamp plus current
injection.

**With passive clamp (240 W, 48 V at 5 A):**
- Uses 950 V silicon MOSFET (IPD95R450P7)
- Vmax = 732 V, voltage derating = 77%
- Efficiency = 97.2% (DC-DC)

**With active clamp (240 W, 48 V at 5 A):**
- Same 950 V silicon MOSFET
- Vmax = 716 V (16 V less than passive), derating = 75%
- Efficiency = 97.4% (DC-DC), 0.2% improvement over passive

**Key features for both variants:**
- ZVS on all switching elements in all operating modes including burst
- Simple control algorithm
- No spikes or glitches
- Suitable for PD 3.1 (5 V to 48 V output)

---

## 6. 800 V Bus Solutions

For applications with 800 V DC bus (e.g., EV on-board chargers, PV inverters), the "Ideal
Flyback" topology using 1700 V SiC MOSFETs provides optimal performance.

**Design example (60 W, 200-1000 V input, single-ended flyback):**
- 1700 V SiC MOSFET in TO-263-7L package
- Rompower "Ideal Flyback" technology
- Achieves 75% voltage derating (vs 80-85% for competing approaches)
- Significantly lower component temperatures than reference designs

---

## 7. EMI Suppression in AC-DC Adapters

### 7.1 Transformer Shielding Techniques

**No shield (45 W adapter, RM8 core, 230 Vac, Y-cap = 150 pF):**
- Approximately 18 dB over the conducted emission limit

**Single copper foil shield:**
- Provides 10 dB attenuation
- Still 8 dB over limit

**Dual copper foil shield:**
- Total attenuation of 16 dB
- Still 2 dB over limit

### 7.2 Active Shield Technology

**Principle:** The shield winding moves in the same direction as the secondary winding,
resulting in zero displacement current between shield and secondary. This eliminates the
CM noise coupling mechanism.

**Implementation:**
- For high-side SR: shield placed between primary and secondary, driven to follow
  secondary voltage
- For low-side SR: similar principle with appropriate shield connection
- Combination of shielding and optimized noise injection achieves near-zero CM noise
  with Y-capacitor as small as 68 pF (5 uA leakage current)

**Winding structure optimization:**
```
Standard:  N1 = N1' = 11 turns, N3' = N3" = 4 turns, N2 = 4 turns
Optimized: N1 = N1' = 11 turns, N3' = 7 turns, N3" = 4 turns, N2 = 4 turns
```

The turns ratio between shield sections (N3'/N3") is adjusted to minimize net CM noise
injection.

**EMI measurements (240 W adapter, PD 3.1, Hybrid Flyback):**

| Configuration | EMI Reduction | Notes |
|--------------|---------------|-------|
| No shield, Y = 1 nF | Baseline (11 dB violation at 154 kHz) | -- |
| Copper foil shield, Y = 1 nF | 12 dB reduction | Traditional approach |
| Rompower active shield, Y = 1 nF | 18 dB reduction | 6 dB better than copper foil |
| Rompower partial shield | +0.3% efficiency, 5.3 C lower temp | Also reduces filter effort |

**Key advantages of active shield:**
- Reduces power dissipation in transformer (0.44 W savings in 240 W design)
- 5.3 C lower temperature rise than copper foil shield
- Simple 3-strand construction reduces labor cost
- 6 dB additional EMI reduction vs conventional copper foil

### 7.3 Reducing Y-Capacitor Requirements

By combining active shielding with optimized winding noise injection:
- Y-capacitor can be reduced to 68-150 pF (vs typical 1-4.7 nF)
- Leakage current reduced to 5-30 uA (vs typical 0.5 mA limit)
- This is critical for medical applications where leakage current limits are 0.1 mA

---

## 8. High-Density Design

### 8.1 Custom Magnetics for Density

Magnetics is the key enabler for increasing power density in AC-DC adapters:
- Custom core shapes optimized for the specific converter geometry
- Example: 65 W adapter achieving 36 W/in^3 power density in 31.6 cm^3 volume

### 8.2 Ultra-High-Density 65 W Design

**Specifications:**
| Parameter | Value |
|-----------|-------|
| Input voltage | 90-264 Vac |
| Output voltage/current | 5V/3A, 9V/3A, 15V/3A, 20V/3.25A |
| Max nominal power | 65 W |
| Efficiency at 90 V | 94% |
| Dimensions (uncased) | 42.5 x 31 x 24 mm |
| Volume | 31.6 cc |
| Power density | 34 W/in^3 |
| Standby power | 28 mW |
| Leakage current | 30 uA |

**Key semiconductor components:**
| Function | Part Number |
|----------|-------------|
| Primary PWM controller | XDPS2110 |
| Primary switch | IGLR60R190D1 |
| Secondary SR switch | ISC0802NLS |
| Secondary controller | MP6908GJ-Z |
| Current injection switch | ISZ230N10NM6ATMA |
| USB-PD controller | CYPD3174-24LQXQ |

**Technologies enabling high density:**
- Rompower passive clamp with energy recycling for ZVS
- Valley detection with adaptive ZVS
- Current injection technology
- EMI suppression (active shield) reduces filter size
- True soft switching eliminates need for snubbers

---

## 9. Design Selection Summary

### 9.1 Low Power (< 100 W, PD 3.0)

**Recommended:** Single-switch flyback with current injection ZVS
- Simplest implementation
- Highest efficiency with Rompower current injection (94%+ at 90 Vac)
- GaN provides additional 0.4-0.5% efficiency over silicon in ZVS mode
- For 800 V bus: use 1700 V SiC MOSFET with "Ideal Flyback" topology

### 9.2 Medium Power (100-250 W, PD 3.1)

**Option A: Hybrid Flyback** -- highest efficiency (97.1% at 280 Vac), lower voltage stress,
sinusoidal secondary current

**Option B: ZVS Two-Transistor Flyback** -- simple control, constant frequency, all elements
ZVS in all modes

**Option C: Single-Switch Flyback with High-Efficiency Clamp** -- 97.2-97.4% DC-DC efficiency,
simplest topology for medium power

### 9.3 Key Design Decisions

| Design Choice | Impact |
|--------------|--------|
| ZVS method | Current injection provides best overall trade-off (no magnetic penalty) |
| Clamp circuit | Rompower passive/active clamp recovers leakage energy at minimal cost |
| GaN vs Si vs SiC | GaN: 0.4-0.5% efficiency gain in ZVS. SiC: needed for > 650 V applications |
| EMI strategy | Active shield + minimized Y-caps reduces filter size and leakage current |
| Magnetics | Custom cores enable 30+ W/in^3 density; simple winding structures preferred |

---

## 10. Final Design Conclusions

1. **PD 3.0 and PD 3.1 transform the flyback topology** for next-generation power delivery
2. Among seven ZVS approaches, **current injection achieves true ZVS** without detrimental
   side effects (no increased core loss, no increased flux swing)
3. **Magnetics and EMI technologies** are the key enablers of high-density, high-efficiency
   converters
4. The **"Ideal Flyback"** topology emerges as optimal for both 400 V (PD 3.1) and 800 V
   bus applications in terms of efficiency, simplicity, and cost
5. **Rompower passive clamp** delivers leakage energy recovery without active switch
   complexity -- a breakthrough pursued for decades
