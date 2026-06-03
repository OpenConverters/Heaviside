# Kolar et al. — Impact of Magnetics on Power Electronics Converter Performance
*PSMA Magnetics Workshop 2017, ETH Zurich PES Laboratory*
*Authors: J.W. Kolar, F. Krismer, M. Leibl, D. Neumayr, L. Schrittwieser, D. Bortis*

---

## 1. η-ρ Performance Limits Framework

### Core Concept
Every converter can be mapped from a **Design Space** (turns, geometry, frequency, materials) into a **Performance Space** (efficiency η vs. power density ρ). The boundary of the achievable region is the **Pareto front** — points where you cannot improve η without sacrificing ρ or vice versa.

### Quantified Trade-Off Examples (1-phase PFC, 3.2kW, 230V→400V)
| Design priority | η | ρ |
|---|---|---|
| Maximum power density | 95.8% | 5.5 kW/dm³ |
| Maximum efficiency | 99.2% | 1.1 kW/dm³ |

**Rule**: You cannot be at both corners simultaneously. Switching frequency is the main design variable that moves the operating point along the Pareto front.

### Component η-ρ Characteristics
Each component family defines its own limit curve. The **overall converter limit** is always worse than any individual component limit:
- Electrolytic capacitors: set ρ_max (storage density limit)
- Heatsink: sets ρ_H from semiconductor losses → cooling volume
- Auxiliary supply: sets efficiency lower bound (loss independent of P_out)
- **Inductor: defines the power density limit for ultra-efficient systems**

> **Key insight**: Converters with no output inductor (e.g. motor drives with built-in machine inductance) achieve far higher power densities. Reducing inductor requirement is the #1 lever for extreme power density.

---

## 2. Inductor Scaling Laws

### Frequency vs. Volume
- Inductor volume scales with **volt-seconds** (∝ V·D / f_sw)
- Higher frequency → smaller inductor, BUT with diminishing returns
- **Observed rule**: factor 10× in switching frequency gives only factor 2× in power density (practical systems include winding losses, EMI filter growth)
- Inductor losses **decrease** with increasing physical dimensions (heat extraction improves, loss density drops)

### Natural Convection Thermal Limit
Maximum allowable inductor surface loss density under natural convection:
- **Cube geometry**: lowest surface/volume ratio — worst case for cooling
- **Planar geometry**: highest surface/volume — best for natural cooling
- Shape factor analysis (D.B. Go, Notre Dame): a thin flat slab (k = h/a >> 1) has ~2× the effective cooling surface vs. a cube of equal volume
- **Critical**: natural convection boundary layer requires >5mm clearance — often violated in compact designs
- Water cooling can deliver extreme local power densities where natural convection is insufficient

### Explicit Heatsink for Magnetics — Validated Example
Phase-shift full-bridge with current-doubler rectifier:
- P_out = 5 kW, V_in = 400V, V_out = 48–56V, f_sw = 120 kHz
- Heat Transfer Component (HTC) + heatsink direct-bonded to transformer
- Result: **9 kW/dm³ @ 94.5%** — 1.6× higher power density than nat. conv.

### Part-Load Efficiency Behavior
For resistive-dominated losses (winding loss dominates):
- Losses ∝ I² ∝ P_out²
- High part-load efficiency achievable even if full-load efficiency is thermal-limited
- Allows optimizing for rated power density while retaining good partial-load profile

---

## 3. Optimal Loss Distribution in Magnetics

### Core vs. Winding Loss Split
For minimum total loss at fixed volume, the optimal split between core loss P_C and winding loss P_W is:

```
P_C / P_W = 2 / β
```

where β is the Steinmetz frequency exponent (typically β ≈ 2.5–3.0 for ferrites).

For β = 2.7: P_C / P_W ≈ 0.74 → winding losses should dominate slightly.

### AC Resistance Optimization
The corresponding optimal ratio of AC to DC winding resistance:
```
R_AC / R_DC = β / α
```
where α is the Steinmetz flux density exponent (typically α ≈ 2.0–2.5).

For Dowell/proximity effect: this translates to an optimal copper layer thickness relative to skin depth.

### Loss Regions
- **Region I (low frequency)**: Saturation-limited — core loss << winding loss
- **Region II (intermediate)**: Optimal operating point — balanced loss split
- **Region III (high frequency)**: Proximity effect dominant — winding losses escalate faster than core losses shrink

---

## 4. Multi-Airgap Inductors — Critical Findings

### Why Multi-Airgap?
- Single large gap → fringing flux → high winding eddy current losses near gap
- Distributed airgaps (N small gaps) reduce fringing flux density per gap
- Multi-airgap + multi-layer foil winding: very high filling factor + low HF losses

### Prototype (ETH Little Box entry)
- L = 10.5 μH, 2×8 turns
- **24 × 80 μm airgaps** in stacked ferrite plates
- Core material: DMR 51 (Hengdian), 0.61mm thick plates
- Winding: 20 μm copper foil, 4 in parallel, 7 μm Kapton isolation
- DC resistance: 20 mΩ, Q ≈ 600
- Dimensions: 14.5 × 14.5 × 22 mm³
- Magnetic shielding eliminates HF current through ferrite → avoids high core losses at the cost of slightly increased parasitic capacitance

### Core Loss vs. Number of Airgaps — Measured Result
**Losses increase linearly with the number N of introduced airgaps** (materials: DMR51, N59, N87; tested at 500 kHz).

| Configuration | ΔT at 100mT/750kHz |
|---|---|
| Solid core | 27.7°C |
| N=20 gaps | 73.5°C |

This is counterintuitive — more gaps should reduce fringing, but each cut surface introduces loss.

### Root Cause: Ferrite Surface Degradation from Machining
**Diamond saw cutting introduces mechanical stress → significant microstructural damage**:
- Loss factor increase: up to **7× higher** in cut surface layers vs. bulk
- Confirmed via electron microscopy (SEM) + focused ion beam (FIB) cross-sections on DMR51, N59, N87
- Effect is consistent across all three materials

### Fix: Chemical Etching
- **100 μm HCl etching of cut surface** partially restores crystal structure
- Removes damaged surface layer
- Polishing (5 μm) also effective but less thorough

### Practical Implication for Design
> When using stacked ferrite plates or custom-gap inductors: account for surface loss with an extended Steinmetz model. Add a term proportional to N (number of cuts) × P_surface_per_cut. Use vendor cores where possible (ground/lapped surfaces are better than saw-cut).

### Steinmetz Extension for Multi-Airgap
Total core loss with N airgaps:
```
P_total(N) = P_bulk + N × P_surface
```
P_surface per cut is material-dependent and frequency/flux-dependent. Linear fit is sufficiently accurate (validated on DMR51, N59, N87 at 250 kHz–1 MHz).

---

## 5. Medium-Frequency (MF) Transformer Design

### Optimal Frequency — Not Always "Higher is Better"
- Higher frequency → smaller transformer **only up to a certain limit**
- Proximity effect (Dowell) causes winding losses to grow faster than core losses shrink at high f
- Minimum volume and minimum weight occur at different frequencies (depend on strand diameter and winding width)
- Rule of thumb: there is a **sweet spot** — typically 10–100 kHz for power-dense MF transformers

### Validated 166 kW / 20 kHz SST Transformer
| Parameter | Value |
|---|---|
| Power rating | 166 kW |
| Efficiency | **99.5%** |
| Power density | **44 kW/dm³** |
| Frequency | 20 kHz |
| Core material | Nanocrystalline, 0.1mm airgaps between parallel cores |
| Winding | Litz wire: 10 bundles × 950 × 71 μm strands per bundle |
| Topology | Half-cycle DCM series resonant DC/DC |

**Design detail**: 0.1 mm airgaps between parallel nanocrystalline core stacks ensure equal flux partitioning across parallel cores.

**Litz wire detail**: Equal current partitioning enforced by common-mode chokes on each bundle.

### Key Design Freedoms for MF Transformers
1. Electric: number of turns N, operating frequency f
2. Geometric: core cross-section, window area, aspect ratio
3. Material: core (ferrite/nanocrystalline/amorphous) + winding (Litz/foil/PCB)
4. Cooling: heat conducting plates between cores, water-cooled top/bottom cold plates

---

## 6. Inductor Volt-Seconds Reduction Techniques

### Why Reduce Volt-Seconds?
Inductor size ∝ volt-seconds (V·s = V·D/f). Reducing volt-seconds directly shrinks the inductor.

### Multi-Level Converters
- N-level converter reduces inductor volt-seconds by **N²** factor
- 5-level flying capacitor converter (FCC): 320 kHz single-cell frequency, 12 μF flying caps, very small output inductor
- Note: FCC voltage balancing is challenging in certain operating conditions
- Basic patent: T. Meynard (1991)

### Interleaving Approaches
- **Parallel interleaving**: requires coupled inductor for current sharing
- **Series interleaving** (multi-level): no coupled inductor needed, identical spectral properties
- Series interleaving preferred — avoids coupling complexity

### EMI Consequence
Higher switching frequency increases required EMI filter attenuation. Above ~500 kHz, filter design becomes the binding constraint — not just the power inductor.

---

## 7. GaN/SiC Implications for Magnetics Design

### Semiconductor Performance Comparison (600–900V class)
- **GaN MOSFETs: best soft-switching performance** (lowest energy loss per switching event)
- Si and SiC: similar soft-switching performance to each other
- Si MOSFETs: almost no voltage-dependency of soft-switching losses
- GaN enables TCM (triangular current mode) operation at higher frequencies with lower switching losses

### TCM ZVS Operation — What It Enables
- Zero voltage switching (ZVS) at both turn-on and turn-off in full operating range
- Implemented via 4D-TCM interleaving (ETH Little Box)
- Variable switching frequency lowers EMI spectral density
- Requires only zero-crossing detection (i = 0)
- **Result**: 8.2 kW/dm³ @ 96.3% for 2 kW solar inverter (Little Box 1.0)

### "The Ideal Switch Is NOT Enough"
Even with zero switching losses AND zero conduction losses (ideal transistors):
- At 6 kW/dm³: η ≈ 99.35% limited by magnetics (L = 50 μH, f_sw = 500–900 kHz)
- L and f_sw remain **independent degrees of freedom** even with ideal semiconductors
- Magnetics losses define the ultimate efficiency limit, not semiconductors

---

## 8. Future Prospects — Roadmap

### Option 1: Improve Modeling and Optimization
- Better core loss models (beyond Steinmetz/iGSE — especially for multi-airgap, temperature-dependent behavior)
- Multi-objective optimization including full system (not just isolated magnetics)
- Design for manufacturing (tolerances, surface finish, assembly)

### Option 2: Improve Materials and Manufacturing
- PCB-based magnetics with high filling factor (VICOR-style)
- Advanced locally-adapted Litz wire
- Low-μ materials (distributed gap — avoids discrete gap surface losses)
- Low HF-loss materials (nanocrystalline, amorphous for higher frequencies)
- Integrated cooling structures

### Option 3: Minimize Inductor Requirement
- Multi-level converter topologies (reduce volt-seconds)
- Magnetic integration (couple multiple inductors into one core)
- Hybrid capacitor/inductor converters (reduce energy storage burden on magnetics)

---

## 9. Summary of Design Rules for Agent Use

| Rule | Value/Formula |
|---|---|
| Optimal core/winding loss split | P_C/P_W = 2/β (β ≈ 2.7 → P_C ≈ 74% of P_W) |
| Optimal AC/DC resistance ratio | R_AC/R_DC = β/α |
| Multi-airgap core loss | P_total = P_bulk + N × P_surface (linear in N) |
| Ferrite machining penalty | Up to 7× loss increase at cut surfaces |
| Fix for cut surfaces | 100 μm HCl etching restores ~bulk properties |
| Frequency scaling (practical) | 10× f_sw → ~2× power density (not 10×) |
| MF transformer sweet spot | ~10–100 kHz for power-dense designs |
| Natural convection limit | Requires >5mm boundary layer clearance |
| Planar > cube | For equal volume, planar shape gives ~2× better nat. conv. cooling |
| Inductor volt-seconds | V_s = V_DC × D / f_sw; reduce via N² multi-level |
| GaN advantage | Best ZVS soft-switching, enables TCM at higher f |
| Magnetics-limited ceiling | Even ideal switches → η limited by magnetics at high ρ |

---

*Source: J.W. Kolar et al., "Impact of Magnetics on Power Electronics Converter Performance – State of the Art and Future Prospects," PSMA Magnetics Workshop 2017, ETH Zurich.*
