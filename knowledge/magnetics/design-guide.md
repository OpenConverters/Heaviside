# Magnetics Design Guide

> This guide provides the theoretical foundation for magnetic component design in power electronics,
> drawn from McLyman (4th ed.), Hurley & Wolfle, and Kazimierczuk (2nd ed.).
> **All numerical computations should be performed using PyOpenMagnetics** -- the equations here
> are for design intuition and understanding the physics.

---

## 1. Primary Tool: PyOpenMagnetics

**ALWAYS use PyOpenMagnetics for calculations. NEVER use manual formulas in place of the library.**

PyOpenMagnetics handles complex magnetic field distributions, temperature-dependent material
properties, geometrical fringing effects, and searches a real commercial core database (1301+
shapes, 409+ materials) that manual formulas cannot replicate.

### Import (Critical -- most agents fail here)

The package has no `__init__.py`. A bare `import PyOpenMagnetics` gives an empty namespace.
You MUST use importlib:

```python
import importlib.util, os, glob

pkg_dir = os.path.join(
    os.path.dirname(__import__('PyOpenMagnetics').__path__[0]),
    'PyOpenMagnetics'
)
so_files = glob.glob(os.path.join(pkg_dir, 'PyOpenMagnetics.cpython-*'))
assert so_files, f"No .so/.pyd found in {pkg_dir}"

spec = importlib.util.spec_from_file_location('PyOpenMagnetics', so_files[0])
PyOM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PyOM)

# MANDATORY -- must call before any other function
PyOM.load_databases({})
```

### Key Design Functions

| Function | Purpose |
|---|---|
| `design_magnetics_from_converter()` | **Method A (preferred)** -- converter spec to complete ranked designs in one call |
| `process_converter()` + `calculate_advised_magnetics()` | **Method B** -- two-step when ngspice unavailable |
| `calculate_inductance(magnetic)` | Post-design inductance verification |
| `calculate_core_losses(core, coil, op, models)` | Core loss analysis (Steinmetz, iGSE, Roshen, etc.) |
| `calculate_winding_losses(magnetic, op, temp)` | Winding loss with skin/proximity effects |
| `simulate(mas)` | Full electromagnetic simulation |
| `calculate_effective_skin_depth(material, freq, temp)` | Skin depth at frequency |

### Method A: `design_magnetics_from_converter()` (Single Call)

```python
result = PyOM.design_magnetics_from_converter(
    "flyback",           # topology_name (positional) -- MUST be lowercase
    converter_dict,      # converter_json (positional) -- Python dict, NEVER json.dumps()
    3,                   # max_results
    "available cores",   # core_mode_json -- lowercase with space!
    True,                # use_ngspice (ignored internally but required)
    None                 # weights_json -- or {"efficiency": 2.0, "cost": 1.0, "dimensions": 0.5}
)
```

### Method B: `process_converter()` then `calculate_advised_magnetics()`

```python
processed = PyOM.process_converter("flyback", advanced_converter_dict, use_ngspice=False)
mas_inputs = {
    "designRequirements": processed["designRequirements"],
    "operatingPoints": processed["operatingPoints"]
}
designs = PyOM.calculate_advised_magnetics(
    mas_inputs, 3, "available cores",
    {"efficiency": 2.0, "cost": 1.0, "dimensions": 0.5}
)
```

### Critical Rules

1. **NEVER use `json.dumps()`** -- pass Python dicts directly. `json.dumps()` converts to string, causing schema errors.
2. **NEVER invent JSON structures** -- use the exact MAS schemas (see AGENTS.md Section 5).
3. **NEVER abandon Method A after an error** -- fix the JSON, do not fall back to manual calculations.
4. **Use positional arguments** -- the `.pyi` has wrong keyword names.
5. **Use `"standard cores"` for faster results** (10-30s vs 60-180s for `"available cores"`).
6. Topology strings are lowercase: `"flyback"`, `"buck"`, `"boost"`, `"single_switch_forward"`, `"llc"`, etc.
7. Flyback/Forward use PLURAL fields (`outputVoltages`, `outputCurrents`); Buck/Boost use SINGULAR.

### Database Access

```python
PyOM.get_core_shape_names(True)          # list of ~1301 shape names (True = include toroidal)
PyOM.get_core_shape_families()           # shape families (NO argument!)
PyOM.find_core_shape_by_name("E 25/13/7")
PyOM.get_core_material_names()           # ~409 material names
PyOM.find_core_material_by_name("3C95")
PyOM.get_wire_names()
PyOM.find_wire_by_name("Round 0.5 - Grade 1")
```

**Note:** Use `find_*_by_name()`, NOT `get_*_by_name()` for individual lookups.

---

## 2. Core Selection Methodology

### 2.1 Area Product (Ap) Method

The area product Ap = Wa * Ac (window area times core cross-section area, in cm^4) is the
classical figure of merit relating a core's geometry to its power-handling or energy-storage
capability.

**For transformers** (McLyman Ch.7, Hurley Ch.5):

```
Ap = Pt * 10^4 / (Bac * f * J * Kf * Ku)    [cm^4]
```

Where:
- Pt = apparent power (Pin + Po) [W]
- Bac = AC flux density [T]
- f = frequency [Hz]
- J = current density [A/cm^2]
- Kf = waveform factor (4.0 for square wave, 4.44 for sine wave)
- Ku = window utilization factor (typically 0.3-0.4 for transformers, 0.2-0.8 range)

**For inductors** (McLyman Ch.8, Hurley Ch.3):

```
Ap = (2 * Energy * 10^4) / (Bm * J * Ku)     [cm^4]
```

Where Energy = L * I_pk^2 / 2 [watt-seconds].

From Hurley, the more refined version incorporating thermal limits:

```
Ap = [ sqrt(1+g) * Ki * L * I_hat^2 / (Bmax * Kt * sqrt(ku * DT)) ]^(8/7)
```

Where Kt = sqrt(hc * ka / (rw * kw)) is a thermal-dimensional constant (~48.2e3 for typical values),
and g = Pfe/Pcu is the core-to-winding loss ratio.

> **PyOpenMagnetics:** Use `CoreAdviser` (via `calculate_advised_magnetics`) instead of
> manually computing Ap. The library searches the full commercial core database and optimizes
> across multiple criteria simultaneously.

### 2.2 Core Geometry Coefficient (Kg) Method

McLyman's Kg approach provides tighter control by incorporating regulation requirements:

```
Kg = (Wa * Ac^2 * Ku) / MLT    [cm^5]
```

**For transformers** (targeting a specific regulation alpha):

```
Kg = Pt * 10^4 / (2 * Ke * alpha)
```

Where Ke = 0.145 * Kf^2 * f^2 * Bm^2 * 10^-4.

**For inductors:**

```
Kg = Energy^2 / (Ke * alpha)    [cm^5]
```

Where Ke = 0.145 * Po * Bpk^2 * 10^-4.

The Kg method directly accounts for copper loss and regulation, giving a more optimized
core selection than the Ap method alone.

> **PyOpenMagnetics:** The library's internal core adviser algorithm evaluates cores using
> multi-objective optimization (efficiency, cost, dimensions) which subsumes the Kg approach.

### 2.3 Practical Core Selection Guidelines

| Core Type | Typical Application | Frequency Range |
|---|---|---|
| Ferrite (MnZn) | High-frequency SMPS transformers/inductors | 10 kHz - 2 MHz |
| Ferrite (NiZn) | EMI filters, very high frequency | 200 kHz - 100 MHz |
| Silicon steel | Low-frequency power transformers | 50 Hz - 2 kHz |
| Amorphous alloy | High-efficiency medium-frequency | up to 250 kHz |
| Nanocrystalline | High-frequency, high Bsat | up to 250 kHz |
| Iron powder (MPP) | DC inductors with bias | 1 kHz - 1 MHz |

**Ferrite materials for SMPS** (from McLyman Table 7-1):
- MnZn ferrites: Bsat = 0.3-0.5 T, initial permeability 750-15000
- NiZn ferrites: Bsat = 0.3-0.4 T, initial permeability 200-1500

**Window utilization factor Ku** (McLyman Ch.4):
- Single winding, bobbin wound: 0.4-0.5
- Multiple windings with isolation: 0.25-0.35
- Toroidal cores: 0.2-0.35
- Planar/PCB windings: 0.25-0.35

---

## 3. Inductor Design Procedure

### 3.1 Step-by-Step Procedure (Hurley Ch.3, McLyman Ch.8)

**Step 1: Determine Specifications**
- Required inductance L
- DC current Io and AC ripple current DeltaI
- Switching frequency f
- Maximum temperature rise DeltaT
- Ambient temperature

**Step 2: Calculate Peak Current and Stored Energy**
```
I_pk = Io + DeltaI/2
Energy = L * I_pk^2 / 2    [J]
```

**Step 3: Select Core Material**
- For DC inductors in SMPS: ferrite with gap, or powder core
- Choose Bmax based on saturation limit (typically 0.2-0.3 T for ferrites at high frequency)
- Consider temperature derating of Bsat

**Step 4: Calculate Ap (or Kg) and Select Core**
```
Ap = (2 * Energy * 10^4) / (Bm * J * Ku)    [cm^4]
```
Select the next larger standard core from manufacturer catalogs.

> **PyOpenMagnetics handles Steps 3-4 automatically** via `calculate_advised_magnetics()`.

**Step 5: Calculate Number of Turns**
From Hurley:
```
N = sqrt(L / AL)
```
Where AL is the inductance factor for the selected core and gap.

Alternatively, from the inductance equation:
```
N = L * I_pk / (Bmax * Ac)
```

**Step 6: Calculate Air Gap**
```
lg = mu_0 * N^2 * Ac / L - lc/mu_r
```
For gapped ferrite cores, the gap dominates and:
```
lg ~ mu_0 * N^2 * Ac / L
```

> **PyOpenMagnetics:** Gap calculation is handled internally. Use `calculate_inductance(magnetic)`
> to verify.

**Step 7: Calculate Wire Size**
```
Aw = I_rms / J
```
Where J is the current density from the thermal constraint:
```
J = Kt * sqrt(DeltaT / (ku * (1 + g))) / Ap^(1/8)
```
Typical J: 200-500 A/cm^2 for natural convection.

**Step 8: Verify Losses and Temperature Rise**
- Winding loss: Pcu = rho_w * N * MLT * I_rms^2 / Aw
- Core loss: Pfe = Kc * f^a * (DeltaB/2)^b * Vc
- Temperature rise: DeltaT = R_theta * (Pcu + Pfe)

> **PyOpenMagnetics:** Use `calculate_winding_losses()` and `calculate_core_losses()` for
> accurate results including skin and proximity effects.

### 3.2 Critical Inductance (McLyman Ch.8)

For buck converters, the critical inductance to maintain continuous conduction mode:
```
L_crit = Vo * T * (1 - D_min) / (2 * Io_min)
```
Where D_min = Vo / (Vin_max * eta).

If the inductor current goes discontinuous, output voltage regulation degrades significantly.
For slaved outputs in multi-output converters, the inductor must never go discontinuous.

### 3.3 Powder Core Inductors (McLyman Ch.9)

Molypermalloy (MPP), Kool-Mu, and iron powder cores have distributed gaps. Design uses
effective permeability curves that show inductance rolloff with DC bias:
- MPP: retains ~80% inductance at 0.3 T bias (rapid falloff beyond)
- Gapped ferrite: retains ~100% inductance up to near Bsat (then cliff)

For powder cores, iterate: select core, check permeability at operating bias, recalculate turns.

---

## 4. Transformer Design Procedure

### 4.1 Step-by-Step Procedure (McLyman Ch.7, Hurley Ch.5)

**Step 1: Determine Specifications**
- Input voltage range, output voltage(s) and current(s)
- Operating frequency
- Efficiency target
- Maximum temperature rise
- Isolation requirements

**Step 2: Calculate Apparent Power**
```
Pt = Pin + Po = Po * (1 + 1/eta)
```

**Step 3: Select Core Material and Bmax**
For SMPS frequencies (50-500 kHz), MnZn ferrite is typical.
Optimum Bmax from Hurley (when not saturation-limited):
```
Bo = f(hc, ka, DeltaT, rw, kw, ku, Kc, f, a, SVA)
```
If Bo > Bsat, use Bmax = Bsat (saturation-limited design).

> **PyOpenMagnetics:** Core material selection and Bmax optimization are handled automatically.

**Step 4: Calculate Ap and Select Core**
```
Ap = Pt * 10^4 / (Bac * f * J * Kf * Ku)    [cm^4]
```

**Step 5: Calculate Primary Turns**
From Faraday's law:
```
Np = Vp * 10^4 / (Ac * Bac * f * Kf)    [turns]
```

**Step 6: Calculate Secondary Turns**
```
Ns = Np * (Vo + Vd) / (Vin * eta)
```
Or from the turns ratio: Ns = Np / n.

**Step 7: Select Wire Sizes**
Based on current density J and window allocation between windings.

**Step 8: Check Window Utilization**
```
Ku_actual = (Np * Awp + Ns * Aws) / Wa
```
Must be <= Ku target.

**Step 9: Calculate Losses and Verify Thermal**
- Winding losses (including high-frequency effects from Ch.6)
- Core losses (using iGSE for non-sinusoidal excitation)
- Total loss must keep temperature within limits

> **PyOpenMagnetics:** `design_magnetics_from_converter()` performs all steps automatically
> and returns ranked designs with complete loss breakdowns.

### 4.2 Flyback Transformer Design (McLyman Ch.13)

The flyback transformer is actually a coupled inductor -- it stores energy during the ON period
and transfers it during the OFF period.

**Key relationships for flyback (McLyman):**

Discontinuous mode:
```
L = Vin^2 * D^2 * T / (2 * Po)          [simplified]
I_pk = 2 * Po / (Vin * D * eta)
```

Continuous mode:
```
L = Vin * D * T / DeltaI
I_pk = Io_reflected + DeltaI/2
```

The turns ratio sets the reflected voltage:
```
n = Np/Ns = (Vin * D) / ((Vo + Vd) * (1-D))
```

> **PyOpenMagnetics:** Use `design_magnetics_from_converter("flyback", ...)` with Method A.
> It computes optimal inductance, turns ratio, and operating waveforms automatically from the
> converter specification. For Method B, pre-calculate desiredInductance and desiredTurnsRatios.

### 4.3 Forward Converter Transformer (McLyman Ch.14)

Unlike the flyback, the forward converter transformer transfers energy directly (not stored).
The transformer must reset each cycle (volt-second balance).

Key constraint: D_max < n_reset / (1 + n_reset), where n_reset is the reset winding ratio.
For a two-switch forward: D_max = 0.5 (no reset winding needed).

---

## 5. Loss Models

### 5.1 Core Losses

#### Steinmetz Equation (Original -- Sinusoidal Excitation Only)

From Kazimierczuk (2.194) and Hurley (1.29):
```
Pv = Kc * f^a * Bm^b    [W/m^3 or mW/cm^3]
```

Where:
- Kc, a, b are empirical constants specific to the core material
- Bm is the peak AC flux density amplitude
- f is the excitation frequency
- Typical values: a = 1.1-1.7, b = 2.0-2.8 depending on material

**This equation is ONLY valid for sinusoidal excitation.** Power electronics waveforms are
typically non-sinusoidal (square wave, triangular, etc.), requiring the iGSE.

#### Improved Generalized Steinmetz Equation (iGSE)

From Hurley (7.27-7.29):
```
Pv = (1/T) * integral_0^T [ ki * |dB/dt|^a * |DeltaB|^(b-a) ] dt
```

Where:
```
ki = Kc / [ 2^(b-1) * pi^(a-1) * (1.1044 + 6.8244/(a+1.354)) ]
```

The iGSE uses the same Kc, a, b coefficients as the original Steinmetz equation but correctly
handles arbitrary waveforms by integrating the instantaneous rate of change of flux density.

**For piecewise-linear waveforms** (common in SMPS), the iGSE simplifies. For a two-slope
waveform with duty cycle D:
```
Pv = ki * |DeltaB|^b * (1/T) * [ D^(1-a) + (1-D)^(1-a) ] * |DeltaB/DT|^a ... (simplified)
```

The key insight: at the same peak flux density, a square wave produces MORE core loss than
a sine wave because it has a higher dB/dt.

#### Core Losses with DC Bias (Gapped Cores)

From Kazimierczuk (2.226-2.227): When the core has a gap, the effective flux density is reduced:
```
Bm = mu_0 * N * Im / (lg + lc/mu_rc)
```

The DC bias itself does not directly contribute to hysteresis loss (only the AC swing does),
but it shifts the operating point on the B-H curve, potentially increasing incremental losses.

> **PyOpenMagnetics:** Use `calculate_core_losses(core, coil, operating_point, models)` with
> `models = {"coreLosses": "IGSE"}` (or `"STEINMETZ"`, `"ROSHEN"`, etc.). The library
> implements all these models with proper material data from its internal database.

### 5.2 Winding Losses

#### DC Resistance

```
Rdc = rho_w * lw / Aw = rho_w * N * MLT / Aw
```

Where rho_w = 1.72e-8 ohm-m for copper at 20C, with temperature correction:
```
rho(T) = rho_20 * [1 + alpha_20 * (T - 20)]
```
alpha_20 = 0.00393 for copper.

#### Skin Effect

From Kazimierczuk Ch.3: At high frequencies, current crowds to the surface of the conductor.
The skin depth is:
```
delta = sqrt(rho_w / (pi * mu_0 * f))
```

For copper at room temperature:
- 100 kHz: delta = 0.21 mm
- 500 kHz: delta = 0.094 mm
- 1 MHz: delta = 0.066 mm

**Rule of thumb:** Wire diameter should be less than 2*delta to avoid significant skin effect losses.

#### Proximity Effect

From Kazimierczuk Ch.4-5: In multilayer windings, the magnetic field from adjacent layers
forces current redistribution, dramatically increasing AC resistance. The proximity effect
typically dominates over skin effect for multilayer windings (Nl >= 2).

#### Dowell's Equation

From Kazimierczuk (5.109-5.110), the AC-to-DC resistance ratio for foil windings:

```
FR = Rw/Rdc = A * [ sinh(2A)+sin(2A) / (cosh(2A)-cos(2A))
                    + (2*(Nl^2-1)/3) * (sinh(A)-sin(A)) / (cosh(A)+cos(A)) ]
```

Where:
- A = h/delta_w (conductor thickness normalized to skin depth)
- Nl = number of winding layers
- First term = skin effect contribution
- Second term = proximity effect contribution

**Low-frequency approximation** (A <= 1.5, Kazimierczuk 5.119):
```
FR ~ 1 + (5*Nl^2 - 1)/45 * A^4
```

This shows that proximity losses scale as Nl^2 and A^4 -- doubling the number of layers
quadruples the proximity loss, and doubling the frequency increases it by 2^2 = 4x.

**High-frequency approximation** (A > 3, Kazimierczuk 5.123):
```
FR ~ (2*Nl^2 + 1)/3 * A
```

At high frequencies, FR grows linearly with A (i.e., sqrt(f)), but the Nl^2 factor means that
a 10-layer winding has ~67x the AC resistance of a single layer.

#### Dowell's Equation for Round Wire

Dowell's equation was derived for foil windings. For round wire, an equivalent foil thickness
is used (Kazimierczuk 5.17):
```
A_round = (pi/4)^(3/4) * d/delta_w    (for round wire of diameter d)
```

> **PyOpenMagnetics:** Use `calculate_winding_losses(magnetic, operating_point, temperature)`.
> The library implements Dowell, Ferreira, and other models with proper layer counting and
> conductor geometry handling.

### 5.3 Interleaving Windings to Reduce Proximity Effect

From Hurley (6.4) and Kazimierczuk: Interleaving primary and secondary windings reduces the
effective number of layers seen by each winding portion, dramatically reducing proximity losses.

Example: A transformer with P-S arrangement has Nl layers for each winding.
With P-S-P-S interleaving, the effective Nl is halved for each portion, reducing proximity
losses by ~4x (since loss scales as Nl^2).

The tradeoff: interleaving increases interwinding capacitance, which may cause common-mode
noise issues.

---

## 6. Wire Selection

### 6.1 Solid Round Wire

- Simplest and cheapest option
- Effective when d < 2*delta (wire diameter less than twice the skin depth)
- For higher frequencies, use multiple strands in parallel or switch to Litz wire
- Standard AWG sizes: use wire tables to match required current density

### 6.2 Litz Wire

From Kazimierczuk (5.23): Litz wire consists of many individually insulated fine strands,
twisted/braided together to equalize current distribution and reduce proximity effect.

**When to use Litz wire:**
- Operating frequency > 50 kHz AND multiple winding layers
- When FR (AC/DC resistance ratio) with solid wire exceeds ~1.5-2.0
- Individual strand diameter should be < delta_w (skin depth at operating frequency)

**Dowell's equation for Litz wire** (Kazimierczuk 5.379):
```
FR_litz = A_l * [ sinh(2A_l)+sin(2A_l) / (cosh(2A_l)-cos(2A_l))
                 + (2*(Nl^2*k - 1)/3) * (sinh(A_l)-sin(A_l)) / (cosh(A_l)+cos(A_l)) ]
```

Where:
- k = number of strands per bundle
- A_l = (pi/4) * 0.75 * dl / (delta_w * sqrt(eta))
- dl = strand diameter
- eta = fill factor (typically < 0.8)
- Nll = Nl * sqrt(k) is the effective number of layers

**Key tradeoff:** Litz wire is 40%+ more expensive, takes ~40% more winding space, and has
higher DC resistance than solid wire of equivalent copper area. It only helps when AC losses
would otherwise dominate.

### 6.3 Foil Conductors

- Best for high-current, low-voltage windings (e.g., secondary of a step-down transformer)
- Excellent window utilization
- Optimum foil thickness from Kazimierczuk (5.16.1): depends on number of layers and frequency
- For single layer: optimum thickness ~ pi * delta_w (full skin depth utilization)
- For multiple layers: optimum thickness decreases with Nl

### 6.4 Wire Selection Rules of Thumb

| Frequency Range | Recommendation |
|---|---|
| < 20 kHz | Solid round wire, d < 2*delta |
| 20-100 kHz | Solid wire with d < delta, or Litz wire for multilayer |
| 100-500 kHz | Litz wire (strand d ~ 0.1 mm) or thin foil |
| > 500 kHz | Fine Litz wire (strand d ~ 0.05 mm) or PCB traces |

---

## 7. Thermal Estimation

### 7.1 Surface Area Method (Hurley)

The simplest thermal model uses Newton's law of convection:
```
DeltaT = R_theta * Q = Q / (hc * At)
```

Where:
- Q = total power loss (core + winding) [W]
- hc = convective heat transfer coefficient [W/m^2-C]
- At = total surface area of the wound component [m^2]
- R_theta = 1/(hc * At) = thermal resistance [C/W]

**Typical values of hc:**
- Natural convection: hc = 8-12 W/m^2-C (typical: 10)
- Forced convection (fan): hc = 10-30 W/m^2-C
- hc for vertical object of height H: hc = 1.42 * (DeltaT/H)^0.25

### 7.2 Volume-Based Thermal Resistance (Hurley)

An empirical formula relating thermal resistance to core volume:
```
R_theta = 0.06 / sqrt(Vc)    [C/W, Vc in m^3]
```

### 7.3 Surface Area from Area Product (Dimensional Analysis)

From Hurley (3.25-3.27), the physical dimensions relate to Ap:
```
At = ka * Ap^(1/2)          (surface area, ka ~ 40)
Vc = kc * Ap^(3/4)          (core volume, kc ~ 5.6)
Vw = kw * Ap^(3/4)          (winding volume, kw ~ 10)
```

### 7.4 Watt Density Method (McLyman)

McLyman uses the watt density (power dissipated per unit surface area):
```
psi = P_total / At    [W/cm^2]
```

Temperature rise from watt density:
```
Tr = 450 * psi^0.826    [C]    (empirical, for natural convection)
```

### 7.5 Thermal Design Rules of Thumb

- **Maximum hot-spot temperature:** 100-120C for most ferrites (Curie temp ~200C for MnZn)
- **Target temperature rise:** 25-50C for reliable operation
- **Power density limit:** ~100 mW/cm^3 for natural convection, ~300 mW/cm^3 with forced air
- Core loss and winding loss should be roughly balanced at the optimum design point
  (Hurley Ch.5: Pfe ~ Pcu at the optimum when not saturation-limited)
- At high frequency, core losses tend to dominate, pushing toward lower Bmax

---

## 8. Practical Design Tips

### 8.1 Common Pitfalls

1. **Ignoring proximity effect:** The biggest source of unexpected winding loss at high frequency.
   A 5-layer winding at 200 kHz can have 10-50x the DC resistance. Always check FR.

2. **Using Bsat at room temperature:** Ferrite Bsat drops significantly with temperature.
   3C95 has Bsat ~ 0.53 T at 25C but ~ 0.38 T at 100C. Design for the hot Bsat.

3. **Neglecting fringing flux at the gap:** Fringing flux causes localized heating in conductors
   near the gap. Keep windings away from the gap by at least one gap length. McLyman Ch.8
   provides fringing flux correction factors.

4. **Forgetting core loss contribution from harmonics:** Rectangular waveforms have higher dB/dt
   than sinusoidal. Using the Steinmetz equation with peak B underestimates losses.
   Always use iGSE for SMPS waveforms.

5. **DC bias effect on powder cores:** Inductance of powder cores rolls off significantly with
   DC bias. A core rated at 100 uH may only provide 50 uH at operating current. Always
   check the permeability vs. DC bias curve.

6. **Window utilization over-estimation:** Real Ku is often lower than theoretical due to
   bobbin walls, insulation tape, lead clearance, and imperfect packing. Use Ku = 0.3-0.4
   for typical bobbin-wound designs.

### 8.2 Rules of Thumb

- **Current density:** 200-500 A/cm^2 for natural convection, up to 1000 A/cm^2 with forced cooling
- **Flux density for ferrites at high frequency:**
  - 100 kHz: Bmax ~ 0.15-0.25 T
  - 500 kHz: Bmax ~ 0.05-0.10 T
  - Core loss roughly doubles for every 50% increase in Bmax (since b ~ 2.0-2.8)
- **Turns ratio accuracy:** Wind secondary first on a bobbin for better coupling; interleave for
  low leakage inductance
- **Gap placement:** Center-leg gap preferred (less fringing to windings); distributed gap
  (multiple smaller gaps) reduces fringing further
- **Minimum turns:** More turns = better inductance control but more winding loss.
  Fewer turns = higher flux density, higher core loss. The optimum balances both.
- **Efficiency target:** Core + winding losses typically 1-3% of throughput power for a
  well-designed transformer

### 8.3 Design Iteration Strategy

1. Start with PyOpenMagnetics `design_magnetics_from_converter()` -- it explores the full
   design space automatically.
2. Review the top-ranked designs for reasonableness (check Bmax, J, temperature rise).
3. If the design needs refinement, adjust weights: `{"efficiency": 3.0, "dimensions": 0.5}`
   for efficiency-optimized, or `{"dimensions": 3.0, "efficiency": 1.0}` for size-optimized.
4. Use `calculate_core_losses()` and `calculate_winding_losses()` for detailed loss analysis.
5. Verify thermal performance using the surface-area or volume-based methods above.
6. If the design is thermally marginal, consider: larger core, interleaved windings, Litz wire,
   lower Bmax, or forced convection.

---

## References

- McLyman, Colonel William T. *Transformer and Inductor Design Handbook*, 4th Edition. Taylor & Francis, 2011.
- Hurley, W. G. and Wolfle, W. H. *Transformers and Inductors for Power Electronics: Theory, Design and Applications*. Wiley, 2013.
- Kazimierczuk, Marian K. *High-Frequency Magnetic Components*, 2nd Edition. Wiley, 2014.
- PyOpenMagnetics AGENTS.md (PyMKF repository) -- definitive API reference for all computation.
- Erickson, R. W. and Maksimovic, D. *Fundamentals of Power Electronics*, 3rd Edition. Springer, 2020.

---

## Erickson Magnetics Design Method (from Erickson Part III)

Sources: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed (2020), Chapters 10-12 (pp.418-513) and Appendix B (pp.1040-1047).

This section covers Erickson's Kg and Kgfe design methods, which complement the Ap-based and Hurley/McLyman methods already documented above. The Erickson approach is distinctive in its clean separation of the two design cases: (1) copper-loss-dominated designs (filter inductors, using Kg), and (2) designs where both core and copper loss matter (transformers, AC inductors, using Kgfe with optimal delta-B).

### 9.1 The Kg Method for Filter Inductors (Erickson Ch11, pp.468-491)

The Kg (core geometrical constant) method targets a **specified copper loss** with a **given maximum flux density Bmax**. Core loss is assumed negligible (valid for filter inductors where the AC flux swing is small compared to the DC bias).

#### Four Design Constraints

Starting from the filter inductor requirements (inductance L, peak current Imax, winding resistance R, fill factor Ku, max flux density Bmax), Erickson derives four constraints (p.470-472):

1. **Maximum flux density**: n * Imax = Bmax * lg / mu_0 (from magnetic circuit with air gap dominating)
2. **Inductance**: L = mu_0 * Ac * n^2 / lg
3. **Winding area**: Ku * WA >= n * AW (wire must fit in window)
4. **Winding resistance**: R = rho * n * MLT / AW

Eliminating the unknowns (n, lg, AW) from these four equations yields the core selection inequality (Eq. 11.14, p.472):

```
Ac^2 * WA / MLT >= rho * L^2 * Imax^2 / (Bmax^2 * R * Ku)
```

The left side is the **core geometrical constant Kg** (units: cm^5):

```
Kg = Ac^2 * WA / MLT    [cm^5]
```

**Physical meaning**: Kg captures the electrical capability of a core geometry. Larger Ac (more flux capacity) or larger WA (more winding space) increase Kg. Larger MLT (longer wire per turn, more resistance) decreases Kg. A core must satisfy Kg >= (required Kg from specs).

#### Step-by-Step Kg Design Procedure (p.473-474)

1. **Determine core size**: Kg >= rho * L^2 * Imax^2 / (Bmax^2 * R * Ku) * 10^8 [cm^5]
   - Select the smallest standard core whose Kg exceeds this value
2. **Number of turns**: n = L * Imax / (Bmax * Ac) * 10^4
3. **Air gap length**: lg = mu_0 * Ac * n^2 / L * 10^-4 [meters]
   - Or equivalently, required AL = 10 * Bmax^2 * Ac^2 / (L * Imax^2) [mH/1000 turns]
4. **Wire size**: AW <= Ku * WA / n [cm^2]
5. **Verify winding resistance**: R = rho * n * MLT / AW

**Note**: This is a first-pass procedure. Fringing flux (which increases inductance) means a somewhat longer gap may be needed. Proximity losses, temperature rise, and turns roundoff require subsequent iteration.

#### Window Area Allocation for Multiple Windings (pp.474-479)

For multi-winding devices (coupled inductors, flyback transformers), Erickson proves via Lagrange multipliers that **total copper loss is minimized when window area is allocated in proportion to each winding's apparent power** (Eq. 11.36, p.468):

```
alpha_m = Vm * Im / sum(Vj * Ij)
```

where alpha_m is the fraction of window area for winding m, and Vm*Im is its apparent power (rms voltage times rms current).

The resulting minimum total copper loss is (Eq. 11.34):

```
Pcu_tot = rho * MLT / (WA * Ku) * [sum(nj * Ij)]^2
```

**Example**: For a PWM full-bridge transformer with center-tapped secondary at D = 0.75, the optimal allocation is: primary 40%, each secondary half 30% (p.469).

#### Multiple-Winding Kg (Coupled Inductor / Flyback, pp.479-490)

For coupled inductors or flyback transformers, the Kg design constraint becomes (Eq. 11.58):

```
Kg >= rho * LM^2 * Imax^2 * sum(nj/n1 * Ij)^2 / (Bmax^2 * R1 * Ku * I1^2) * 10^8
```

where LM is the magnetizing inductance referred to winding 1, and Imax is the peak magnetizing current.

**Flyback transformer design example** (p.485-490): A CCM flyback at 200 kHz, 150W. The Kg method yields the core, gap, and turns; after the first pass, proximity losses are evaluated and the design iterated if needed.

### 9.2 The Kgfe Method for Transformers and AC Inductors (Erickson Ch12, pp.493-510)

When **core loss is significant** (conventional transformers, AC inductors), the operating flux density delta-B becomes a design variable to be optimized. This leads to the Kgfe geometrical constant.

#### The Core Loss vs. Copper Loss Tradeoff (pp.496-497)

This is the **central insight** of Erickson's transformer design:

- **Core loss** Pfe = Kfe * (delta-B)^beta * Ac * lm -- increases with delta-B
- **Copper loss** Pcu = [rho * lambda1^2 * Itot^2 / (4*Ku)] * MLT / (WA * Ac^2) * (1/delta-B)^2 -- decreases with delta-B (fewer turns needed at higher delta-B)
- **Total loss** Ptot = Pfe + Pcu has a minimum at the optimal delta-B

**The optimum does NOT occur where Pfe = Pcu** (a common misconception). Rather, it occurs where:

```
dPfe/d(delta-B) = -dPcu/d(delta-B)
```

Since Pfe ~ (delta-B)^beta and Pcu ~ (delta-B)^(-2), at the optimum:

```
Pfe/Pcu = 2/beta
```

For typical ferrite with beta = 2.6: Pfe/Pcu = 0.77 at the optimum (i.e., copper loss slightly exceeds core loss). For beta = 2.0, Pfe = Pcu exactly. The common rule-of-thumb "equal core and copper losses at the optimum" is only exactly true for beta = 2.

#### Optimal Flux Density (Eq. 12.13, p.489)

```
delta-B_opt = [ rho * lambda1^2 * Itot^2 / (2*Ku) * MLT / (WA * Ac^3 * lm) * 1/(beta * Kfe) ] ^ (1/(beta+2))
```

where:
- lambda1 = applied primary volt-seconds per half-cycle [V-sec]
- Itot = sum of rms winding currents referred to primary [A]
- Kfe = core loss coefficient [W / (cm^3 * T^beta)] at the operating frequency
- beta = core loss exponent (typically 2.6-2.8 for ferrite)

If delta-B_opt > Bsat (after accounting for any DC bias), the design is saturation-limited and must use the Kg method instead with Bmax = Bsat.

#### The Kgfe Geometrical Constant (Eq. 12.16-12.17, p.489)

```
Kgfe = WA * Ac^(2*(beta-1)/beta) / (MLT * lm^(2/beta)) * u(beta)
```

where u(beta) = [beta/2^(-beta/(beta+2)) + beta/2^(2/(beta+2))]^(-(beta+2)/beta). For beta = 2.7, u(2.7) = 0.305.

**Core selection**: Choose a core satisfying:

```
Kgfe >= rho * lambda1^2 * Itot^2 * Kfe^(2/beta) / (4 * Ku * Ptot^((beta+2)/beta)) * 10^8
```

Like Kg, Kgfe is tabulated for standard core families in Appendix B. Unlike Kg, Kgfe depends weakly on beta, but varies by less than 5% over the practical range 2.6-2.8.

#### First-Pass Transformer Design Procedure (pp.498-499)

1. **Determine core size**: Evaluate required Kgfe from specs, select core from tables
2. **Evaluate optimal delta-B**: Use Eq. 12.20 (= Eq. 12.13 with core dimensions substituted). Check that delta-B + any DC bias < Bsat.
3. **Primary turns**: n1 = lambda1 / (2 * delta-B * Ac) * 10^4
4. **Other winding turns**: From desired turns ratios
5. **Window allocation**: alpha_j from apparent power ratios (Section 9.1 above)
6. **Wire sizes**: AW_j = alpha_j * Ku * WA / nj
7. **Iterate**: Check proximity losses, adjust effective rho if needed (rho_eff = rho * Rac/Rdc), repeat

#### Example: Transformer Size vs. Frequency (pp.500-504)

Erickson's Cuk converter example (P = 150W, 5:1 turns ratio) shows that transformer size decreases with frequency up to ~250 kHz (due to reduced lambda1), then increases above ~250 kHz (due to increased core loss requiring reduced delta-B). The optimal frequency depends on the core material, power level, and loss budget.

### 9.3 AC Inductor Design via Kgfe (pp.507-510)

An AC inductor (no DC bias, such as a resonant inductor or filter inductor in an AC application) is designed like a transformer but with a single winding and an air gap. The Kgfe method applies directly, with:

```
Kgfe >= rho * L^2 * Iac^2 * Kfe^(2/beta) / (4 * Ku * Ptot^((beta+2)/beta)) * 10^8
```

The AC inductor requires a gap to obtain the desired inductance, but unlike the filter inductor, the gap length is determined by delta-B_opt rather than by Bmax.

### 9.4 Magnetic Device Categories and Their B-H Loops (Erickson Ch10, pp.453-459)

Erickson classifies magnetic devices by their B-H operating trajectories:

| Device | DC Bias? | AC Swing | Dominant Loss | Design Method |
|---|---|---|---|---|
| **Filter inductor** | Large DC (from load current) | Small AC ripple | Copper (DC) | Kg, specify Bmax near Bsat |
| **AC inductor** | None | Large AC | Both core and copper | Kgfe, optimize delta-B |
| **Conventional transformer** | None (or negligible magnetizing current) | Determined by applied V-sec | Both core and copper | Kgfe, optimize delta-B |
| **Coupled inductor** | Large DC (magnetizing + reflected load) | Small AC ripple | Copper (DC) | Kg, specify Bmax |
| **Flyback transformer** | Large DC (magnetizing current) | Moderate AC | Both (depends on CCM/DCM) | Kg or Kgfe depending on AC ripple magnitude |

**Key practical point**: In a flyback transformer operating in CCM with small ripple, the DC magnetizing current is large and copper loss dominates -- use Kg. In DCM, the full current swing is AC, core loss is significant, and Kgfe may be more appropriate.

### 9.5 Design Tables for Common Ferrite Cores (Appendix B, pp.1040-1047)

Erickson tabulates Kg and Kgfe for standard core families. A subset is reproduced below.

#### Pot Cores (Table B.1)

| Core | Kg (cm^5) | Kgfe (cm^x) | Ac (cm^2) | WA (cm^2) | MLT (cm) | lm (cm) | Rth (C/W) |
|---|---|---|---|---|---|---|---|
| 1408 | 2.1e-3 | 1.1e-3 | 0.251 | 0.097 | 2.90 | 2.00 | 100 |
| 1811 | 9.5e-3 | 2.6e-3 | 0.433 | 0.187 | 3.71 | 2.60 | 60 |
| 2213 | 27.1e-3 | 4.9e-3 | 0.635 | 0.297 | 4.42 | 3.15 | 38 |
| 2616 | 69.1e-3 | 8.2e-3 | 0.948 | 0.406 | 5.28 | 3.75 | 30 |
| 3622 | 0.411 | 21.7e-3 | 2.02 | 0.748 | 7.42 | 5.30 | 19 |
| 4229 | 1.15 | 41.1e-3 | 2.66 | 1.40 | 8.60 | 6.81 | 13.5 |

#### EE Cores (Table B.2)

| Core | Kg (cm^5) | Kgfe (cm^x) | Ac (cm^2) | WA (cm^2) | MLT (cm) | lm (cm) |
|---|---|---|---|---|---|---|
| EE16 | 2.0e-3 | 0.84e-3 | 0.19 | 0.190 | 3.40 | 3.45 |
| EE22 | 8.3e-3 | 1.8e-3 | 0.41 | 0.196 | 3.99 | 3.96 |
| EE30 | 85.7e-3 | 6.7e-3 | 1.09 | 0.476 | 6.60 | 5.77 |
| EE40 | 0.209 | 11.8e-3 | 1.27 | 1.10 | 8.50 | 7.70 |
| EE50 | 0.909 | 28.4e-3 | 2.26 | 1.78 | 10.0 | 9.58 |
| EE60 | 1.38 | 36.4e-3 | 2.47 | 2.89 | 12.8 | 11.0 |

#### ETD Cores (Table B.4)

| Core | Kg (cm^5) | Kgfe (cm^x) | Ac (cm^2) | WA (cm^2) | MLT (cm) | lm (cm) | Rth (C/W) |
|---|---|---|---|---|---|---|---|
| ETD29 | 0.098 | 8.5e-3 | 0.76 | 0.903 | 5.33 | 7.20 | -- |
| ETD34 | 0.193 | 13.1e-3 | 0.97 | 1.23 | 6.00 | 7.86 | 19 |
| ETD39 | 0.397 | 19.8e-3 | 1.25 | 1.74 | 6.86 | 9.21 | 15 |
| ETD44 | 0.846 | 30.4e-3 | 1.74 | 2.13 | 7.62 | 10.3 | 12 |
| ETD49 | 1.42 | 41.0e-3 | 2.11 | 2.71 | 8.51 | 11.4 | 11 |

#### PQ Cores (Table B.5)

| Core | Kg (cm^5) | Kgfe (cm^x) | Ac (cm^2) | WA (cm^2) | MLT (cm) | lm (cm) |
|---|---|---|---|---|---|---|
| PQ20/16 | 22.4e-3 | 3.7e-3 | 0.62 | 0.256 | 4.4 | 3.74 |
| PQ26/20 | 83.9e-3 | 7.2e-3 | 1.19 | 0.333 | 5.62 | 4.63 |
| PQ32/20 | 0.203 | 11.7e-3 | 1.70 | 0.471 | 6.71 | 5.55 |
| PQ35/35 | 0.820 | 30.4e-3 | 1.96 | 1.61 | 7.52 | 8.79 |
| PQ40/40 | 1.20 | 39.1e-3 | 2.01 | 2.50 | 8.39 | 10.2 |

> **PyOpenMagnetics:** These tables are useful for quick manual estimates only. For actual
> designs, always use `calculate_advised_magnetics()` which searches the full commercial
> database and optimizes across multiple criteria simultaneously.

### 9.6 Comparison: Erickson vs. McLyman/Hurley Methods

| Aspect | Erickson Kg/Kgfe | McLyman Kg | Hurley Ap |
|---|---|---|---|
| Core selection metric | Kg (cm^5) or Kgfe | Kg (cm^5) | Ap = WA * Ac (cm^4) |
| Primary constraint | Copper loss (Kg) or total loss (Kgfe) | Copper loss + regulation | Energy storage + thermal |
| Flux density | Specified (Kg) or optimized (Kgfe) | Specified | Optimized via Kt thermal constant |
| Core loss | Ignored (Kg) or co-optimized (Kgfe) | Ignored | Via loss ratio g = Pfe/Pcu |
| Optimal Pfe/Pcu ratio | 2/beta (Kgfe, typically ~0.77) | Not addressed | ~1.0 (when not saturation-limited) |
| Best for | Clean first-pass design, textbook clarity | Regulation-constrained designs | Thermally-constrained designs |

All three methods converge to similar core selections for a given application. The Erickson Kgfe method is particularly elegant for transformer design because it simultaneously optimizes flux density and provides explicit formulas for the optimal loss split.

---

## 10. Advanced Core Loss Models (from Muhlethaler ETH 2012)

Source: J. Muhlethaler, "Modeling and Multi-Objective Optimization of Inductive Power Components," PhD thesis, ETH Zurich, 2012.

### 10.1 Limitations of iGSE

The iGSE (Section 5.1 above) assumes that no losses occur when flux is constant (zero voltage across the winding). Measurements by Muhlethaler show this assumption is **wrong**: during phases of constant flux, losses still occur due to magnetic relaxation. The material has not reached thermal equilibrium and the domain structure continues to rearrange, dissipating energy. This effect is significant for waveforms common in power electronics (rectangular voltage with zero-voltage intervals, e.g., in DAB converters, phase-shifted full bridges, or discontinuous conduction mode).

Measured on EPCOS N87 (R42 toroid), varying the zero-voltage duration t1 while keeping dB/dt and DeltaB constant shows that the energy loss per cycle increases with t1, following an exponential approach to a maximum. For DeltaB = 100 mT and t2 = 10 us, the additional energy loss was approximately 30-40% of the baseline loss.

### 10.2 The i2GSE (improved-improved Generalized Steinmetz Equation)

The i2GSE extends the iGSE by adding a relaxation loss term for each transition to zero or reduced dB/dt:

```
Pv = (1/T) * integral_0^T [ ki * |dB/dt|^alpha * (DeltaB)^(beta-alpha) ] dt
     + sum_{l=1}^{n} Qrl * Prl
```

where the first term is the standard iGSE and the sum accounts for n transitions to zero (or reduced) voltage. For each transition l:

```
Prl = (1/T) * kr * |dB(t-)/dt|^alpha_r * (DeltaB)^beta_r * (1 - exp(-t1/tau))
```

```
Qrl = exp( -qr * |dB(t+)/dt| / |dB(t-)/dt| )
```

**Parameters:**
- alpha, beta, ki: standard Steinmetz parameters (same as iGSE)
- kr, alpha_r, beta_r: relaxation loss parameters (empirically determined)
- tau: relaxation time constant (material-dependent, typically microseconds)
- qr: transition sharpness parameter
- dB(t-)/dt: flux slope immediately before the transition
- dB(t+)/dt: flux slope immediately after the transition
- t1: duration of the zero/reduced voltage interval

**Behavior of Qrl:** When voltage switches to zero (dB(t+)/dt = 0), Qrl = 1 and full relaxation loss applies. When the slope changes to a nonzero value (e.g., D = 0.5 triangular flux), Qrl approaches 0 and relaxation loss is negligible. This correctly captures the observation that relaxation matters most at extreme duty cycles.

### 10.3 i2GSE Parameter Extraction

Eight parameters are needed: alpha, beta, ki (from Steinmetz), plus alpha_r, beta_r, kr, tau, qr. The procedure:

1. **alpha, beta, ki**: Excite with symmetric triangular flux (D = 0.5 rectangular voltage). At D = 0.5 the relaxation term vanishes (Qrl ~ 0). Measure at three operating points (varying f and DeltaB), solve for the three parameters. These can also be taken from manufacturer datasheets.

2. **tau**: From measurements of energy loss vs. zero-voltage duration t1. Plot energy vs. t1; the relaxation time constant is tau = DeltaE / (dE/dt)|_{t1=0}. For N87: tau = 6 us. For VITROPERM 500F: tau = 4.5 us.

3. **kr, alpha_r, beta_r**: Measure at three operating points with t1 large enough for equilibrium. Each measurement gives DeltaE; solve the power function DeltaE = kr * |dB(t-)/dt|^alpha_r * (DeltaB)^beta_r.

4. **qr**: Fit to a duty-cycle sweep measurement (loss vs. D at constant f and DeltaB).

**Extracted parameters for EPCOS N87 (ferrite, 25C):**

| Parameter | Value |
|---|---|
| ki | 8.41 |
| alpha | 1.09 |
| beta | 2.16 |
| kr | 0.0574 |
| alpha_r | 0.39 |
| beta_r | 1.31 |
| tau | 6 us |
| qr | 16 |

**Extracted parameters for VITROPERM 500F (nanocrystalline, 25C):**

| Parameter | Value |
|---|---|
| ki | 137e-6 |
| alpha | 1.88 |
| beta | 2.02 |
| kr | 139e-6 |
| alpha_r | 0.76 |
| beta_r | 1.70 |
| tau | 4.5 us |
| qr | 4 |

### 10.4 When i2GSE Matters

The relaxation term is most significant when:
- The waveform includes zero-voltage intervals (trapezoidal flux)
- Duty cycle deviates significantly from 50%
- The zero-voltage duration is on the order of tau (a few microseconds for ferrites)

For symmetric triangular flux (D = 0.5), the i2GSE reduces exactly to the iGSE. The improvement is most noticeable for DAB transformers, phase-shifted converters, and inductors in DCM.

Experimental validation on a DAB transformer (N87, R42, 50 kHz, 42V) showed the i2GSE matched measurements within 5%, while the iGSE underestimated losses by up to 25% at large phase shifts (long zero-voltage intervals).

### 10.5 Steinmetz Premagnetization Graph (SPG) for DC Bias

A major limitation of both iGSE and i2GSE is that they do not account for DC bias. Muhlethaler introduces the **Steinmetz Premagnetization Graph (SPG)**: a graph showing how the Steinmetz parameters (alpha, beta, ki) vary with DC premagnetization HDC.

Key experimental findings (EPCOS N87, 40C):
- **alpha is independent of HDC** (confirmed up to 100 kHz): the frequency exponent does not change with DC bias.
- **beta increases with HDC**: the flux-density exponent grows, meaning the loss increase with flux swing is steeper under bias.
- **ki increases with HDC**: the proportionality constant grows, often by a factor of 2-4x at HDC = 50 A/m compared to zero bias.
- At HDC = 50 A/m and DeltaB = 100 mT, core loss roughly doubles compared to zero bias.
- The DC bias effect is independent of frequency (confirmed up to 100 kHz).

**How to use the SPG:** Replace the Steinmetz parameters in the SE, iGSE, or i2GSE with the bias-dependent values read from the SPG. For example:

```
Pv = ki(HDC) * (2f)^alpha * DeltaB^beta(HDC)
```

The SPG provides a practical, measurement-based approach that requires only a few additional measurements beyond standard Steinmetz characterization. Muhlethaler provides SPGs for N87, N27, 3F3, and VITROPERM 500F.

> **PyOpenMagnetics:** The library's `calculate_core_losses()` supports models including
> `"IGSE"`, `"STEINMETZ"`, and `"ROSHEN"`. For applications where DC bias or relaxation
> effects are significant, compare model outputs and consider measurement-based validation.

---

## 11. Core Loss Measurement and Practical Considerations (from Mu VT 2013)

Source: Mingkai Mu, "High Frequency Magnetic Core Loss Study," PhD thesis, Virginia Tech, 2013.

### 11.1 Why Measurement Is Critical

Mu's thesis demonstrates that core loss depends on so many interacting factors that **measurement under the actual operating conditions is the only reliable way to know the true core loss**. The factors include:
- AC excitation frequency and amplitude (the Steinmetz equation captures this only approximately)
- Waveform shape (rectangular vs. sinusoidal vs. arbitrary)
- Duty cycle (for rectangular excitation)
- DC bias field HDC
- Temperature (ferrites have a U-shaped loss vs. temperature curve)

None of the existing models (MSE, GSE, iGSE) accurately predict losses across all these dimensions simultaneously.

### 11.2 Core Loss Measurement Pitfalls

#### Phase Error Sensitivity

The two-winding method (integrating v2 * iR over one period) is extremely sensitive to phase discrepancy between voltage and current measurements. Since the phase angle between sensing voltage and excitation current is close to 90 degrees (due to the large reactive component), even small phase errors are amplified:

```
Relative error ~ tan(phi_v-i) * Delta_phi
```

At phi_v-i near 90 degrees (typical for high-Q magnetic cores), a 1-degree phase discrepancy produces over 100% error. Sources of phase error include:
- Parasitic inductance of the current sensing resistor
- Probe mismatch and propagation delay differences
- Oscilloscope sampling resolution (200ps at 5GS/s = 0.36 degrees at 5MHz)

#### Capacitive Cancellation Method (Sinusoidal)

Mu proposes adding a resonant capacitor Cr in series with the excitation winding to cancel the reactive voltage component. When Cr resonates with the magnetizing inductance Lm at the test frequency:

```
Cr = N2/N1 / ((2*pi*f)^2 * Lm)
```

The cancelled voltage v3 is approximately in phase with the excitation current, making the measurement insensitive to phase errors. The Cr value does not need to be exact -- keeping the phase angle between v3 and vR below 30 degrees is sufficient (only 1% error for 1 degree discrepancy).

#### Inductive Cancellation Method (Non-sinusoidal)

For rectangular waveforms, the capacitive cancellation does not work. Mu proposes using a reference air-core inductor in series to cancel the reactive voltage for non-sinusoidal excitation. This method enables accurate core loss measurement under rectangular voltage with different duty cycles.

#### Practical Measurement Recommendations

1. **Use matched probes**: Same model for all voltage probes to ensure identical propagation delays. Exchange probes during measurement to verify consistency.
2. **Bifilar winding**: Use bifilar winding on the toroid CUT to guarantee 1:1 turns ratio regardless of leakage inductance.
3. **Current sensing**: Use surface-mount film resistors (low parasitic inductance). Avoid coaxial shunts (different delay from voltage probes). A few resistors in parallel reduce ESL.
4. **Resonant capacitor**: Use high-Q capacitors (silver mica or RF porcelain). Compensate capacitor loss from measured results. A combination of fixed + variable capacitor aids tuning.
5. **Measure simultaneously**: v3 must be measured directly as a single probe measurement, NOT computed from separately measured v2 and vc (the subtraction amplifies errors).
6. **Temperature control**: Perform measurements quickly (automated) to avoid core heating. A single operating point should be measured rapidly.
7. **Averaging**: Use acquisition averaging to reduce noise and stabilize results.

### 11.3 Core Loss Under Non-Sinusoidal (Rectangular) Excitation

Mu measured seven commercial MnZn ferrites (3C90, 3F3, 3F35, 3F5, N49, PC90, DMR50B) at 200 kHz, 500 kHz, and 1 MHz under rectangular voltage with duty cycles from 10% to 90%.

Key findings:
- **At D = 50%**, the core loss for rectangular excitation is close to (or slightly lower than) sinusoidal excitation at the same peak flux density. This is because the fundamental dominates the symmetric triangular flux waveform.
- **At extreme duty cycles** (D < 20% or D > 80%), core loss can increase by 50-100% compared to sinusoidal.
- **None of MSE, GSE, or iGSE accurately predict** the duty-cycle dependence across all materials and frequencies.

#### Equivalent Core Loss Resistor Model

Mu introduces a parallel equivalent core loss resistor model. The key insight: when flux swing is kept constant, the ratio of the equivalent core loss resistor for rectangular vs. sinusoidal excitation is approximately 1 at D = 0.5 for all tested materials. This means at 50% duty cycle, the sinusoidal core loss data can directly predict rectangular wave loss using the equivalent resistance concept.

#### RESE (Rectangular Extension of Steinmetz Equation)

Based on measurements, Mu proposes:

```
Pv_rect = kf^alpha * Bm^beta * 8 / (pi^2 * [4*D*(1-D)]^gamma)
```

where gamma is a material- and frequency-dependent parameter that captures the waveform factor. At D = 0.5, the factor [4*D*(1-D)]^gamma = 1 and the equation reduces to the sinusoidal Steinmetz equation (with the 8/pi^2 factor correcting for the triangular vs. sinusoidal waveform).

Example gamma values for 3F35: gamma = -0.1 at 500 kHz, gamma = 0.14 at 1 MHz, gamma = 0.15 at 1.5 MHz. The sign change indicates the duty-cycle sensitivity reverses between these frequencies.

### 11.4 DC Bias Effect on Core Loss

Mu measured two materials (3F35 and PC90) under combined rectangular AC voltage and DC bias current. The main finding:

**DC bias and waveform affect core loss approximately independently.** The total loss can be factored as:

```
Pv = kf^alpha * Bm^beta * F(Hdc) * [waveform_factor(D)]
```

where F(Hdc) is a DC bias factor. For 3F35 at 500 kHz:

```
F(Hdc) = 2.1875e-4 * Hdc^2 + 1
```

This quadratic bias factor gives less than 10% error across the measured range (Hdc = 0 to 80 A/m).

Key practical points:
- Core loss generally increases with DC bias (the domains are further from equilibrium)
- At Hdc = 50 A/m, loss increase is typically 50-100% depending on material
- The bias factor is approximately independent of frequency and AC flux amplitude for the range tested
- Use Hdc (not Bdc) as the bias variable -- it is directly computable from DC current via Ampere's law without needing to know the B-H curve
- The bias effect is largely independent of the duty-cycle effect, so the two corrections can be applied multiplicatively

### 11.5 Temperature Dependence of Core Loss

Mu confirms the well-known U-shaped temperature dependence for MnZn ferrites:
- Ferrites typically display minimum loss between 60C and 100C
- The minimum-loss temperature shifts with excitation level (flux density and frequency)
- The parabolic temperature model commonly used in literature is only approximate
- **At different AC flux excursions, the minimum loss occurs at different temperatures** (measured on 3C85)
- **The temperature dependence also varies with frequency** (measured on 3F45 -- the parabola shape changes at different frequencies)

**Practical recommendation:** Do not assume a single temperature correction factor. If designing near the thermal limit, measure core loss at the expected operating temperature for the actual excitation conditions.

### 11.6 Practical Core Loss Summary (from Mu)

1. **For quick estimates**: Use iGSE with manufacturer Steinmetz parameters. Accept 10-30% error.
2. **For accurate design**: Measure core loss under actual conditions (frequency, duty cycle, DC bias, temperature). Build a loss map.
3. **Waveform correction**: At D = 50%, sinusoidal loss data is a reasonable proxy for rectangular wave loss. At extreme duty cycles, apply a waveform correction or measure directly.
4. **DC bias**: Always check. Even moderate bias (Hdc = 20-30 A/m) can increase losses by 20-50%. Use a quadratic bias factor as a first approximation.
5. **Temperature**: Design at the expected operating temperature, not room temperature. Ferrite loss at 25C can be 50% higher than at the optimal 80C.

> **PyOpenMagnetics:** Use `calculate_core_losses()` with the appropriate model. For critical
> designs, cross-check with measured data or request loss maps from the core manufacturer.

---

## 12. Multi-Objective Magnetics Optimization (from Muhlethaler)

Source: Muhlethaler Ch.7: optimization of an LCL input filter for a three-phase PFC rectifier.

### 12.1 The Optimization Framework

Muhlethaler demonstrates a complete multi-objective optimization that trades off **volume vs. losses** for magnetic components. The framework consists of:

1. **Design vector X**: All geometric parameters of the magnetic component (core dimensions a, w, h, t; winding parameters N, do, ww, d) are collected into a parameter vector.

2. **Loss models**: Core losses (i2GSE + loss map hybrid), winding losses (skin + proximity with 2D field calculation), and capacitor/damping losses are evaluated for each candidate design.

3. **Thermal constraint**: A thermal model (single-resistance or resistor network) checks that the maximum temperature Tmax is not exceeded. This is the key constraint that prevents making the component arbitrarily small.

4. **Cost function**:
```
F = k_Loss * q_Loss * P + k_Volume * q_Volume * V
```
where k_Loss, k_Volume are designer-chosen weighting factors and q_Loss, q_Volume are proportionality factors to normalize the two objectives to comparable ranges.

5. **Optimizer**: Nelder-Mead simplex (MATLAB fminsearch). The optimizer varies the design parameters and evaluates the cost function, discarding designs that violate constraints.

### 12.2 The P-V Pareto Front

By sweeping the ratio k_Loss/k_Volume, a **Pareto front** of optimal designs is generated in the P-V (loss-volume) plane. Each point on this front represents a design that is optimal for a particular weighting of losses vs. volume -- no design exists that is simultaneously smaller AND lower-loss.

Key observations from Muhlethaler's PFC filter example (8 kHz, 650V DC, 15.4A):
- Filter volume ranges from ~2 dm^3 (low loss) to ~1 dm^3 (minimum volume)
- Filter losses range from ~120 W (low volume) to ~250 W (minimum loss)
- The knee of the Pareto front (best tradeoff) occurs around 3-4 dm^3 / 150 W
- Increasing switching frequency shifts the entire Pareto front to lower volumes and losses for the filter, but increases semiconductor switching losses

### 12.3 System-Level Optimization

Muhlethaler shows that optimizing magnetic components in isolation can be suboptimal. The switching frequency affects both:
- **Filter volume/losses**: Higher fsw means lower L and C values, smaller components, lower losses
- **Semiconductor losses**: Higher fsw means higher switching losses and larger heatsinks

The system-level optimum is found by combining the magnetic P-V Pareto front with the semiconductor P-V Pareto front. The overall optimal switching frequency balances these competing effects. In Muhlethaler's example, this occurs around 8-12 kHz for the three-phase PFC rectifier.

### 12.4 How to Set Up a Magnetics Optimization

Based on Muhlethaler's framework, the general procedure is:

1. **Define the design space**: List all free parameters (core geometry, gap length, turns count, wire diameter, number of layers). Set bounds for each.
2. **Define constraints**: Maximum temperature (typically 100-125C), maximum volume, minimum inductance, saturation limit, THD or ripple limit.
3. **Implement models**: Wire together the loss models (core: i2GSE or iGSE; winding: Dowell or 2D proximity; thermal: resistor network or simplified).
4. **Choose objectives**: Typically total loss P and total volume V. May also include cost, weight, or EMI.
5. **Run the optimizer**: Use Nelder-Mead, genetic algorithm, or exhaustive search over a discrete core database.
6. **Generate the Pareto front**: Sweep the weighting factors to map out the full tradeoff curve.
7. **Select the operating point**: Choose a design from the Pareto front based on application requirements.

**Simplification for rapid estimation**: For the optimization loop, use simplified waveform models (e.g., approximate ripple as constant-amplitude sinusoidal). After selecting a candidate design from the Pareto front, validate with full waveform simulation and detailed loss models.

### 12.5 Experimental Validation

Muhlethaler built the optimized PFC filter and measured losses on the actual hardware. Key results:
- Filter inductor L1: calculated 2.23 W, measured 3.0 W (simplified model underestimated due to neglecting HF ripple in L1)
- Boost inductor L2: calculated 34.7 W (simplified), 27.3 W (detailed), measured 44.9 W at the actual (non-optimal) prototype point
- Current waveforms matched simulations well; THD was 3.86% measured vs. 3.97% simulated
- The simplified models used in optimization overestimated boost inductor losses and underestimated filter inductor losses, but the overall accuracy was acceptable for optimization purposes

> **PyOpenMagnetics:** The library's `calculate_advised_magnetics()` performs a form of
> multi-objective optimization internally, searching the commercial core database and ranking
> designs by weighted criteria (efficiency, cost, dimensions). The Muhlethaler framework
> provides the theoretical foundation for understanding what this optimization is doing and
> how to interpret the results.

---

## 13. Improved Winding Loss Models (from Muhlethaler)

Source: Muhlethaler Ch.4: winding loss modeling.

### 13.1 Beyond Dowell: Exact Bessel Function Solutions for Round Conductors

While Dowell's equation (Section 5.2 above) was derived for foil windings and then adapted to round wire via an equivalent thickness, Muhlethaler uses the **exact Bessel function solutions** for round conductors, following Ferreira's approach.

**Skin-effect factor for round conductors** (exact):

```
FR = (xi / (4*sqrt(2))) * [ ber0(xi)*bei1(xi) - ber0(xi)*ber1(xi)
                             - bei0(xi)*ber1(xi) + bei0(xi)*bei1(xi) ]
                           / [ ber1(xi)^2 + bei1(xi)^2 ]
```

where xi = d / (sqrt(2) * delta), d is the conductor diameter, delta is the skin depth, and ber/bei are Kelvin functions (real and imaginary parts of the Bessel function of the first kind).

**Proximity-effect factor for round conductors** (exact):

```
GR = -(xi * pi^2 * d^2 / (2*sqrt(2))) * [ ber2(xi)*ber1(xi) + ber2(xi)*bei1(xi)
                                            + bei2(xi)*bei1(xi) - bei2(xi)*ber1(xi) ]
                                          / [ ber0(xi)^2 + bei0(xi)^2 ]
```

The total winding loss per unit length is then:

```
P = Rdc * [ FR * I_hat^2 * NL * ML + NL * GR * sum_{m=1}^{ML} H_avg_m^2 ] * lm
```

where NL is conductors per layer, ML is number of layers, and H_avg_m is the average external H-field at layer m.

**Advantage over Dowell for round wire:** The Bessel function approach does not require the porosity factor or equivalent foil thickness approximation that Dowell uses. It is exact for the single-conductor case and more accurate for round wire multilayer windings.

### 13.2 2D Proximity Effect Calculation for Gapped Cores

Dowell's 1D approach assumes the H-field is parallel to the layers and varies only in the direction perpendicular to the layers. This is a poor approximation for gapped cores, where the air gap fringing field creates a complex 2D field pattern.

Muhlethaler uses a **2D approach based on image currents (method of images)**:

1. Each conductor is treated as a current source. The external H-field at conductor (xi, yk) due to current in conductor (xu, yl) is:

```
H = i_{xu,yl} * [(yl - yk) - j*(xu - xi)] / (2*pi * [(xu - xi)^2 + (yl - yk)^2])
```

2. The magnetic core boundaries are modeled by **mirroring** the conductors across the core walls (method of images for mu -> infinity). Multiple mirroring iterations push the boundaries further away, improving accuracy.

3. The **air gap is modeled as a fictitious conductor** carrying a current equal to the magneto-motive force across the gap. This correctly captures the fringing field.

4. For E-cores, the winding is divided into sections (inside the window vs. outside), and the field calculation is done separately for each section with appropriate mirroring.

This 2D approach captures the gap fringing effect, which can increase proximity losses by 50-200% for conductors near the gap compared to the 1D prediction.

### 13.3 Litz Wire Loss Calculation

Muhlethaler separates litz wire losses into strand-level effects (skin and internal proximity between strands within the bundle) and bundle-level effects (external proximity from neighboring bundles and gap fringing).

**Skin-effect losses in litz wire** (n strands, strand diameter di):
```
PS_litz = n * Rdc_strand * FR(f) * (I_hat/n)^2
```

**Proximity-effect losses** = external + internal:
```
PP_litz = n * Rdc_strand * GR(f) * (H_e^2 + H_i^2)
```

where:
- H_e is the external field (from neighboring bundles, gap fringing) -- calculated by the 2D method
- H_i is the internal field (from neighboring strands within the bundle), approximated assuming uniform current distribution over the bundle cross-section:
```
H_i^2 = n * I_hat^2 / (16 * pi * r0^2 * n^2)   (average over all strand positions)
```
with r0 being the bundle outer radius.

**Bundle-level effects** (circulating currents between strands taking different paths through the bundle) are neglected in the model, under the assumption that the litz wire is well-twisted. If the twisting is poor, bundle-level effects can be significant and should be evaluated separately.

### 13.4 Foil Conductor Loss Calculation

For foil conductors, Muhlethaler derives exact analytical solutions for skin and proximity effects. The key result is similar to Dowell but without the approximation of round-to-foil equivalence.

**Foil skin-effect factor:**
```
FF = A * [sinh(2A) + sin(2A)] / [cosh(2A) - cos(2A)]
```

**Foil proximity-effect factor:**
```
GF = -2*A*bF^2 * [sinh(A)*cos(A) + cosh(A)*sin(A)] / [cosh(2A) - cos(2A)]
```

where A = t_foil / delta (foil thickness normalized to skin depth) and bF is the foil width.

For short foil conductors (width not spanning the full core window), a correction factor is needed because the H-field is not uniform across the foil width. Muhlethaler provides a modified proximity calculation that accounts for the field distribution in this case.

### 13.5 Orthogonality of Skin and Proximity Losses

Muhlethaler proves (Appendix A.9 of the thesis) that for non-sinusoidal currents:

1. **Skin and proximity losses can be calculated independently and summed.** There is no cross-coupling term.
2. **Losses at different harmonic frequencies can be calculated independently and summed.** Each Fourier component's losses are evaluated separately using FR and GR at that frequency.

This orthogonality greatly simplifies winding loss calculation for non-sinusoidal waveforms: decompose the current into harmonics, compute skin and proximity losses for each harmonic, and sum all contributions.

### 13.6 Practical Winding Loss Recommendations (from Muhlethaler)

1. **For ungapped transformers**: The 1D approach (Dowell or Bessel functions + Ampere's law field) is adequate. Use the average H-field at each layer.
2. **For gapped inductors**: Use the 2D image method. The gap fringing field dominates proximity losses near the gap. Keep conductors at least one gap length away from the air gap.
3. **Round wire vs. foil**: Use the exact Bessel function formulas for round wire, not the Dowell porosity-factor approximation, especially when the fill factor deviates significantly from unity.
4. **Litz wire**: The internal proximity effect between strands within the bundle can dominate at high frequencies. Check that the strand diameter is well below the skin depth, and that the bundle does not become electrically too large.
5. **Accuracy**: FEM comparisons show the analytical models achieve 5-15% accuracy for typical winding arrangements, which is sufficient for optimization purposes. The main error sources are assumptions about field uniformity and neglect of 3D effects.

> **PyOpenMagnetics:** Use `calculate_winding_losses(magnetic, operating_point, temperature)`.
> The library implements both Dowell and Ferreira (Bessel function) methods. For gapped
> cores, verify that the chosen model properly accounts for fringing field effects.

---

## 14. Original iGSE Paper (Li, Abdallah, Sullivan 2001 -- DOI: 10.1109/ias.2001.955931)

Source: J. Li, T. Abdallah, and C. R. Sullivan, "Improved Calculation of Core Loss with Nonsinusoidal Waveforms," in Conf. Rec. 36th IEEE IAS Annual Meeting, 2001, vol. 4, pp. 2203-2210.

### 14.1 From Steinmetz to GSE to iGSE

The paper traces the evolution of nonsinusoidal core loss models:

**Steinmetz Equation (SE):** Valid only for sinusoidal excitation.
```
Pv = k * f^alpha * B_hat^beta    [W/m^3]
```

**Modified Steinmetz Equation (MSE):** Reinert/Brockmeyer/De Doncker (1999). Introduces an equivalent frequency based on dB/dt:
```
f_eq = (2 / (DeltaB^2 * pi^2)) * integral_0^T (dB/dt)^2 dt
Pv = k * f_eq^(alpha-1) * B_hat^beta * f_r
```
where f_r = 1/T is the repetition frequency. The MSE has a critical anomaly: it implicitly assumes loss proportional to f^2 while simultaneously using f^alpha. For a flux waveform consisting primarily of an mth harmonic with negligible fundamental, the MSE underestimates by a factor of m^(alpha-2). For alpha = 1.5 and m = 3, this is a 42% underestimate.

**Generalized Steinmetz Equation (GSE):** Li and Sullivan's first formulation:
```
Pv(t) = k1 * |dB/dt|^alpha * |B(t)|^(beta-alpha)
```
Averaged over one period:
```
Pv = (1/T) * integral_0^T k1 * |dB/dt|^alpha * |B(t)|^(beta-alpha) dt
```
where:
```
k1 = k / [ (2*pi)^(alpha-1) * integral_0^(2*pi) |cos(theta)|^alpha * |sin(theta)|^(beta-alpha) d(theta) ]
```
The GSE is fully consistent with the SE for sinusoidal waveforms and avoids the MSE anomalies. However, it produces a DC-bias sensitivity that depends on the arbitrary choice of the zero-flux reference, which is unphysical for hysteresis-dominated losses.

**Improved Generalized Steinmetz Equation (iGSE):** Published in the follow-up paper by Venkatachalam, Sullivan, Abdallah, and Tacca (2002, COMPEL Workshop), the iGSE replaces |B(t)| with the peak-to-peak excursion DeltaB:
```
Pv = (1/T) * integral_0^T ki * |dB/dt|^alpha * (DeltaB)^(beta-alpha) dt
```
where:
```
ki = k / [ (2*pi)^(alpha-1) * integral_0^(2*pi) |cos(theta)|^alpha * 2^(beta-alpha) d(theta) ]
```
This removes the DC-bias artifact while retaining the waveform-shape sensitivity. The iGSE uses only the three standard Steinmetz parameters (k, alpha, beta) -- no additional material characterization is required.

### 14.2 Key Experimental Validation

**Triangular waveforms with variable duty cycle** (3C85 MnZn ferrite, 20 kHz, 200 mT peak):
- At D = 50%, GSE and MSE both match measured data within 5%.
- At extreme duty cycles (D = 95%), both underestimate, but using Steinmetz parameters for the 100-200 kHz range (alpha = 1.5 instead of 1.3) improves accuracy because the steep slope at D = 95% has the same dB/dt as D = 50% operation at 200 kHz.

**Two harmonically-related sinusoids** (20 kHz + 60 kHz):
- MSE error reaches 57% when the third harmonic dominates (c = 1).
- GSE error stays within 5% at c = 1, but can reach 40% at intermediate harmonic ratios (c = 0.3-0.5).
- Phase sensitivity: at c = 0.7, measured loss variation with phase is ~8%, GSE predicts ~0%, MSE predicts 30%. GSE is closer.

### 14.3 Practical Guidance from the Paper

1. The iGSE (not the GSE) should be used in practice because it correctly handles DC bias without artifacts.
2. Accuracy is limited by the frequency range over which the Steinmetz parameters are valid. For waveforms with harmonic content spanning a wide frequency range, choose parameters for the dominant loss-producing frequency.
3. For piecewise-linear waveforms (common in SMPS), the iGSE integral can be evaluated analytically segment by segment.
4. The GSE/iGSE framework breaks down for waveforms with minor loops (multiple peaks per half-cycle). In such cases, the waveform should be decomposed into major and minor loops, each evaluated separately.

---

## 15. Original Dowell Analysis (1966 -- DOI: 10.1049/piee.1966.0236)

Source: P. L. Dowell, "Effects of Eddy Currents in Transformer Windings," Proc. IEE, vol. 113, no. 8, pp. 1387-1394, August 1966.

### 15.1 Foundational Concepts

Dowell established the method of dividing transformer windings into **winding portions**, each containing one position of zero MMF. This principle underlies all subsequent 1D winding loss analysis:

**Principle 1:** When considering the leakage impedance due to a particular layer, it is only necessary to consider the other layers insofar as they affect the flux in that layer.

**Principle 2:** The leakage-flux distribution across any layer depends only on the current in that layer and the total current between the layer and an adjacent position of zero MMF.

A **winding portion** is the set of layers between two adjacent positions of zero MMF. The leakage impedances of all portions are summed (referred to the primary) to give the total transformer leakage impedance.

### 15.2 The Exact FR Equation (Dowell's Equation 10)

For a winding portion with m full layers:
```
FR = Rw / Rw0 = M' + ((m^2 - 1) / 3) * D'
```
where:
- M = alpha*h * coth(alpha*h)  -->  M' = Re{M}
- D = 2*alpha*h * tanh(alpha*h / 2)  -->  D' = Re{D}
- alpha = sqrt(j*omega*mu_0 / rho)  =  (1+j) / (delta * sqrt(2))
- h = conductor height (layer thickness)
- delta = skin depth = sqrt(rho / (pi * mu_0 * f))
- m = number of layers in the winding portion

In terms of the normalized variable Q = h/delta (often written as Delta or A in other references):
```
alpha*h = (1+j) * Q / sqrt(2)
```
Leading to the explicit hyperbolic form (as reproduced in Kazimierczuk and the existing Section 5.2):
```
FR = Q * [ sinh(2Q) + sin(2Q) ] / [ cosh(2Q) - cos(2Q) ]
     + (2*(m^2-1)/3) * Q * [ sinh(Q) - sin(Q) ] / [ cosh(Q) + cos(Q) ]
```
The first term is the **skin-effect contribution** (m-independent), and the second term is the **proximity-effect contribution** (scales as m^2).

### 15.3 Leakage Inductance Factor FL (Dowell's Equation 13)

Dowell simultaneously derived the AC leakage inductance ratio:
```
FL = Lw / Lw0 = [ 3*M'' + (m^2 - 1)*D'' ] / [ m^2 * |alpha^2 * h^2| ]
```
where M'' and D'' are the imaginary parts of M and D. For m >= 3, FL approaches the infinite-layer limit:
```
FL_inf = D'' / |alpha^2 * h^2|
```
At high frequency (large Q), FL approaches zero -- the AC leakage inductance due to flux cutting the conductors vanishes because the flux is excluded from the conductors.

### 15.4 Half-Layer Portions and Sectionalised Windings

When a winding is split into portions at positions of zero MMF, some portions may contain a **half layer** (half the current per conductor). Dowell derived a modified FR for (m + 1/2) layers (Equation 17):
```
FR = [ 12*m*M' + 6*M'_{1/2} + m*(4*m^2 + 6*m - 1)*D' ] / [ 12*m + 6 ]
```
where M'_{1/2} is M' evaluated at the half-layer height.

### 15.5 Round-Wire Approximation (Porosity Factor)

Dowell replaced round conductors with square conductors of equal cross-sectional area. This introduces a **porosity factor** eta = d_wire / spacing, which effectively modifies the skin depth:
```
delta_eff = delta / sqrt(eta)
```
and the effective layer height:
```
h_eff = d_wire * sqrt(eta)
```
This approximation is exact at DC but increasingly inaccurate at high frequencies, as Ferreira (1994) later demonstrated.

### 15.6 Key Assumptions and Limitations

1. **1D field assumption:** The H-field is assumed parallel to the layer surfaces and uniform across the winding breadth. This is valid for tightly wound, full-breadth windings but breaks down near gaps, core edges, or partial-breadth windings.
2. **Rectangular-to-round equivalence:** Square conductors replace round ones via equal cross-sectional area. This understates losses for round wire at fnorm > 3 (Ferreira showed 30-47% error at fnorm = 10).
3. **No magnetizing current:** The analysis assumes perfect ampere-turn balance (zero net MMF). Magnetizing current in inductors creates a field component not parallel to the conductor surface, violating the 1D assumption.
4. **Solenoidal geometry:** Curvature effects are neglected (mean turn length used). Valid for typical communication/power transformers.

### 15.7 Practical Skin Depth Formula (Dowell's Equation 15)

For copper at 20C:
```
|alpha^2 * h^2| = 464 * h^2 * f    (h in cm, f in Hz)
```
Temperature correction:
```
coefficient = 464 + 0.72 * (T - 20)    (T in Celsius)
```
At 70C the coefficient becomes 500.

---

## 16. Ferreira Round Wire Model (1994 -- DOI: 10.1109/63.285503)

Source: J. A. Ferreira, "Improved Analytical Modeling of Conductive Losses in Magnetic Components," IEEE Trans. Power Electron., vol. 9, no. 1, pp. 127-131, January 1994.

### 16.1 Key Contribution: Orthogonality of Skin and Proximity Effects

Ferreira proved that for conductors with an axis of symmetry and a uniform applied field parallel to that axis, skin-effect and proximity-effect losses are **orthogonal** -- they can be calculated independently and simply added. The cross-term in the power integral vanishes:
```
P_total = P_skin + P_proximity    (no cross-term)
```
This orthogonality was implicit in 50 years of prior 1D analysis (Bennet 1940, Dowell 1966) but had never been explicitly recognized or proven.

### 16.2 Exact Skin-Effect Factor for Round Wire (Equation 1)

For a single round conductor carrying current I, the exact AC-to-DC resistance ratio due to skin effect is:
```
R_skin / R_dc = (gamma / (4*sqrt(2))) * [ ber_0(gamma)*bei_1(gamma) - bei_0(gamma)*ber_1(gamma)
                                           - ber_0(gamma)*ber_1(gamma) - bei_0(gamma)*bei_1(gamma) ]
                                         / [ ber_1(gamma)^2 + bei_1(gamma)^2 ]
```
where gamma = d / (delta * sqrt(2)), d is the wire diameter, delta is the skin depth, and ber_n / bei_n are Kelvin functions (real/imaginary parts of the Bessel function J_n of complex argument).

The Dowell square-conductor approximation for the same single wire gives:
```
R_skin_square / R_dc = (xi/2) * [ sinh(xi) + sin(xi) ] / [ cosh(xi) - cos(xi) ]
```
where xi = (sqrt(pi)/2) * d / delta.

**Comparison:** At fnorm = 10 (skin depth = d/10), the square approximation underpredicts skin-effect resistance by 16%. For the mth layer of a multilayer winding, the error reaches 30% (m=1) to 47% (m=4) at fnorm = 10.

### 16.3 Exact Proximity-Effect Factor GR for Round Wire (Equation A8)

The proximity-effect power dissipation per unit length in a round conductor subjected to uniform external field H_e is:
```
P_prox = G_R * H_e^2
```
where:
```
G_R = -(2*pi*gamma / sqrt(2)) * [ ber_2(gamma)*ber_1(gamma) + bei_2(gamma)*bei_1(gamma)
                                   + ber_2(gamma)*bei_1(gamma) - bei_2(gamma)*ber_1(gamma) ]
                                 / [ ber_0(gamma)^2 + bei_0(gamma)^2 ]
```

The Dowell equivalent for the rectangular approximation is:
```
G_rect = (sqrt(pi)/2) * d * xi * [ sinh(xi) - sin(xi) ] / [ cosh(xi) + cos(xi) ]
```

### 16.4 Combined AC Resistance for Round Wire Multilayer Winding (Equation 5)

Using orthogonality, the total AC resistance of the mth layer is:
```
R_ac_m / R_dc = FR(gamma) + 2*pi*(2m-1)^2 * GR(gamma)
```
where FR is the skin-effect factor from Section 16.2, GR is from Section 16.3, and the (2m-1)^2 factor accounts for the external H-field at layer m (from Ampere's law with uniform field assumption).

### 16.5 The Porosity Factor Problem

Dowell introduced a "geometrical skin depth" delta_geom = delta / sqrt(eta), where eta is the packing factor. Ferreira argues this is physically incorrect -- skin depth is a material constant and cannot depend on geometry. The eta factor compensates for the gaps between square-conductor representations, but conflates a geometrical correction with a fundamental material property. The Bessel-function approach avoids this issue entirely because it solves the exact cylindrical field problem.

### 16.6 When to Use Ferreira vs. Dowell

| Condition | Recommended Method |
|---|---|
| Foil conductors | Dowell (exact for this geometry) |
| Round wire, fnorm < 3, tight packing (eta > 0.8) | Dowell adequate, Ferreira slightly more accurate |
| Round wire, fnorm > 3 | Ferreira (Dowell error exceeds 15%) |
| Round wire, loose packing (eta < 0.6) | Ferreira strongly preferred |
| Inductors with magnetizing current | Neither (1D assumption violated); use 2D methods |

> **PyOpenMagnetics:** The library supports both Dowell and Ferreira models via
> `calculate_winding_losses()`. For round-wire designs at high frequency (wire diameter
> approaching or exceeding skin depth), select the Ferreira model for higher accuracy.

---

## 17. Sullivan Litz Wire Optimization (1999 -- DOI: 10.1109/63.750181)

Source: C. R. Sullivan, "Optimal Choice for Number of Strands in a Litz-Wire Transformer Winding," IEEE Trans. Power Electron., vol. 14, no. 2, pp. 283-291, March 1999.

### 17.1 Loss Model for Litz Wire

For strands small compared to a skin depth (the regime where litz wire is beneficial), the AC resistance factor is:
```
Fr = 1 + (omega^2 * mu_0^2 * n * N^2 * d_s^6) / (768 * rho^2 * b_w^2) * F_p
```
where:
- n = number of strands
- N = number of turns
- d_s = strand copper diameter
- rho = copper resistivity
- b_w = winding breadth
- F_p = factor for field distribution in multiwinding transformers (= 1 for standard cases)

The key equation for total loss (Equation 2):
```
Fr ~ 1 + (omega^2 * mu_0^2 * n^2 * N^2 * d_s^6) / (768 * rho^2 * b_w^2)
```
Note the n^2 dependence: more strands at fixed strand diameter increases proximity losses because the effective number of layers increases.

### 17.2 DC Resistance Increase from Strand Insulation

The only DC resistance factor that varies with strand count is the strand insulation area. Sullivan models insulation build with a power law (for AWG 30-60 wire):
```
d_o = d_s * (1 + c1 * (d_ref / d_s)^c2)
```
where d_o is the overall strand diameter including insulation, d_ref is a reference diameter (AWG 40 = 0.079 mm), and for single-build insulation: c1 = 0.882, c2 = 0.457.

### 17.3 Optimal Number of Strands (Equation 12)

For a full bobbin (minimum-loss condition), the optimal number of strands is:
```
n_opt = [ (b_w * h_w * k_a) / (pi/4 * d_o^2 * N * k_t) ]^(2/(2+c2))
        * [ (768 * rho^2 * b_w^2) / (omega^2 * mu_0^2 * N^2 * d_ref^(2*c2)) ]^(1/(2+c2))
```
This is derived by minimizing the total resistance factor F'_r = F_dc * F_r, where F_dc accounts for the increased DC resistance due to insulation taking up more of the winding area.

In practice, the optimal design uses many fine strands (strand diameter well below skin depth) such that the AC resistance factor Fr is brought very close to 1.0 while the DC resistance penalty from insulation is small.

### 17.4 Suboptimal Stranding (Cost-Constrained)

When the optimal strand count is too expensive, **two practical alternatives** exist:

**Fixed number of strands** (minimize diameter): Choose the strand diameter that minimizes total loss for a given n. The optimal diameter is (Equation 13):
```
d_s_opt = [ (768 * rho^2 * b_w^2) / (omega^2 * mu_0^2 * n * N^2) ]^(1/6)
```
This yields Fr such that the AC loss component equals twice the DC loss component, or equivalently Fr = 1 + 2/(n_effective).

**Fixed minimum strand diameter** (minimize number): If the finest available strand is specified (e.g., 48 AWG), use (Equation 15) to find the optimal n for that diameter. Increasing n beyond the full-bobbin count provides no benefit.

### 17.5 Design Example Results

For a 14-turn winding on RM5 ferrite at 375 kHz:
- Optimal: 130 strands of AWG 48, total F'_r = 2.3 (Fr = 1.0, Fdc = 2.3)
- 50 strands of AWG 44: F'_r only slightly higher -- a practical compromise

At 1 MHz on the same geometry:
- Optimal: 792 strands of AWG 56 (impractical/expensive)
- 50 strands of AWG 44 gives F'_r ~ 3-4 -- acceptable for many designs

### 17.6 Effective Frequency for Nonsinusoidal Currents (Appendix C)

For Fourier-decomposed current with harmonics I_n at frequencies n*f_1:
```
f_eff = [ sum(n^2 * I_n^2 * (2*pi*n*f_1)^2) / sum(I_n^2) ]^(1/2) / (2*pi)
```
Simplification using the derivative:
```
f_eff = (I_rms(dI/dt)) / (2*pi * I_rms(I))
```
For a triangular current: f_eff = f_1 * sqrt(1 + ...) (approximately 1.15 * f_1 for symmetric triangular).

For a trapezoidal waveform with transition fraction epsilon: f_eff grows as 1/sqrt(epsilon), emphasizing that fast transitions in current waveforms significantly increase effective frequency and litz wire losses.

**Limitation:** The effective frequency approach fails for waveforms with significant harmonics at frequencies where the strand diameter is no longer small compared to a skin depth. In such cases, full Fourier decomposition with Bessel-function loss calculation at each harmonic is required.

> **PyOpenMagnetics:** The library's wire selection and winding loss calculations handle
> litz wire with proper strand-level proximity analysis. Use the Sullivan guidelines above
> for manual strand selection or to interpret PyOpenMagnetics design recommendations.

---

## 18. Dixon Magnetics Design Series (Unitrode/TI SLUP125-129)

Source: Lloyd H. Dixon, Unitrode Power Supply Design Seminar Series (SLUP125 through SLUP129), Texas Instruments.

Dixon's five-part series provides intensely practical design guidance for SMPS magnetics. The content below captures rules and methods not already covered in the McLyman, Hurley, Erickson, or Kazimierczuk sections above.

### 18.1 Energy-Based Current Path Principle (SLUP125)

Dixon's foundational insight for understanding high-frequency effects:

**"Current flows in the path(s) that result in the lowest expenditure of energy."**

- At low frequency, this minimizes I^2*R losses -- current distributes uniformly.
- At high frequency, inductive energy transfer dominates -- current flows to minimize stored magnetic energy (inductance), even if I^2*R increases dramatically.
- This single principle explains skin effect, proximity effect, parallel winding current distribution, and why interleaving works.

### 18.2 Paralleled Windings -- When They Succeed and Fail (SLUP125)

**Paralleling succeeds** when equal current division among parallel paths results in the lowest stored energy. Example: interleaved windings (P-S-P-S) where paralleling the two primary sections balances the field in both winding portions.

**Paralleling fails** when unequal current division results in lower stored energy. Example: two parallel layers on the same side of the secondary -- all HF current flows in the inner layer closest to the secondary, because any current in the outer layer would create additional field (stored energy) between the layers.

**Rule:** A single-layer secondary of copper strap, thicker than several skin depths, does NOT benefit from being paralleled with thinner strips. All HF current flows in the one strip closest to the primary.

### 18.3 Passive Winding Losses (SLUP125)

Windings carrying little or no current can still incur large AC losses if located in the high-field region between primary and secondary. Examples:
- Faraday shields (must be much thinner than skin depth)
- Inactive half of a center-tapped secondary
- Lightly loaded secondaries

**Mitigation:** Relocate passive windings out of the high-field region. In multiple-secondary transformers, place the highest-power secondary closest to the primary. Sequence: Primary - S1(highest power) - S2 - S3.

### 18.4 Practical Thermal Resistance (SLUP126)

Dixon provides empirical thermal resistance formulas for E-core transformers:
```
R_E = 800 / A_S    [C/W]    (A_S = total surface area in cm^2)
```

For ETD/EC series cores, the usable surface area is approximately:
```
A_S ~ 22 * A_W    (A_W = window area in cm^2)
```
Therefore:
```
R_E ~ 36 / A_W    [C/W]    (A_W in cm^2, for ETD/EC cores)
```

For pot cores or PQ cores: R_E ranges from 16/A_W to 32/A_W (proportions vary more).

### 18.5 Quick Core Sizing via Area Product (SLUP126)

Dixon's area product formula for power transformers:
```
AP = A_W * A_E = (P_O / (DeltaB * f_T * K))^(4/3)    [cm^4]
```
where:
- P_O = output power [W]
- DeltaB = peak-to-peak flux density swing [T]
- f_T = transformer operating frequency [Hz]
- K = 0.014 (forward converter, push-pull CT) or 0.017 (bridge, half-bridge)

Based on 420 A/cm^2 current density and 40% copper window utilization. For DeltaB, use the value corresponding to 100 mW/cm^3 core loss (read from manufacturer curves at the operating frequency).

For inductors (saturation-limited):
```
AP = (L * I_SCpk^2 / (B_MAX * K1))^(4/3)    [cm^4]
```
where K1 = 0.03 (single winding), 0.027 (multiple windings), 0.013 (flyback non-isolated), 0.0085 (flyback isolated).

For inductors (core-loss-limited):
```
AP = (L * DeltaI * I_FL / (DeltaB_MAX * K2))^(4/3)    [cm^4]
```
where K2 = 0.707 * K1. The 4/3 exponent accounts for the fact that larger cores must operate at lower power density (volume grows faster than surface area).

### 18.6 Transformer Design Cookbook Summary (SLUP126)

Dixon's step-by-step process (practical sequence, complementing the Kg/Kgfe methods):

1. Define circuit parameters: V_IN range, outputs, topology, f_S, max loss, max temperature rise
2. Establish duty cycle limits (D_lim absolute, D_max normal) and normal V_IN*D
3. Calculate desired turns ratios; note that low-voltage secondaries severely constrain choices (1 vs. 2 turns is a factor of 2 in flux swing)
4. Tentatively select core material, shape, and size (via AP formula or manufacturer guidance)
5. Calculate thermal resistance and loss limit; apportion ~50/50 between core and winding
6. Determine loss-limited DeltaB from core loss curves at 100 mW/cm^3
7. Calculate secondary turns from Faraday's law: N_S = V_O' * T_S / (DeltaB * A_e)
8. Verify flux swing under worst-case V_IN_max * D_lim conditions (must not saturate)
9. Define winding structure (interleaved preferred for low leakage and eddy current loss)
10. Size conductors, calculate DC and AC winding losses using Dowell's curves
11. Verify total loss and temperature rise; iterate core size if needed

### 18.7 Inductor Design -- Optimum Gap (SLUP127)

**The smallest inductor is achieved when the core is simultaneously at maximum flux density AND maximum winding current density.** This defines a unique optimum gap length. Any other gap results in less energy storage capability per unit volume.

Gap area correction for fringing (empirical):
```
A_g = (a + l_g) * (b + l_g)      (rectangular center-pole, dimensions a x b)
A_g = pi/4 * (D_cp + l_g)^2      (round center-pole, diameter D_cp)
```
When l_g = 0.1 * D_cp, the area correction factor is 1.21. The gap must be made proportionally larger to achieve the desired inductance.

**Melted windings near gaps:** In flyback transformers and discontinuous-mode inductors with large flux swings, conductors near the gap can experience enormous eddy current losses from the fringing field. Solutions:
1. Keep winding turns away from the immediate vicinity of the gap (use spacer)
2. Distribute the gap into 2-3 smaller gaps along the center leg
3. Replace the ferrite center leg with a powdered metal rod (distributed gap, no fringing)

### 18.8 Core Eddy Current Loss Model (SLUP128)

Dixon models core eddy current loss as a parallel resistance R_E across the winding:
```
Loss = V_p^2 * t_p / (R_E * T)
```
where V_p is the pulse voltage amplitude and t_p is the pulse width. This is exactly the I^2*R loss formula for a resistor.

**Key insight:** Core eddy current losses depend on dB/dt (i.e., applied volts/turn), NOT on frequency per se. For fixed flux swing and duty cycle, eddy current loss varies with f_S^2. But in a buck-derived converter where V_IN*D is constant, if V_IN doubles and D halves, the flux changes at twice the rate for half the time -- eddy current loss doubles. **Worst case for core eddy current loss is at high V_IN.**

This is the opposite of winding loss (worst at low V_IN due to highest D and rms current), making the loss balancing point input-voltage dependent at frequencies where core eddy current losses are significant (>200-300 kHz for ferrites).

### 18.9 Flux Walking in Push-Pull Topologies (SLUP126)

Small volt-second asymmetries in push-pull drive waveforms cause DC flux to accumulate ("walk") toward saturation. The transformer's very low DC resistance cannot produce enough I*R drop to counteract the asymmetry.

**Solutions:**
- **Current mode control** (peak or average): Automatically corrects volt-second asymmetry by adjusting ON-times to achieve equal peak currents in alternate cycles.
- **Small gap in series:** Increases magnetizing current so that I*R drop can offset asymmetry, but increases snubber losses.
- **Caution with half-bridge:** Current mode control corrects volt-second asymmetry but creates charge asymmetry that causes the capacitor divider voltage to walk to a rail. Requires additional balancing circuitry.

### 18.10 Winding Hierarchy for Multiple Outputs (SLUP125)

For transformers with multiple secondaries:
1. Place highest-power secondary closest to the primary (best coupling, lowest leakage)
2. Lower-power secondaries go outside, away from the high-field region
3. If interleaving, wrap S1 outside the primary, with S2 further out
4. Avoid center-tapped windings where possible (poor utilization, passive losses in inactive half)

**Window area allocation (simplified Dixon rules):**
- Bridge/half-bridge primary + center-tapped secondaries: 40% primary, 60% secondaries
- Forward converter (SE primary/SE secondary): 50%/50%
- CT primary + CT secondary: 50%/50%

> **PyOpenMagnetics:** The library's `design_magnetics_from_converter()` implements automated
> core selection, turns calculation, and loss optimization that follows principles consistent
> with Dixon's cookbook approach. Use the practical rules above for manual verification and
> for understanding design tradeoffs that the optimizer is balancing.

## Medium-Frequency Transformer Design (from Guillod ETH 2018)

Source: T. Guillod, "Modeling and Design of Medium-Frequency Transformers for Future Medium-Voltage Power Electronics Interfaces," PhD thesis, ETH Zurich, 2018.

### 19.1 MF Transformer Scaling Laws

Guillod derives closed-form analytical scaling laws for optimal MF transformers using E-core shell-type geometry with litz wire windings. The model considers core losses (GSE), proximity-effect winding losses, and a convection-based thermal model.

**Optimal design point (global optimum):** When both frequency and turns are free variables, the global optimum satisfies two simultaneous conditions:

```
Core-to-winding loss ratio:    r_cw = P_core / P_winding = 2 / beta_c
AC/DC winding resistance ratio: r_w = beta_c / alpha_c
```

where alpha_c and beta_c are the Steinmetz frequency and flux exponents. For typical ferrites (alpha_c ~ 1.5, beta_c ~ 2.5), this gives r_cw ~ 0.8 and r_w ~ 1.67.

**Optimal frequency:**

```
f_opt = sqrt( (beta_c - alpha_c) / (alpha_c * a_w) )
```

where a_w is the proximity-effect factor: a_w = (1/24) * (pi * mu_0 * sigma * k_w * d_w * d_s)^2, with d_w = winding window width and d_s = strand diameter.

**Frequency diversity (flat optimum):** The loss curve around f_opt is remarkably flat. Operating at half the optimal frequency (f = f_opt/2) increases losses by at most 15% for typical Steinmetz parameters. This means:
- Semiconductor switching losses or EMI concerns can justify operating well below f_opt with minimal penalty on magnetics.
- Parameter uncertainties in core material data can shift f_opt significantly without major efficiency impact.
- If the selected frequency is far below f_opt, a cheaper core material and coarser litz wire can be used with negligible performance loss.

**Power density scaling (P = const, rho variable):** For increasing power density at fixed power:
- Frequency scales as rho^(1/3) -- a 10x density increase requires ~2.15x frequency increase.
- Loss fraction scales as rho^0.2 to rho^0.42 -- losses increase slowly with density.
- Temperature rise scales as rho^0.6 to rho^1.1 -- thermal limit is the binding constraint.

**Power rating scaling (rho = const, P variable):** For increasing power at fixed density:
- Frequency scales as P^(-1/3) -- larger transformers use lower frequency.
- Loss fraction decreases as P^(-0.17 to -0.30) -- larger transformers are more efficient.
- Temperature rise increases as P^(0.03 to 0.30) -- weak dependence on power.

**Key insight:** The area product AcAw ~ S/f is only valid for LF transformers where B_pk = B_sat and J = J_max. For MF transformers, optimal flux density and current density are typically well below saturation, making the area product an upper bound only.

### 19.2 Litz Wire Loss with Twisting Imperfections

Perfect litz wire (idealized hexagonal packing, perfect transposition) has losses given by the orthogonality model (Dowell/Ferreira). In practice, twisting imperfections increase losses significantly:
- **Displacement currents between strands**: Negligible for typical litz wire below 10 MHz.
- **Imperfect transposition**: If strands do not occupy all positions equally, net flux linkage differences drive circulating currents between strands.
- **Number of pitches**: More twist pitches per winding length improve transposition. A minimum of 3-5 pitches inside the winding window is recommended.
- **Measured penalty factors**: Real litz wire losses can be 1.5-3x higher than the theoretical perfect-twisting prediction, depending on construction quality.

### 19.3 MV Insulation Design for MF Transformers

For medium-voltage (>1 kV) MF transformers, insulation design is a critical challenge:

**Construction approach (demonstrated for 25 kW, 7 kV, 50 kHz):**
- LV winding on inner coil former (polycarbonate), MV winding on outer coil former.
- MV winding divided into 2 chambers and 3 layers to reduce inter-layer voltage stress (max 1.2 kV per layer for a 3.5 kV total).
- Polypropylene spacers between MV winding layers (chosen for low dielectric loss and high breakdown strength).
- Entire winding package vacuum-potted with silicone elastomer (Dow Corning TC4605 HLV) for void-free insulation.
- Creepage/clearance distances sized per IEC 60950-1 with margin (prototype: 40 mm clearance for 7 kV peak).

**Vacuum potting process (critical for reliability):**
1. Mix two-component silicone with electric mixer for homogeneity.
2. Place silicone and winding package in separate vacuum vessels, evacuate to ~30 mbar.
3. Pressurize silicone vessel slightly (~300 mbar above winding vessel) to force silicone through winding package.
4. Fill from bottom upward (against gravity) to ensure complete filling.
5. After filling, increase to atmospheric pressure to compress any residual microvoids.
6. Cure in oven per silicone manufacturer's schedule.

**Material compatibility warning:** Many cyanoacrylate and UV-curing adhesives inhibit silicone curing. Only validated adhesives (e.g., Loctite 3090 two-component) should be used during assembly.

### 19.4 Electrical Shielding of MV/MF Transformers

MV/MF transformers operated with PWM voltages create significant electric field stress in the insulation. Guillod examines four shielding methods:

- **Geometric shielding**: Conductive electrodes shape the field. Simple but metallic electrodes create eddy current losses at MF.
- **Capacitive shielding**: Multiple concentric electrodes control potential distribution. Difficult in compact MF transformers.
- **Resistive shielding**: Semi-conductive coatings on insulation surfaces reshape the potential distribution. Works at DC, MF, and HF. Recommended approach.
- **Refractive shielding**: High-permittivity materials shape the field. Frequency-dependent, not suitable for wide-band PWM excitation.

**Resistive shield design:** A semi-resistive coating connected to earth (core) is applied to the winding insulation surface. The optimal conductivity balances low-frequency shielding effectiveness against high-frequency displacement current losses. Gaps in the coating prevent formation of a short-circuit winding (which would create eddy current losses from the magnetic field).

### 19.5 Dielectric Losses with PWM Voltages

PWM voltages contain harmonics up to the corner frequency defined by the switching transition speed. For MV/MF insulation, these harmonics cause dielectric polarization losses that can be significant.

**Key results:**
- For frequency-independent materials (constant tan_delta), dielectric loss with PWM is proportional to the RMS voltage squared, regardless of harmonic content. The effective frequency for loss calculation is the switching frequency.
- For frequency-dependent materials, each harmonic's contribution must be summed using the material's complex permittivity spectrum.
- Material selection must avoid dielectric relaxation peaks (alpha and beta relaxations) in the operating frequency-temperature range.
- Silicone elastomers generally have low and flat dielectric loss up to several MHz, making them suitable for MF insulation.
- Epoxy resins can have beta relaxation peaks in the 100 kHz-10 MHz range that cause high dielectric losses at MF.

**Design guidelines for MV/MF insulation material selection:**
1. Identify the relevant frequency range: switching frequency to corner frequency (= 1 / (pi * t_rise)).
2. Check that no dielectric loss peak (alpha or beta relaxation) falls in the operating frequency-temperature range.
3. If permittivity is flat across the spectrum, simplified loss calculation applies. Otherwise, use full harmonic summation.
4. Dielectric losses are usually secondary to core/winding/semiconductor losses. Include them in thermal modeling only if they are within an order of magnitude of other losses.
5. Use small-signal dielectric spectroscopy for early material screening (linearity of dry-type polymers allows extrapolation to MV).

### 19.6 MV/MF Transformer Equivalent Circuit Extraction

Guillod shows that for MF transformers with high coupling factors, extracting T-model or Pi-model equivalent circuit parameters from impedance measurements requires care:
- Open-circuit and short-circuit measurements at the operating frequency are the primary method.
- For transformers with high coupling (k > 0.99), small measurement errors in open-circuit impedance cause large errors in extracted leakage inductance (leakage is obtained as the difference of two nearly equal numbers).
- 3D FEM simulations agree with measurements within 5-10% for a well-characterized geometry, but 2D models can have 20-30% error due to winding-head effects.
- Statistical analysis of manufacturing tolerances (winding placement, core gap, material properties) shows that the leakage inductance can vary by +/-20-50% between nominally identical units.

### 19.7 Prototype Performance (25 kW, 50 kHz, 7 kV)

Key measured results from the ETH demonstrator:
- Efficiency: 99.4% at 25 kW (total loss ~150 W).
- Core loss: measured via calorimetric method (subtracting winding loss from total). Core loss was ~60% of total loss.
- Winding loss: measured via impedance analyzer at operating frequency. Proximity effect factor was 1.3-1.8x at 50 kHz.
- Power density: 7.4 kW/l (including terminations and fan) or 9.6 kW/l (core + winding package only).
- Insulation: passed 15 kV CM insulation test.
- Thermal: forced air cooling with small fan. Max winding temperature ~80C at full load and 25C ambient.

---

## 20. Ferrite Material Properties (from Snelling 1969)

Source: E. C. Snelling, "Soft Ferrites: Properties and Applications," Iliffe Books, 1969.

Snelling's monograph is the foundational reference on soft ferrite physics. While many of its applications are superseded, the material science treatment remains authoritative. The content below captures ferrite property fundamentals not covered elsewhere in this guide.

### 20.1 Loss Mechanisms in Ferrites

Snelling decomposes the total magnetic loss tangent into three additive components (Eqn 2.56-2.65):

```
tan(delta_m) = tan(delta_h) + tan(delta_e) + tan(delta_r)
```

Where:
- **tan(delta_h)** = hysteresis loss component (proportional to flux density)
- **tan(delta_e)** = eddy current loss component (proportional to frequency)
- **tan(delta_r)** = residual loss component (approximately independent of both B and f)

#### Hysteresis Loss

In the Rayleigh region (low-amplitude excitation), the permeability depends on flux density as:

```
mu_a = mu_i * (1 + v*B)
```

where v is the Rayleigh hysteresis coefficient (units: m/A). The hysteresis energy loss per cycle is proportional to B^3 in this region. The hysteresis loss tangent is:

```
tan(delta_h) = (v * B * mu_i) / (3 * mu_a)
```

At higher amplitudes, the hysteresis loss departs from the B^3 law and follows an empirical power law closer to B^(1.6-2.0).

#### Eddy Current Loss

Despite the high bulk resistivity of ferrites (0.01 to 10 ohm-m for MnZn, >10^3 ohm-m for NiZn), eddy currents can be significant at high frequencies. The eddy current loss tangent is:

```
tan(delta_e) = (mu_i * omega * C_e) / rho_eff
```

where C_e is a geometrical constant depending on core cross-sectional shape and dimensions, and rho_eff is the effective resistivity. Snelling notes that the effective resistivity at high frequency may differ from the DC value due to the polycrystalline dielectric structure of ferrites: MnZn ferrite behaves as semiconducting crystallites surrounded by high-resistivity grain boundaries. At low frequency, the grain boundary resistance dominates (high effective resistivity); at high frequency, the intragranular conductivity dominates (lower effective resistivity), causing eddy current losses to increase faster than f^2.

#### Residual Loss

The residual loss encompasses domain wall resonance, spin rotation damping, and other frequency-dependent mechanisms not captured by hysteresis or eddy current terms. It is the dominant loss mechanism at very low flux densities and moderate frequencies (100 kHz-1 MHz for MnZn ferrites). The residual loss tangent divided by initial permeability, (tan delta_r)/mu_i, is a material constant at a given frequency.

### 20.2 Permeability vs. Temperature

Ferrite permeability rises monotonically with temperature up to the Curie temperature, where it drops sharply to unity. Key properties:

- **Curie temperature (Tc):** The temperature at which ferrimagnetic ordering is destroyed. For MnZn ferrites: Tc = 100-300C depending on composition. For NiZn ferrites: Tc = 100-600C.
- **Temperature factor of permeability:** Defined as (delta_mu)/(mu^2 * delta_T), this normalized coefficient allows prediction of effective permeability change in gapped circuits. Snelling emphasizes dividing by mu^2 (not mu) because when mu is reduced to an effective value mu_e by a gap, the temperature effect is reduced by the ratio mu_e/mu_i.
- **Practical implication:** A core with a large gap (low mu_e) has inherently better temperature stability of inductance than an ungapped core. This is a primary reason for gapping cores used in LC filters and timing circuits where inductance stability matters.

### 20.3 Permeability vs. Frequency

Ferrite permeability remains approximately constant from DC up to a critical frequency, then undergoes a dispersion:

- **MnZn ferrites:** The onset of permeability dispersion occurs at frequencies inversely proportional to the initial permeability. High-mu materials (mu_i > 5000) show dispersion onset as low as 100 kHz. Low-mu materials (mu_i ~ 100-500) remain useful to 10 MHz or beyond.
- **NiZn ferrites:** Due to their much higher resistivity, NiZn ferrites maintain stable permeability to higher frequencies (up to 100 MHz for low-mu grades). They are preferred above ~2 MHz.
- **Snoek's limit:** The product mu_i * f_cutoff is approximately constant for a given class of ferrite (Snoek's law). For MnZn ferrites: mu_i * f_r ~ 4-8 GHz; for NiZn: mu_i * f_r ~ 1-5 GHz. This fundamental limit means high permeability and high frequency cannot be achieved simultaneously.

### 20.4 Permeability vs. Flux Density

Above the Rayleigh region, the amplitude permeability mu_a increases with B, reaches a maximum, then decreases as the material approaches saturation. The B-H loop shape determines:

- **Saturation flux density (Bsat):** 0.3-0.5 T for MnZn at 25C, decreasing with temperature (approximately linearly, reaching zero at Tc). NiZn ferrites have lower Bsat (0.2-0.4 T).
- **Bsat temperature derating:** Snelling's data shows approximately 0.15-0.2 %/C decrease from room temperature. At 100C, Bsat is typically 70-80% of its 25C value.
- **Remanence ratio (Br/Bsat):** Typically 0.5-0.8 for MnZn ferrites. A high remanence ratio can cause transformer saturation after a transient; gapping reduces the effective remanence ratio.

### 20.5 Gapping Effects on Permeability and Stability

When an air gap of length lg is introduced into a core of magnetic path length le and material permeability mu_i, the effective permeability becomes (Snelling Ch4):

```
mu_e = mu_i / (1 + (lg/le) * mu_i)
```

For mu_i >> le/lg (typical case): mu_e ~ le/lg (independent of material permeability).

**Consequences of gapping:**

1. **Inductance stability:** Since mu_e becomes independent of mu_i, variations in mu_i due to temperature, time (disaccommodation), or DC bias have negligible effect on inductance. This is the primary purpose of gapping in filter inductors and precision LC circuits.

2. **Reduced temperature sensitivity:** The temperature coefficient of mu_e is reduced by the factor (mu_e/mu_i)^2 compared to the ungapped case. Example: if mu_i = 2000 and mu_e = 100 (gap ratio lg/le = 0.0005), the temperature sensitivity is reduced by a factor of (100/2000)^2 = 0.0025 -- a 400x improvement.

3. **Increased saturation current:** Gapping reduces the effective permeability, so a higher current is needed to reach Bsat. The energy storage capacity is proportional to lg (the gap stores energy in the field).

4. **Increased hysteresis loss:** Counterintuitively, gapping can increase the hysteresis loss component because for a given inductance, the core must operate at higher flux density (since mu_e is lower and fewer turns may be used). The hysteresis loss per cycle is proportional to the area of the B-H loop, which increases with peak B.

5. **Fringing flux:** The gap introduces fringing flux that extends beyond the gap region. Snelling provides demagnetization factor charts for cylindrical and rectangular cores. The fringing flux causes localized eddy current heating in nearby conductors and increases the effective gap area (reducing the effective reluctance).

### 20.6 Disaccommodation

Freshly cooled ferrites undergo a logarithmic decrease in permeability over time (disaccommodation). This is caused by slow migration of lattice vacancies created during sintering:

```
delta_mu / mu^2 = DF * log10(t2/t1)
```

where DF is the disaccommodation factor (material constant, typically 2-15 x 10^-6 for MnZn ferrites). The effect is most pronounced in high-permeability grades and can cause 1-3% inductance drift over months. **Gapping largely eliminates the practical impact** because mu_e becomes independent of mu_i.

### 20.7 Dimensional Resonance

At frequencies where the core cross-sectional dimension approaches half a wavelength in the ferrite medium, dimensional resonance occurs. The resonant dimension is:

```
d_res = c_0 / (2 * f * sqrt(|mu| * |epsilon|))
```

For a typical MnZn ferrite with mu = 2000, epsilon = 10^5, the wavelength at 1 MHz is approximately 50 mm. This means cores with cross-sections above ~25 mm may exhibit anomalous permeability peaks and dips. **Practical rule:** At frequencies above 1 MHz, keep core cross-sectional dimensions below 10 mm for MnZn ferrites.

---

## 21. Valchev & Van den Bossche Contributions

Source: V. Valchev and A. Van den Bossche, "Inductors and Transformers for Power Electronics," CRC Press, 2005.

This book provides a practical engineering-focused treatment with original contributions in fast design nomograms, improved eddy current loss models, and thermal design. The content below covers methods not already present in this guide.

### 21.1 Fast Design Approach (Nomogram Method)

Valchev introduces a rapid core sizing method using a single scaling parameter a_ch (the largest physical dimension of the core) related to the VA rating:

```
S = A * a_ch^gamma    [VA]
```

Where A is a design coefficient (typically 5-25 x 10^6 when a_ch is in meters) and gamma ~ 3.5. The coefficient A depends on material, cooling, and winding technology.

**Practical rule of thumb:** For A = 10 x 10^6, 1 cm of a_ch corresponds to approximately 10 W of power handling. This allows immediate core sizing from a power spec without any detailed calculation.

**Design categories** (determined before detailed design begins):
- **(A) Saturated thermally limited:** Core losses are low relative to copper losses; the design is limited by saturation and temperature rise. Typical of low-frequency or high-DC-bias applications.
- **(B) Non-saturated thermally limited:** Both core and copper losses are significant; the design is limited by temperature rise before saturation is reached. Typical of high-frequency AC applications (most SMPS transformers).
- **(C) Signal quality limited:** Neither saturation nor thermal limits dominate; design is constrained by accuracy (current transformers), linearity, or bandwidth.

### 21.2 Heat Dissipation Capability

Valchev derives a practical dissipation capability formula:

```
P_h = k_A * a_ch^2    [W]
```

Where k_A ~ 2500 W/m^2 for natural convection with 50C temperature rise. This gives the maximum total loss the component can dissipate. For a 42 mm core: P_h = 2500 * 0.042^2 = 4.4 W.

### 21.3 Improved Eddy Current Loss Model (2D Field Factor)

The existing guide covers Dowell (1D) and Ferreira (Bessel function) approaches. Valchev introduces a **2D field factor k_F** that corrects for the actual field distribution in the winding window:

```
P_eddy = k_c * k_F * P_dc
```

Where k_c is the eddy current loss factor (from Dowell or Bessel functions) and k_F accounts for the 2D field geometry. Key features:

- **k_F depends on kappa** = ratio of distance from winding axis to core leg, normalized to winding width. For windings between core legs (EE cores), k_F ranges from 2-15 depending on geometry. For coil ends (no core nearby), k_F ~ 0.2-0.5.
- **Practical impact:** The 2D correction can increase or decrease predicted eddy current losses by a factor of 2-10x compared to 1D methods, especially for inductors with large gaps where fringing flux is significant.
- The method is calibrated against >100 finite element simulations, matching within 3% typically and 10% worst case.

### 21.4 Thermal Resistance Network (Level 2)

While the existing guide covers simple surface-area and volume-based thermal models (Section 7), Valchev provides a detailed resistance network with five thermal paths:

1. **R_hs (hot-spot to coil surface):** Conduction through the winding bulk. Depends on copper fill factor and insulation thermal conductivity.
2. **R_cf (coil to ferrite):** Combined conduction + radiation across the air gap between coil and core inside the winding window. The air gap l_cf is typically 0.1-0.5 mm.
3. **R_ca (coil to ambient):** Parallel combination of convection and radiation from exposed coil surfaces.
4. **R_fa (ferrite to ambient):** Parallel combination of convection and radiation from exposed core surfaces.
5. **R_fc (ferrite to coil):** Reverse path from core to winding (used when core losses dominate).

**Convection heat transfer coefficient** (improved formula from experimental validation):

```
h_c = C * (Delta_T)^a_T * L_char^a_L * p_atm^a_p * T_amb^a_Ta
```

Where C depends on orientation (horizontal vs. vertical surface), and the exponents are empirically fitted. Key finding: the standard value h_c = 10 W/m^2-C is reasonable for moderate temperature rises (30-50C) but overestimates cooling at small Delta_T and underestimates it at large Delta_T.

**Thermal capacitance** for transient analysis:

```
C_q = sum(c_i * m_i)    [J/C]
```

The thermal time constant tau = R_q * C_q determines how quickly the component reaches steady state. For small EE cores: tau ~ 5-15 minutes. For large cores: tau ~ 30-60 minutes. This is relevant for short-term overload capability.

### 21.5 Optimal Copper-to-Core Loss Ratio (Generalized)

Section 9.2 (Erickson) derived Pfe/Pcu = 2/beta at the optimum. Valchev generalizes this for different constraint scenarios:

**General result:** With core losses proportional to B^b (Steinmetz exponent) and copper losses proportional to e^g (where e is the relative turns factor and g is the copper loss exponent), the optimal ratio is:

```
P_fe / P_cu = g / b
```

The copper loss exponent g depends on the design constraint:
- **Constant copper volume, variable wire cross-section:** g = 2 (ohmic losses dominate). Optimal: P_fe = P_cu when b = 2.
- **Constant wire cross-section, variable turns:** g = 1 (wire length increases linearly with turns). Optimal: P_fe = P_cu * (1/b).
- **Including eddy current losses (low-frequency approx):** g rises toward 3 because eddy current losses scale as e^3. Optimal copper-to-core ratio shifts: more core loss is acceptable (30-50% of total) with the remainder in copper.
- **With thermal resistance asymmetry:** If the core has better thermal paths than the winding (common in shell-type EE/EI cores where core surface area is ~2x coil surface area), more loss can be tolerated in the core. The optimal ratio becomes P_fe/P_cu = (g/b) * (R_cu_amb / R_fe_amb).

**Practical summary table (Valchev Table 10.1):**

| Constraint | g | Optimal P_cu/P_fe (for b=2.5) |
|---|---|---|
| Constant Cu volume, ohmic only | 2 | 0.8 |
| Constant wire section, ohmic only | 1 | 0.4 |
| Constant Cu volume + LF eddy currents | ~2.5 | 1.0 |
| Constant wire section + LF eddy currents | ~1.5 | 0.6 |

---

## 22. High Reliability Magnetics (from McLyman)

Source: Colonel Wm. T. McLyman, "High Reliability Magnetic Devices: Design & Fabrication," Marcel Dekker, 2002.

This book addresses the manufacturing and quality aspects of magnetics for aerospace, military, and medical applications. The content below covers fabrication, failure modes, and reliability guidelines not present in the design-focused sections above.

### 22.1 Fabrication Process Control

High-reliability magnetics require documented, step-by-step fabrication procedures with in-process inspection at every stage. Key requirements:

**Work environment:**
- Clean, well-ventilated workbenches; cleaned with alcohol-dampened wipes daily
- No smoking, eating, or drinking within 3 meters of work area
- Operators must wash hands thoroughly before handling parts; wear clean cotton gloves for sensitive operations
- ESD protection required for all workstations

**Winding tension control (McLyman Table 5-4):**

| AWG | Nominal Tension | Maximum Tension |
|---|---|---|
| 20 | 9.7 lb | 16.1 lb |
| 24 | 3.8 lb | 6.3 lb |
| 28 | 1.5 lb | 2.5 lb |
| 32 | 270 gm | 1.0 lb |
| 36 | 110 gm | 180 gm |
| 40 | 68 gm | 110 gm |
| 44 | 25 gm | 45 gm |

Exceeding maximum tension causes wire stretching (diameter reduction, resistance increase) or breakage. Insufficient tension causes loose windings with poor thermal coupling and potential vibration-induced failures.

**Crossed wires:** Strictly prohibited in layer-wound coils. Crossed wires create localized pressure points that can damage insulation under thermal cycling, leading to turn-to-turn shorts. Each layer must be inspected before applying interlayer insulation.

### 22.2 Winding Techniques for Reliability

**Layer winding (preferred for HR):**
- Wind each layer from margin to margin with consistent tension
- Apply interlayer insulation (polyester or polyimide tape, 50% overlap minimum)
- Anchor start and tap leads with support tape before continuing winding
- For toroidal cores: progressive winding with defined angular coverage per winding

**Tap leads:**
- Must be insulated from adjacent winding with tape on both sides
- Must exit through the margin area, never over the winding surface
- Use woven glass sleeving for lead protection in high-temperature applications

**Foil windings:**
- Foil edges must be deburred to prevent puncture of interlayer insulation
- Use edge tape or fold-over to protect insulation from sharp foil edges
- Anchor foil start with adhesive tape; solder connections must be smooth and free of sharp points

### 22.3 Soldering and Termination Standards

**Solder joints must meet these criteria:**
- Continuous, smooth, shiny appearance (no cold joints, no porosity)
- Wire outline visible beneath solder coating (not buried in excess solder)
- No solder wicking under wire insulation (creates a rigid, stress-concentrating point)
- For stranded wire: individual strands must be visible beneath solder

**Magnet wire stripping:**
- Chemical stripping preferred for fine wire (< AWG 30) to avoid mechanical damage
- Thermal stripping acceptable for larger wire with proper temperature control
- Abrasive stripping (wheel) acceptable when other methods are not practical
- Never use blade stripping on wire < AWG 26 (risk of nicking conductors)

**Strain relief:** All lead connections require minimum 3 turns of magnet wire wrapped tightly before soldering to prevent mechanical stress from reaching the winding.

### 22.4 Impregnation and Encapsulation

Two-step process for maximum reliability:

**Step 1: Vacuum impregnation** (using epoxy resin, e.g., Scotchcast 280):
- Removes trapped air from winding interstices
- Provides complete penetration of resin into all voids
- Vacuum level: 29 inches Hg minimum for 15 minutes
- Breaks vacuum to force resin into voids by atmospheric pressure

**Step 2: Embedment** (potting compound, e.g., Scotchcast 281):
- Encapsulates the component in a protective shell
- Provides mechanical protection, moisture barrier, and thermal mass
- Must be free of voids, cracks, and surface defects after cure

**Cure verification:** Every batch of mixed resin requires a proof-of-cure sample. The sample must meet hardness and adhesion specifications before the batch is accepted.

**RTV sealant:** Applied around all exiting leads before embedment to prevent resin leakage along lead wires.

### 22.5 High Voltage Design Guidelines

For transformers operating above 250V peak:

**Corona onset:** Corona discharge begins when the electric field in air exceeds the breakdown strength (~3 kV/mm at sea level, decreasing with altitude). Corona causes progressive degradation of organic insulation and is the primary failure mechanism in high-voltage magnetics.

**Voltage breakdown vs. frequency:** The breakdown threshold decreases with increasing frequency. At 100 kHz, breakdown occurs at approximately half the DC breakdown voltage.

**Design rules to avoid corona:**
- Eliminate sharp edges on conductors (round all solder joints, deburr foil edges)
- Use spherical solder joints on high-voltage terminals (Teflon sleeving forms the mold)
- Maintain creepage distances: minimum 1.5x conductor spacing from board edges
- Separate high-voltage and low-voltage circuits with grounded guard conductors (double-sided ground bus on PCB)
- Fill all voids in the winding with impregnation resin (voids are sites for partial discharge)

**Insulation materials for high voltage:**
- Kapton (polyimide): excellent dielectric strength, withstands 300C
- Teflon: excellent dielectric, low loss tangent, withstands 260C
- Nomex (aramid paper): good mechanical strength, withstands 220C
- Glass tape: highest temperature capability but lower dielectric strength per unit thickness

### 22.6 Testing and Quality Assurance

**Electrical tests:**
- Turns ratio (voltage method at low frequency)
- Primary inductance and leakage inductance (bridge or current method)
- DC resistance of each winding
- Hipot (dielectric withstand): 2x rated voltage + 1000V for 60 seconds
- Insulation resistance: minimum 100 Mohm at 500V DC
- Self-resonant frequency (identifies parasitic capacitance issues)

**Fabrication tests (visual/mechanical):**
- Inspection of every winding layer for crossed wires, spacing, and tension
- Verification of insulation tape overlap and coverage
- Solder joint inspection under magnification
- Dimensional check against drawing tolerances

**Environmental tests (qualification):**
- Thermal shock: -55C to +125C, 100 cycles minimum
- Humidity: 240 hours at 95% RH, 65C
- Vibration: random vibration per MIL-STD-810
- Altitude: operation at reduced pressure (corona test for HV units)

### 22.7 Common Failure Modes in Magnetics

Based on McLyman's experience with aerospace magnetic components:

1. **Turn-to-turn shorts:** Caused by crossed wires, insufficient interlayer insulation, thermal cycling fatigue, or corona erosion. Prevention: strict winding process control, adequate insulation margins.

2. **Winding-to-core shorts:** Caused by inadequate clearance between winding and core edges, especially at corners of rectangular bobbins. Prevention: margin tape, adequate creepage distance, rounded core edges.

3. **Lead breakage:** Caused by insufficient strain relief, thermal cycling, or vibration. Prevention: minimum 3-turn strain relief at every lead exit, flexible lead wire for external connections.

4. **Saturation drift:** In push-pull topologies, small volt-second imbalances cause DC flux walking toward saturation. Prevention: current-mode control, or add a small gap.

5. **Thermal degradation:** Insulation breakdown from sustained operation above rated temperature. Prevention: design for worst-case ambient + worst-case internal temperature rise. Use insulation materials rated for the actual operating temperature class.

6. **Corona failure (HV only):** Progressive insulation erosion from partial discharge in air voids. Prevention: complete vacuum impregnation, spherical electrode shapes, adequate creepage distances.

---

## 23. Parasitic Properties of Magnetics (from Albach 2017)

Source: Manfred Albach, "Induktivitaeten in der Leistungselektronik: Spulen, Trafos und ihre parasitaeren Eigenschaften," Springer Vieweg, 2017.

Albach provides the most detailed analytical treatment of parasitic properties (winding capacitance, leakage inductance, and the interaction of core geometry with winding losses) available in any single reference. The content below covers methods not already present in this guide.

### 23.1 Winding Capacitance of Wire-Wound Coils

Albach derives the interwinding capacitance by computing the electric field energy stored between conductor layers. Key simplifications:

- Capacitance between adjacent turns within a single layer is negligible (small voltage difference).
- Capacitance between non-adjacent layers is negligible (field falls off rapidly with distance).
- The dominant contribution is the **layer-to-layer capacitance** between adjacent layers.

For a two-layer coil with N turns total (N/2 per layer), the equivalent lumped capacitance referred to the coil terminals is:

```
C_eq = (epsilon_0 * epsilon_r * l_w * b_w) / (3 * d_layer)
```

where l_w is the winding length (along the core), b_w is the winding breadth, and d_layer is the interlayer distance (including insulation and air gap between round wires). The factor of 1/3 arises from the quadratic voltage distribution across each layer.

**For N_L layers, the general formula is:**

```
C_eq = (4 * (N_L - 1) * epsilon_0 * epsilon_r * l_w * b_w) / (3 * N_L^2 * d_layer)
```

**Practical implications for capacitance reduction:**
- Increasing the number of layers at constant total turns increases C_eq (because d_layer decreases or stays constant while the voltage across each pair increases).
- **Sectionalizing the winding** (dividing into multiple bobbin sections wound in the same direction) is the most effective way to reduce capacitance: sectionalizing into k sections reduces capacitance by approximately 1/k^2.
- **Bank winding** (zigzag pattern within each section) further reduces capacitance by reducing the voltage between adjacent conductors.
- Increasing the interlayer insulation thickness d_layer reduces capacitance but wastes window area.

### 23.2 Winding Capacitance of Foil Coils

For foil windings, the capacitance calculation simplifies because the conductors fill the winding breadth uniformly:

```
C_eq = (epsilon_0 * epsilon_r * A_foil) / (3 * d_insulation)
```

where A_foil is the foil area per layer and d_insulation is the insulation thickness between foil layers. Foil windings inherently have high parasitic capacitance due to the large plate area and thin insulation.

### 23.3 Core Influence on Winding Capacitance

When a winding is placed on a magnetic core, the core (which has a high permittivity, epsilon_r = 10-100k for ferrites) creates an additional capacitance between the winding and core:

```
C_core = epsilon_0 * epsilon_r_core * A_contact / d_gap
```

where A_contact is the area where winding faces the core and d_gap is the bobbin wall thickness or air gap between winding and core. This additional capacitance can be comparable to or exceed the inter-layer capacitance, especially for high-permittivity MnZn ferrites.

**Albach's method:** Calculate an equivalent surface (Ersatzoberflaecke) for the core-to-winding interface, then compute the electric energy stored in this gap. The resulting additional capacitance is added in parallel with the inter-layer capacitance.

### 23.4 Leakage Inductance Calculation

Albach derives leakage inductance for concentric windings (common in power transformers) by integrating the magnetic energy stored in the space between and within the windings:

```
L_leak = mu_0 * N^2 * l_w * (d_12/3 + d_gap/1)
```

for two concentric windings, where d_12 is the winding thickness and d_gap is the insulation gap between windings. The factor of 1/3 arises from the linear MMF distribution within each winding.

**For interleaved windings (P-S-P-S with n_s sections):**

```
L_leak_interleaved = L_leak_non-interleaved / n_s^2
```

Interleaving into 2 sections reduces leakage by 4x; into 3 sections by 9x. This relationship is exact for the 1D model and approximate for real geometries.

**Methods to minimize leakage inductance:**
- Interleave primary and secondary windings (most effective)
- Reduce insulation gap between windings (limited by safety standards)
- Use wider winding breadth (shorter magnetic path for leakage flux)
- Use bifilar or twisted-pair windings for closely coupled transformers

### 23.5 Core and Air Gap Influence on Proximity Losses

Albach provides a detailed treatment of how the core and air gap modify the field distribution in the winding window, affecting proximity losses. This extends the existing coverage (Muhlethaler 2D image method in Section 13.2):

**Field distribution in the winding window** is computed by solving for the magnetic vector potential in the core window cross-section. The core boundaries act as flux guides (mu -> infinity approximation) that constrain the field lines.

**Key findings:**
- **Without an air gap:** The field in a well-filled winding window is approximately 1D (parallel to layers), and Dowell's analysis is adequate.
- **With an air gap:** The fringing field from the gap creates a 2D field pattern that dramatically increases proximity losses in conductors near the gap. Albach quantifies this with a position-dependent loss factor.
- **Flux displacement in the core cross-section (Flussverdrengung):** At high frequencies, eddy currents within the core itself cause the flux to crowd toward the core surface, reducing the effective cross-sectional area and increasing the effective reluctance. This effect is significant for MnZn ferrites above ~500 kHz.

**Winding position optimization:**
- Conductors should be placed as far as possible from air gaps
- For center-gapped E-cores, avoid placing any winding turns directly adjacent to the gap
- For side-gapped cores (spacer gap), the fringing field extends into the space around the core and can couple into nearby PCB traces or components
- Albach provides graphs of the proximity loss penalty factor as a function of distance from the gap, normalized to gap length

### 23.6 EMC Aspects of Inductive Components

Albach dedicates an entire chapter to electromagnetic compatibility, providing analytical models for:

**Common-mode noise generation in transformers:**
- The interwinding capacitance couples switching transients (dV/dt) from primary to secondary, creating common-mode current
- The common-mode current is: I_cm = C_ps * dV/dt, where C_ps is the primary-to-secondary capacitance
- Reducing C_ps by interleaving is counterproductive because it increases coupling; instead, use a **Faraday shield** (grounded electrostatic screen between windings)

**Stray magnetic field from inductive components:**
- Gapped cores radiate magnetic field from the gap region
- The stray field at distance r from a gap carrying flux Phi decays as approximately 1/r^3 (magnetic dipole behavior)
- For EMC compliance, either minimize the gap (use powder cores with distributed gap) or use closed core geometries (pot cores, RM cores) that contain the fringing flux

**Inductive components as EMI filters:**
- Common-mode chokes require high leakage inductance (for differential-mode filtering) and high magnetizing inductance (for common-mode filtering)
- The parasitic capacitance of the choke limits its high-frequency effectiveness: above the self-resonant frequency, the choke becomes capacitive and provides no filtering
- Albach provides design guidelines for optimizing the self-resonant frequency by controlling winding capacitance

### 23.7 Extended Equivalent Circuit Models

Albach develops comprehensive equivalent circuits for inductors and transformers that include all parasitic elements:

**Inductor equivalent circuit:**
```
      L_main
  o---UUUU---+---o
              |
          C_parallel
              |
          R_parallel (core loss)
              |
  o-----------+---o
```
Plus series resistance R_s (DC + skin + proximity losses), which is frequency-dependent.

**Transformer equivalent circuit (capacitive network):**
- Six capacitances for a two-winding transformer: C_11, C_22 (self-capacitances), C_12 (interwinding), C_1g, C_2g (winding-to-ground), C_12g (coupling through ground)
- These capacitances determine the common-mode and differential-mode behavior at high frequency
- The capacitive network must be measured (Albach provides the measurement procedure using impedance analyzer sweeps) because analytical calculation has limited accuracy for complex geometries

---

## 24. Practical Magnetics Circuits (from Engineer's Notebook)

Source: Stefan Hollos and J. Richard Hollos, "Engineer's Notebook on Inductor and Transformer Circuits," Abrazol Publishing, 2022.

This small reference provides worked circuit problems with analytical solutions and SPICE simulation verification. The content below highlights practical modeling approaches not covered elsewhere in this guide.

### 24.1 Magnetic Circuit Modeling in SPICE

The book demonstrates modeling magnetic components in ngspice using the XSPICE **lcouple** (magnetic coupling) and **core** (reluctance) models. This approach separates the magnetic circuit from the electrical circuit:

- Each winding is represented by an **lcouple** element that converts electrical current to magnetomotive force (MMF = N*I) in the magnetic circuit domain
- The core is represented by a **reluctance** element: R_m = l / (mu * A)
- The magnetic flux Phi is the "current" in the magnetic circuit, and MMF is the "voltage"
- For saturable cores, the core model accepts B-H curve data as arrays: H_array and B_array

This approach naturally handles:
- Multiple coupled windings on a single core (each lcouple feeds into the same magnetic circuit node)
- Nonlinear core behavior (saturation, hysteresis)
- Core with air gaps (additional linear reluctance in series)

### 24.2 Inductor with Saturable Core

The notebook works through the transient response of an LR circuit with a core that has a piecewise-linear B-H curve (linear region up to Hsat, then mu drops to zero):

- In the linear region, the circuit behaves as a standard LR circuit with time constant tau = L/R
- When the core saturates, the inductance drops to near zero and the current jumps immediately to V/R
- The time to saturation from a step input is: t_sat = tau * ln(V_R / (V_R - I_sat * R)), where I_sat is the saturation current

This models the behavior of a flyback transformer primary under overload or a saturating inductor used as a magnetic switch.

### 24.3 CCFL Transformer (Step-Up Application)

The notebook provides a complete analysis of a cold-cathode fluorescent lamp (CCFL) transformer with:
- Push-pull primary (two N-turn windings)
- Feedback winding (N/4 turns)
- High-voltage secondary (100N turns)
- Saturable core (used for self-oscillation in Royer oscillator configuration)

Key analytical results for the four winding currents are derived using Laplace transforms, giving exact closed-form expressions. The time constant is:

```
tau = L * (1/R1 + 1/R2 + 1/(32*R3) + 10000/R4)
```

where L = N^2/R_m is the magnetizing inductance and R1-R4 are the resistances in each winding circuit.

### 24.4 Royer Oscillator

The Royer oscillator uses core saturation as a switching mechanism: when the core saturates, the feedback winding voltage collapses, turning off the conducting transistor and turning on the other. The oscillation frequency is determined by the time to saturate the core:

```
f_osc = 1 / (2 * t_sat) ~ V_supply / (4 * N * Bsat * A_core)
```

This is a practical example of a self-oscillating converter that requires no external clock and is commonly used in low-cost DC-AC inverters and CCFL backlight drivers.

### 24.5 Center-Tapped Transformer Analysis

For a transformer with center-tapped secondary driving two equal loads R, the current ratio between the two halves under balanced conditions satisfies 3*I_2 = 2*I_3 (due to the shared magnetic path). This asymmetry in current distribution is often overlooked in simplified analyses that assume ideal transformer behavior.

> **PyOpenMagnetics:** The library handles multi-winding transformer simulation through
> `simulate(mas)`. For transient analysis including saturation effects, use the ngspice-based
> SPICE simulation capabilities described in AGENTS.md.

---

## Ferroxcube Practical Design Data

Source: Ferroxcube Soft Ferrites and Accessories Data Handbook (2013).

### Material Selection for Power Applications

The following table summarizes Ferroxcube power ferrite materials. Select based on switching frequency and operating conditions.

| Material | µi | Bsat (mT) @25C | Tc (C) | Freq Range | Best For |
|----------|-----|----------------|--------|------------|---------|
| 3C81 | 2700 | ~450 | >=210 | <100 kHz | Low-freq power, loss minimum ~60C |
| 3C90 | 2300 | ~470 | >=220 | <200 kHz | General industrial SMPS |
| 3C91 | 3000 | ~470 | >=220 | <300 kHz | Medium-freq, loss minimum ~60C |
| 3C92 | 1500 | ~520 | >=280 | <200 kHz | Power inductors, output chokes (highest Bsat) |
| 3C93 | 1800 | ~500 | >=240 | <300 kHz | Medium-freq, loss minimum ~140C (high-temp apps) |
| 3C94 | 2300 | ~470 | >=220 | <300 kHz | Medium-freq, low losses at high flux density |
| 3C95 | 3000 | ~530 | >=215 | <300 kHz | High µi + high Bsat, good for flyback/forward |
| 3C96 | 2000 | ~500 | >=240 | <400 kHz | Very low losses at high flux density |
| 3F3 | 2000 | ~440 | >=200 | 200-700 kHz | High frequency power conversion |
| 3F35 | 1400 | ~500 | >=240 | 500 kHz-1 MHz | Very low losses ~500 kHz |
| 3F4 | 900 | ~410 | >=220 | 1-2 MHz | Resonant converters (LLC, etc.) |
| 3F45 | 900 | ~420 | >=300 | 1-2 MHz | Resonant converters, high Tc |
| 3F5 | 650 | ~380 | >=300 | 2-4 MHz | Very high frequency resonant |
| 4F1 | 80 | ~320 | >=260 | 4-10 MHz | NiZn, very high frequency |

All power materials above are MnZn ferrite except 4F1 (NiZn). MnZn ferrites have resistivity ~1-10 Ohm-m; NiZn ~10^5 Ohm-m.

### Material Selection Guidelines

**Key selection criteria:**
1. **Switching frequency** determines the material family (see table above)
2. **Operating temperature** -- each material has a loss-vs-temperature curve with a minimum. Choose material whose loss minimum matches expected operating temperature:
   - 3C81, 3C91: loss minimum ~60C (consumer/office)
   - 3C93: loss minimum ~140C (automotive, industrial)
   - 3C94, 3C96: loss minimum ~80-100C (typical SMPS)
3. **Flux density** -- for inductors storing energy under DC bias, 3C92 (Bsat=520 mT) is preferred. For transformers, 3C95 (Bsat=530 mT, high µi) is excellent.
4. **Cost** -- 3C90 is the lowest-cost general-purpose power material

**Performance factor (f x Bmax):** A key figure of merit at a given loss density (typically 500 mW/cm3). Higher is better. At each frequency, there is an optimal material:
- <200 kHz: 3C90, 3C94, 3C96 are equivalent
- 200-500 kHz: 3C96 > 3C94 > 3F3
- 500 kHz-1 MHz: 3F35 > 3F3
- 1-2 MHz: 3F4, 3F45
- 2-4 MHz: 3F5

### EMI Suppression Materials

For common-mode chokes and EMI filters:

| Material | µi | Type | Freq Range | Notes |
|----------|------|------|-----------|-------|
| 3E25 | 6000 | MnZn | <1 MHz | Current-compensated chokes |
| 3E5 | 10000 | MnZn | <1 MHz | High µi EMI chokes |
| 3E6 | 12000 | MnZn | <1 MHz | Very high µi |
| 3S1 | 4000 | MnZn | 1-30 MHz | Wideband suppression |
| 4A11 | 850 | NiZn | 30-1000 MHz | High frequency suppression |
| 4C65 | 125 | NiZn | 50-1000 MHz | Highest freq NiZn |
| 3S3 | 350 | MnZn | 30-1000 MHz | Wideband, lower µi |

### Core Type vs. Power Range

| Power Range | Recommended Core Types |
|-------------|----------------------|
| <5 W | RM4, P11/7, EF13, U10 |
| 5-10 W | RM5, P14/8 |
| 10-20 W | RM6, E20, P18/11, EFD15, U15 |
| 20-50 W | RM8, RM10, ETD29, E25, EFD20, P22/13, U20 |
| 50-100 W | ETD29, ETD34, EC35, EC41, RM12, P30/19, EFD25 |
| 100-200 W | ETD34, ETD39, ETD44, EC41, EC52, RM14, E30, E42, EFD30, P36/22 |
| 200-500 W | ETD44, ETD49, E55, EC52, E42, P42/29, U67 |
| >500 W | E65, EC70, U93, U100, P66/56, PM87, PM114 |

### Output Choke Design with Gapped Ferrites

Output chokes operate under DC bias. Without an air gap, power ferrites (3C90, 3F3) saturate at ~50 A/m.

**Remedies:**
1. **Gapped ferrite cores**: An air gap dramatically increases the allowable DC bias. The energy storage capability is characterized by the I²L product.
2. **Iron powder cores (2P material)**: Distributed gap, inherently high DC bias capability, but higher core losses than ferrite at equivalent conditions.

**Gapped core selection procedure:**
1. Calculate required I²L = L * I_DC² (in Joules)
2. Select the smallest core from the I²L graphs where the required I²L is achievable within the gap range
3. For a given core, read the required air gap from the graph
4. Verify that the effective permeability (µe) at that gap provides acceptable inductance with a reasonable number of turns: N = sqrt(L / AL)

### Power Transformer Design

**Flyback transformers** require high Bsat and moderate µi. 3C95 is recommended (530 mT Bsat). For higher temperature designs, 3C96 or 3C93.

**Forward transformer** -- similar material choices. 3C94 and 3C96 offer good performance at 100-300 kHz.

**Half/full-bridge transformers** operate with bipolar excitation (no DC bias). Choose material purely based on core loss at operating frequency and flux swing.

**Resonant converter (LLC) transformers** operate at higher frequencies (200 kHz to 2 MHz). Use 3F3 (200-700 kHz), 3F4/3F45 (1-2 MHz), or 3F5 (2-4 MHz).

### Practical Core Data

**Effective core parameters (selected common shapes):**

| Core | Ae (mm²) | le (mm) | Ve (mm³) | AL,ungapped (nH) |
|------|----------|---------|----------|-------------------|
| E 25/13/7 (3C90) | 52.0 | 57.5 | 2990 | ~2700 |
| E 42/21/15 (3C90) | 178 | 97.2 | 17300 | ~4200 |
| ETD 29 (3C90) | 76.0 | 72.0 | 5470 | ~2430 |
| ETD 34 (3C90) | 97.1 | 78.6 | 7640 | ~2840 |
| ETD 44 (3C90) | 173 | 103 | 17800 | ~3860 |
| ETD 49 (3C90) | 211 | 114 | 24000 | ~4260 |
| EFD 20 (3F3) | 31.0 | 47.0 | 1460 | ~1300 |
| EFD 25 (3F3) | 58.0 | 57.0 | 3310 | ~2030 |
| RM 8 (3C90) | 62.9 | 38.4 | 2420 | ~3760 |
| RM 10 (3C90) | 96.6 | 44.6 | 4310 | ~4990 |

> **PyOpenMagnetics**: Use `find_core_shape_by_name()` and `find_core_material_by_name()` for
> exact parameters from the built-in database (1301+ shapes, 409+ materials). The tables above
> are for quick reference only.
