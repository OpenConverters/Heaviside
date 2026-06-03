---
description: Design an Active Clamp Flyback (ACF) converter from specs — ZVS-capable, leakage energy recovery, 90-97% efficiency. Includes analytical equations, component stresses, dead time calculation, Lm sizing for ZVS, control loop, and ngspice netlist template.
---

# Active Clamp Flyback (ACF) Converter Design

## When to Use

| Condition | Action |
|-----------|--------|
| Isolated, 30–150W, target η ≥ 90% | **ACF is the recommended topology** |
| Isolated, 30–75W, target η ≥ 93% | **ACF + SR is mandatory** |
| Isolated, 75–180W, target η ≥ 95% | **ACF + SR + PFC front-end** |
| Same as above but budget-constrained | Use QR flyback + SR as fallback (~92% ceiling) |
| > 150W isolated, η ≥ 95% | Consider LLC half-bridge instead |

**ACF vs passive-RCD flyback:** ACF recovers the leakage inductance energy (which RCD clamps burn as heat) and achieves ZVS turn-on of the main switch. This saves **5–9% efficiency** at 60–100W. The price: one extra FET (clamp switch Qa), one capacitor (Cclamp), and complementary gate drive timing.

**Reference designs using ACF:**
- Power Integrations InnoSwitch4-CZ + ClampZero (DER-930: 95% at 180W)
- TI UCC28780-based designs (TIDA-01622: 94% at 65W)
- onsemi NCP1568 family

---

## Circuit Description

```
                    T1 (Lm, Llk, n=Np:Ns)
          +----[Llk]---+---[pri dot]---+
          |             |              |
Vbus ----+             sw_node        sec_dot --[D_sr/SR]-- Vout
          |             |              |                      |
          |    Cclamp --+-- Qa (clamp) |                     Co
          |    (to Vbus top)           |                      |
          |             |              0 (sec return) --------+
          +--- Q1 (main)---+
                          |
                          GND (pri return)
```

**Components:**
- **Q1** (main FET): primary switch, drain = `sw_node`, source = GND
- **Qa** (clamp FET): drain = `clamp_node`, source = `sw_node`
- **Cclamp**: from `clamp_node` to Vbus+. Stores leakage energy.
- **T1**: flyback transformer with air gap. Primary `Lm` (magnetizing inductance), series leakage `Llk`. Turns ratio `n = Np/Ns`.
- **Coss_Q1, Coss_Qa**: output capacitances — explicitly needed for ZVS resonance calculation
- **SR or diode**: secondary rectifier

**Energy flow per cycle (4 intervals):**

```
Interval 1: Q1 ON (duration t_on = D/fsw)
  → Energy stored in Lm + Llk. Imag ramps up from Imag_valley.
  → Secondary SR is off. SR FET Vds_SR = Vout + Vbus/n  [NOT Vout + VOR — see Step 5]

Interval 2: Dead time 1 (Q1 off → Qa on, duration t_d1 ≈ 50-200 ns)
  → Q1 turns off. Llk + Imag current charges Vds_Q1 toward Vclamp+Vbus.
  → Qa body diode clamps: energy enters Cclamp via body diode.

Interval 3: Qa ON (duration t_clamp = (1-D)/fsw - t_d1 - t_d2)
  → Cclamp connected to sw_node via Qa. Llk resonates with Cclamp.
  → Magnetizing current REVERSES: Imag decays from positive through zero to negative.
  → This negative Imag is the ZVS engine for Q1 re-turn-on.
  → Secondary SR conducts during part of this interval if VOR > 0 (current flows back).

Interval 4: Dead time 2 (Qa off → Q1 on, duration t_d2 ≈ 50-200 ns)
  → Qa turns off. Negative Imag + Llk current discharges Vds_Q1 toward zero.
  → Q1 turns on at VDS = 0 V → ZERO SWITCHING LOSS.
  → SR turns off when secondary current hits zero.
```

**Key difference from ACF forward:** In ACF-flyback, the transformer has an air gap (energy storage). The clamp forces magnetizing current reversal, which builds the negative inductor current that discharges Q1's Coss. In ACF-forward, the transformer has no gap — the clamp only recycles reset energy.

---

## Design Procedure

### Step 1: Choose Turns Ratio

Same formula as standard flyback, but ACF can operate at **higher D** (0.45–0.65) because the clamp FET provides controlled voltage clamping:

```
VOR_target = 90–120 V  (for universal AC input: Vbus_nom = 325 V at 230VAC)
n = Np/Ns = VOR_target / (Vout + Vf)

D_nom = VOR / (Vbus_nom + VOR)
D_max = VOR / (Vbus_min + VOR)   ← worst case at Vin_min
D_min = VOR / (Vbus_max + VOR)   ← worst case at Vin_max
```

For ACF, keep D_max ≤ 0.65. The clamp FET sees Vds_Qa = Vbus/(1-D) — higher D means higher clamp voltage. At D=0.65: Vclamp = 0.65/0.35 × Vbus ≈ 1.86 × Vbus. For Vbus=375V, Vds_Qa_max ≈ 700V → use 800V-rated FET.

ACF advantage: operating at higher D reduces primary peak current for the same Pout.

### Step 2: Size Lm for ZVS (CRITICAL — differs from standard flyback)

Standard flyback sizes Lm from ripple ratio (r=0.4). **ACF must size Lm smaller** to guarantee sufficient negative magnetizing current (Imag_rev) to discharge Coss before Q1 turns on.

**ZVS condition:**
```
Energy available from Imag_rev ≥ Energy to discharge Ceq
0.5 × Lm × Imag_rev² ≥ 0.5 × Ceq × Vbus²

→ Imag_rev_min = Vbus × sqrt(Ceq / Lm)

where:
  Ceq = Coss_Q1 + Coss_Qa + C_parasitic   [total switch node capacitance]
  Imag_rev = amount of negative magnetizing current when Qa turns off
```

**Imag_rev calculation:**
During the clamp interval (Qa ON), the magnetizing inductance resonates with Cclamp. For practical Cclamp (100–470 nF, much larger than Coss), the magnetizing current ramp is approximately linear:
```
Imag_rev ≈ Imag_peak × (t_clamp/t_half_resonance)   [simplified]

More accurately: the volt-second balance during clamp interval:
  Vclamp = D/(1-D) × Vbus
  During t_clamp: ΔImag = Vclamp × t_clamp / Lm  (magnetizing current reverses by this amount)
  Imag_rev = Imag_peak - ΔImag/2   (midpoint reversal, assuming linear ramp)
```

**Practical Lm sizing recipe:**
```
1. Start with standard flyback Lm from r=0.4:
   Lm_std = Vbus_min × D_max / (0.4 × Iin_avg × fsw)

2. Apply ZVS scaling factor α = 0.4–0.7 (lower α = easier ZVS, higher losses):
   Lm_ACF = α × Lm_std

3. Verify ZVS margin at Vbus_max (hardest case: highest Vbus, lowest Imag_peak):
   Imag_pk_at_Vbus_max = Vbus_max × D_min / (Lm_ACF × fsw)
   Imag_rev ≈ 0.5 × Imag_pk_at_Vbus_max  (approximate: clamp interval ~ t_clamp ≈ (1-D)*T)
   Required: Imag_rev ≥ Vbus_max × sqrt(Ceq / Lm_ACF)

4. If ZVS not met, reduce Lm_ACF further (try α = 0.3) or increase dead time.

ZVS margin — use ENERGY RATIO (dimensionless):
  ZVS_margin = E_avail / E_required
             = (0.5 × Lm × Imag_rev²) / (0.5 × Ceq × Vbus_max²)

⚠ Do NOT report ZVS margin as a percentage — "170%" is ambiguous and
  inconsistent with the energy ratio. A 22.6× energy ratio means abundant margin.
  Minimum recommended: ZVS_margin ≥ 1.5× (50% margin above the ZVS threshold).
  Typical well-designed ACF: 5×–25×.

Typical values:
  Lm_ACF ≈ 0.4–0.6 × Lm_standard_flyback
  For 60W/12V at 66kHz: Lm_std ≈ 600µH → Lm_ACF ≈ 250–380µH
  For 180W/20V at 100kHz: Lm_std ≈ 1334µH → Lm_ACF ≈ 667µH (α=0.5)
```

**Trade-off:** Smaller Lm → larger peak current → higher conduction losses. The optimal Lm minimizes total losses (conduction + switching), which is a function of Ceq, Rds_on, and fsw.

### Step 3: Clamp Capacitor Sizing

```
Vclamp_cap = Vbus × D/(1-D)   [voltage across Cclamp, above the positive Vbus rail]
Vds_Q1_max = Vbus + Vclamp_cap = Vbus/(1-D)   [total Q1 drain-to-source voltage]

⚠ TERMINOLOGY: "Vclamp" is ambiguous. Use:
  Vclamp_cap = capacitor voltage above Vbus = D/(1-D) × Vbus  (e.g., 133V at D=0.25, Vbus=400V)
  Vds_Q1_max = total Q1 Vds = Vbus/(1-D)  (e.g., 533V at D=0.25, Vbus=400V)

Cclamp sizing — use ENERGY BALANCE, not the charge-ripple formula:
  Energy to store per cycle: E_llk = 0.5 × Llk × Ipk²
  ΔVclamp ≤ 5% × Vclamp_cap  (keep ripple < 5% of cap voltage)
  Cclamp_min = 2 × E_llk / (ΔVclamp × Vclamp_cap)
             = 2 × 0.5 × Llk × Ipk² / (0.05 × Vclamp_cap × Vclamp_cap)
             = Llk × Ipk² / (0.05 × Vclamp_cap²)

⚠ WRONG FORMULA (do not use):
  Cclamp_min = Imag_pk / (ΔV × fsw × (1-D))
  This is a charge-balance formula that gives results 100× too small because
  it omits the voltage level in the denominator. The arithmetic error produces
  ~52nF where the correct minimum is ~5µF for a 180W design.

Example (180W/20V ACF, Llk=7µH, Ipk=2.62A, Vclamp_cap=133V, ΔV=5%):
  Cclamp_min = 7e-6 × 2.62² / (0.05 × 133²) = 47.9µJ / 884.5 = 54 nF

However: NCP1568, UCC28780, and similar ACF controller app notes recommend
  100–470 nF for 100–300W designs based on magnetizing current balance
  and gate drive supply regulation. The 220nF value from app notes is correct
  in practice — the energy-balance formula gives the leakage minimum only.
  Use: 220nF / 250V film as default for 60–200W designs.
  Verify: AC current rating at fsw. I_Cclamp_rms ≈ I_Qa_rms (same current path).

Film capacitor mandatory (not electrolytic) — sees AC ripple current at fsw.
Film cap ESR should be < 0.5 Ω.
```

### Step 4: Dead Time Calculation

```
The optimal dead time allows the Q1 drain voltage to resonantly discharge
from Vclamp+Vbus to 0V before Q1 gate goes high.

Resonant half-period:
  t_d_opt = π × sqrt(Lm × Ceq) / 2   [quarter-wave resonance is sufficient]
           ≈ π × sqrt(Lm × Ceq) / 4   [practical: use 1/4 of resonant period]

where Ceq = Coss_Q1 + Coss_Qa (Cclamp >> Coss, dominated by Coss)

Example: Lm=300µH, Ceq=300pF:
  t_res = 2π × sqrt(300e-6 × 300e-12) ≈ 18.8 µs   → quarter-period = 4.7 µs
  This is too long for a 100kHz converter (T=10µs).

Reality: at practical frequencies (66–130kHz), full ZVS via Lm resonance
requires very short dead times (50–200ns) because Lm is much larger than Coss.
The actual mechanism is: Llk (leakage inductance, 2–10µH) provides fast
initial discharge, then Lm sustains it.

Correct dead time (uses Llk, not Lm):
  t_d = π × sqrt(Llk × Ceq) / 2   [half-resonance of Llk with Coss]

Example: Llk=5µH, Ceq=300pF:
  t_d = π × sqrt(5e-6 × 300e-12) = 96 ns   ✓ (practical!)

Rule of thumb: t_d = 100–200 ns for most offline flybacks.
```

### Step 5: Component Stresses

**Main FET Q1:**
```
Vds_Q1_max = Vbus + Vclamp = Vbus / (1-D)   [CLAMPED — no leakage spike]
             (vs Vbus + VOR + Vspike in standard flyback)
Advantage: lower voltage rating needed. At D=0.5, Vds_max = 2×Vbus.
For 325V bus, D=0.45: Vds_max = 325/0.55 = 591V → use 650V FET (vs 800V for RCD flyback)

I_Q1_peak = Imag_peak = Iin_avg/D × (1 + r/2) × D  [primary peak]
I_Q1_rms = Iin_avg × sqrt(D) × sqrt(1 + r²/12)   [same as standard flyback]
```

**Clamp FET Qa:**
```
Vds_Qa_max = Vbus / (1-D)   [same as Q1]
I_Qa_rms = sqrt((1-D)/3 × (Ipk² + Ipk×(-Imag_rev) + Imag_rev²))
         ≈ sqrt((1-D)/3) × sqrt(Ipk² - Ipk×Imag_rev + Imag_rev²)

⚠ COMMON ERROR: Using I_Qa_rms ≈ Imag_rev/sqrt(3) × sqrt(1-D) ONLY captures
  the negative magnetizing reversal tail. During the FULL Qa conduction interval,
  Qa conducts the primary current ramping from +Ipk through zero to −Imag_rev.
  The correct RMS includes the positive half as well.

Example (180W/20V ACF): Ipk=2.62A, Imag_rev=1.31A, D=0.25
  I_Qa_rms = sqrt(0.75/3 × (6.86 − 3.43 + 1.72)) = sqrt(0.25 × 5.15) = 1.13A
  (vs 0.38A from the old underestimate — 3× too low)
  P_Qa = 1.13² × 80mΩ = 0.102W  (not 0.017W)

I_Qa_peak = Imag_rev   [negative magnetizing current peak]

Note: Qa can be a smaller FET than Q1 — it carries the full-swing reversal
current, but the RMS is still lower than Q1 because Qa only conducts for (1-D)
of the period. Typically 60-80% of Q1's current rating.
```

**Clamp capacitor Cclamp:**
```
Vcclamp_avg = Vclamp = D × Vbus / (1-D)
Vcclamp_max = Vcclamp_avg × (1 + 0.05)   [5% ripple spec]
I_Cclamp_rms ≈ Imag_rev / sqrt(3)

Select: V_rating ≥ 1.5 × Vcclamp_max; film capacitor; low ESR
```

**Secondary SR FET or diode:**
```
V_SR_max = Vout + Vbus/n   [correct formula: secondary winding sees Vbus/n when Q1 is ON]
           = Vout + Vbus_max/n    [worst case at Vbus_max]

⚠ COMMON ERROR: Do NOT use "Vout + VOR" for SR stress.
  VOR = n × Vout is the primary-referred output voltage — it does NOT appear directly
  across the SR FET. The correct secondary voltage stress is Vout + Vbus/n.
  These are equal only when D = 0.5 (Vbus/n = VOR/n² × n = VOR·D/(1-D) → equal at D=0.5).
  At D=0.25 and n=6.67: VOR=133V (wrong) vs Vbus/n=60V (correct). Off by 2.2×.

I_SR_peak = Imag_pk × n    [same as standard flyback]
I_SR_rms  = I_SR_peak/sqrt(3) × sqrt(1-D)   [triangular secondary current in DCM]

SR FET voltage rating rule:
  V_SR_rated ≥ 1.5 × (Vout + Vbus_max/n)    [IPC-9592B 1.5× derating]

Example: Vout=20V, n=6.67, Vbus_max=450V:
  V_SR_max = 20 + 450/6.67 = 87.5V → minimum 131V rated FET for 1.5× derating.
  80V FETs are OUT. 100V FETs are borderline (1.14×). Use 150V for comfortable margin.

Note: In ACF, the SR stays on during part of the Qa interval if magnetizing
current reverses far enough to drive secondary current positive. This is
handled automatically by a synchronous rectifier controller sensing current
direction, or by the InnoSwitch/UCC28780 integrated SR gate drive.
```

### Step 6: Transformer Design for ACF

ACF uses the same flyback transformer structure (gapped core, Lm sized above). Key differences:
- **Llk matters more:** Llk drives the initial ZVS discharge. Target Llk = 0.5–2% of Lm.
- **Lm is smaller:** ACF Lm ≈ 0.4–0.6× standard flyback Lm → higher Bpk, larger core or higher fsw.
- **Winding technique:** Interleaved or bifilar primary to minimize Llk.

Pass the following to the magnetics-designer agent:
```json
{
  "topology": "flyback",
  "lm_target_H": <Lm_ACF in Henries>,
  "turns_ratio_Np_Ns": <n>,
  "llk_max_fraction": 0.01,
  "zvs_mode": true,
  "note": "ACF: Lm is 40-60% of standard flyback value to ensure ZVS"
}
```

PyOM does not have a dedicated `active_clamp_flyback` topology — use `flyback` with the ACF-sized Lm as target.

### Step 7: Loss Budget

```
P_cond_Q1    = I_Q1_rms² × Rds_Q1
P_cond_Qa    = I_Qa_rms² × Rds_Qa
P_switching  ≈ 0   (ZVS achieved → Coss energy recycled, no dissipation)
               [residual: gate drive loss = Qg × Vgate × fsw for Q1 + Qa]
P_clamp_eff  = I_Qa_rms² × Rds_Qa   [conduction of clamp current only]
P_core       = from PyOM or Steinmetz
P_winding    = from PyOM (primary + secondary)
P_SR         = I_SR_rms² × Rds_SR   [SR FET, much less than diode]
P_bridge     = 2 × Vf × Iin_avg    [bridge rectifier on AC input]

Total_losses = P_cond_Q1 + P_cond_Qa + P_gate + P_core + P_winding + P_SR + P_bridge
η = Pout / (Pout + Total_losses)
```

**Comparison vs RCD flyback at 60W/230VAC:**
```
RCD flyback: P_clamp_RCD ≈ 0.5 × Llk × Ipk² × fsw ≈ 0.6–2W (dissipated in resistor)
ACF:         P_switch ≈ 0 (ZVS); P_Qa_cond ≈ 0.1–0.3W (recycled, not dissipated)
Net gain: 0.5–1.5W → 1–2.5% efficiency improvement from clamp alone.

Additional gain from reduced Q1 voltage rating:
  ACF allows 650V FET vs 800V FET for same bus voltage
  650V FET typically has 30–50% lower Rds_on → 0.2–0.5W conduction saving
```

---

## Control Loop

### Small-Signal Transfer Function

The ACF flyback has the same plant structure as the standard flyback in voltage-mode control, with two key differences:

**1. Right-Half-Plane Zero (RHPZ):**
The RHPZ is still present but at a **higher frequency** because Lm_ACF < Lm_standard:
```
f_RHPZ = Vout × (1-D)² / (2π × Lm_ACF/n² × Iout)
       = Vout × (1-D)² × n² / (2π × Lm_ACF × Iout)

This is higher than standard flyback by factor (Lm_std / Lm_ACF) = 1/α ≈ 1.5–2.5×
→ ACF allows higher control bandwidth than standard flyback.
```

**2. Secondary pole from Cclamp resonance:**
The Cclamp introduces a high-frequency pole in the loop. For typical Cclamp = 100–470 nF:
```
f_clamp_pole ≈ 1 / (2π × Rclamp_esr × Cclamp)
```
This is typically > 100 kHz — well above the crossover frequency — and can be ignored.

**Compensation:**
Use standard Type 2 (voltage-mode, DCM) or Type 3 (CCM) compensator, same as standard flyback. The RHPZ is less restrictive in ACF, so crossover can be pushed higher (10–20% of RHPZ vs 5–10% for standard flyback).

**Burst mode at light load:**
When load drops below ~20% of full load, Imag_rev becomes insufficient for ZVS (the magnetizing current doesn't reverse enough). The controller must:
- Either increase Lm (by pulse skipping / burst mode): switch to burst/skip-cycle mode
- Or reduce dead time to match the lower Imag_rev

Burst mode (skip-cycle) is the standard approach for ACF at light load. This is handled by integrated controllers (UCC28780, NCP1568, InnoSwitch4-CZ) automatically. For discrete implementations, add a comparator monitoring Vclamp voltage — when Vclamp drops below threshold (indicating insufficient energy), initiate burst mode.

**Practical controller ICs for ACF flyback:**
- TI UCC28780 (full ZVS ACF controller, valley detection, SR drive)
- onsemi NCP1568 (variable-frequency ACF)
- PI InnoSwitch4-CZ (integrated GaN FET + ACF + SR, "ClampZero" = ACF in one IC)
- Monolithic Power MP44018 (ACF controller)

For simulation purposes, model the controller as: fixed duty cycle + complementary gate with adjustable dead time.

---

## Ngspice Netlist Template

```spice
* Active Clamp Flyback (ACF) Converter — Heaviside Template v1.0
* 
* TOPOLOGY: Isolated ACF flyback with ZVS main switch and complementary clamp
* Vin_bus: {Vbus} V DC (rectified AC or DC bus from PFC stage)
* Vout: {Vout} V / {Iout} A
* Pout: {Pout} W
* fsw: {fsw} Hz
* Turns ratio Np:Ns = {n}:1 (n = Np/Ns)
* Lm: {Lm} H  (ACF-sized: ~0.4-0.6x standard flyback Lm)
* Llk: {Llk} H  (primary leakage inductance, ~0.5-2% of Lm)
* Cclamp: {Cclamp} F  (film cap, clamp energy storage)
* Dead time: {t_dead} s  = pi * sqrt(Llk * Ceq) / 2
*
* References:
*   - knowledge/topologies/active-clamp-flyback.md (this file)
*   - Basso, "Switch-Mode Power Supplies", ACF chapter
*   - TI UCC28780 datasheet and application note
*
* NOTE ON CONVERGENCE:
*   ACF is harder to converge than standard flyback due to resonant transitions.
*   Use .options ABSTOL=1e-9 VNTOL=1e-5 RELTOL=0.003 ITL4=200
*   Add UIC to .tran
*   If Vds swings cause convergence failure, add 100pF snubber across Q1

.title ACF Flyback — {Pout}W / {Vout}V

* ── Parameters ──────────────────────────────────────────────────────────────
.param Vbus={Vbus}
.param Vout_nom={Vout}
.param Iout={Iout}
.param fsw={fsw}
.param Lm={Lm}
.param Llk={Llk}
.param n={n}
.param Cclamp={Cclamp}
.param Rload={Vout}/{Iout}
.param D={D_nom}
.param t_dead={t_dead}
* Compute switch timings
.param T=1/{fsw}
.param t_on={D}*{T}
.param t_off=(1-{D})*{T} - 2*{t_dead}

* ── Input supply ─────────────────────────────────────────────────────────────
Vbus dc_bus 0 DC {Vbus}
Cin  dc_bus 0 10u IC={Vbus}

* ── Primary switch Q1 ────────────────────────────────────────────────────────
* Drain = sw_node, Source = 0 (GND)
* Rds_on modeled via SW model Ron parameter
.model SW_Q1 SW(Ron={Rds_Q1} Roff=10Meg Vt=2.5 Vh=0.5)
S_Q1 sw_node 0 gate_q1 0 SW_Q1
* Q1 output capacitance (explicit — needed for ZVS resonance)
Coss_Q1 sw_node 0 {Coss_q1} IC=0

* ── Clamp FET Qa ─────────────────────────────────────────────────────────────
* Drain = clamp_node, Source = sw_node  (connects Cclamp to switch node)
.model SW_Qa SW(Ron={Rds_Qa} Roff=10Meg Vt=2.5 Vh=0.5)
S_Qa clamp_node sw_node gate_qa 0 SW_Qa
* Qa body diode (conducts during dead time 1, charging Cclamp)
D_qa sw_node clamp_node D_body
.model D_body D(Is=1e-7 N=1.0 Rs=0.05 BV=1200)
* Qa output capacitance
Coss_Qa clamp_node sw_node {Coss_qa} IC=0

* ── Clamp capacitor ──────────────────────────────────────────────────────────
* Between clamp_node and dc_bus (positive rail)
* Initial condition: IC = D/(1-D) * Vbus
Cclamp_cap clamp_node dc_bus {Cclamp} IC={D_nom/(1-D_nom)*Vbus}

* ── Transformer (coupled inductors) ─────────────────────────────────────────
* PRIMARY: Lm (magnetizing) in series with Llk (leakage)
* Correct ACF flyback wiring:
*   dc_bus → Llk_ser → pri_dot (transformer primary dot) → sw_node
*   (Q1 source is GND; Q1 drain is sw_node)
*
* Leakage inductance — separate element in series
L_llk dc_bus pri_dot {Llk} IC=0
* Magnetizing inductance — primary winding (dot at pri_dot, other end at sw_node)
Lm_pri pri_dot sw_node {Lm} IC=0
* Secondary winding: Ls = Lm / n^2
* Dot at sec_dot, other end at sec_ret (secondary ground)
Ls_sec sec_dot sec_ret {Lm/n/n} IC=0
* Coupling (k should be ~1 after Llk is modeled separately)
K_T Lm_pri Ls_sec 0.9998

* ── Secondary SR FET or diode ────────────────────────────────────────────────
* SR FET: drain = sec_dot (anode of energy flow), source = Vout rail
* For diode-only: replace with .model D_SR D(...)
* SR gate: timed complementary to Q1, with blanking during dead times
.model SW_SR SW(Ron={Rds_SR} Roff=10Meg Vt=2.5 Vh=0.5)
* SR body diode (ensures current path during body-diode conduction)
D_SR_body sec_dot out_node D_sr_body_m
.model D_sr_body_m D(Is=1e-7 N=1.0 Rs={Rds_SR} BV=200)
S_SR sec_dot out_node gate_sr 0 SW_SR

* Secondary return
Rsec_ret sec_ret 0 0.001

* ── Output capacitor ─────────────────────────────────────────────────────────
* Use polymer cap (low ESR) for accurate efficiency
Cout out_node 0 {Cout} IC={Vout_nom}
Resr_cout out_node out_sense {Resr_cout}
* Load
Rload_R out_sense 0 {Rload}

* ── Gate drive waveforms ──────────────────────────────────────────────────────
* Q1: ON for t_on, then OFF. Starts at t=0.
* Timing: |--t_on--|--t_dead--|--t_off_Qa--|--t_dead--|
Vg_Q1   gate_q1 0 PULSE(0 10 0 2n 2n {t_on} {T})

* Qa: Complementary to Q1, delayed by t_dead on both transitions
* Qa ON: starts at t_on+t_dead, stays on for t_off
Vg_Qa   gate_qa 0 PULSE(0 10 {t_on+t_dead} 2n 2n {t_off} {T})

* SR: mirrors Qa timing (SR conducts when clamp FET is on = secondary freewheeling)
* Small additional delay (blanking) to prevent shoot-through
.param t_blank=20n
Vg_SR   gate_sr 0 PULSE(0 10 {t_on+t_dead+t_blank} 2n 2n {t_off-2*t_blank} {T})

* ── Simulation control ───────────────────────────────────────────────────────
* ACF needs tight timestep to resolve ZVS transitions (~Llk/Ceq resonance)
* tstep ≤ t_dead/10 is recommended
.param tstep=5n
.param tstop=3m
.param tmeas_start=2.5m

.options ABSTOL=1e-9 VNTOL=1e-5 RELTOL=0.003 ITL4=200 ITL5=0
.tran {tstep} {tstop} 0 {tstep} UIC

* ── Measurements ─────────────────────────────────────────────────────────────
.measure tran Vout_avg   AVG   v(out_sense)      FROM={tmeas_start} TO={tstop}
.measure tran Vout_pp    PP    v(out_sense)       FROM={tmeas_start} TO={tstop}
.measure tran Vds_Q1_max MAX   v(sw_node)         FROM={tmeas_start} TO={tstop}
.measure tran Vclamp_avg AVG   v(clamp_node)      FROM={tmeas_start} TO={tstop}
.measure tran Iin_avg    AVG   i(Vbus)            FROM={tmeas_start} TO={tstop}
* Efficiency
.measure tran Pin_avg    PARAM '-Iin_avg*{Vbus}'
.measure tran Pout_meas  PARAM 'Vout_avg*Vout_avg/{Rload}'
.measure tran eff_pct    PARAM 'Pout_meas/Pin_avg*100'

* ZVS verification — Vds_Q1 at Q1 turn-on must be near zero
* (ngspice doesn't support FIND...WHEN directly in all versions, but measure max Vds
*  during dead time 2 as proxy: if Vds_after_td2 < 10V, ZVS achieved)

.end
```

### Netlist Instantiation Example (60W/12V at 66kHz)

```spice
* Parameters for DER-1025 equivalent (60W, 12V/5A, 325V bus, 66kHz)
.param Vbus=325
.param Vout=12
.param Iout=5
.param fsw=66000
.param n=8.5          ; Np/Ns = 8.5 (VOR = 100V)
.param Lm=300e-6      ; 300µH (ACF: ~0.5x standard 600µH)
.param Llk=5e-6       ; 5µH leakage (0.8% of 600µH std value, 1.7% of Lm_ACF)
.param Cclamp=220e-9  ; 220nF film cap
.param D_nom=0.235    ; D at 325V bus: VOR/(Vbus+VOR) = 100/425
.param t_dead=100n    ; 100ns dead time: pi*sqrt(5u*300p)/2 ≈ 96ns
.param Coss_q1=150e-12 ; 150pF (typical 650V SiC FET)
.param Coss_qa=100e-12 ; 100pF (clamp FET, smaller)
.param Rds_Q1=0.04    ; 40mΩ (e.g. C3M0040065D, 650V SiC)
.param Rds_Qa=0.04    ; 40mΩ (same FET or smaller)
.param Rds_SR=0.002   ; 2mΩ (e.g. BSC012N10NS5, 100V Si)
.param Cout=4e-3      ; 4mF output cap
.param Resr_cout=0.004 ; 4mΩ ESR (polymer)
```

---

## Troubleshooting & Convergence

### Common simulation failures and fixes:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `timestep too small` | Resonant transition too fast for timestep | Reduce tstep to 2–5ns; add `ITL4=200` |
| `node floating` | sec_ret not connected to ground | Add `Rsec_ret sec_ret 0 0.001` |
| Vds_Q1 oscillates, never settles | Cclamp too small → large voltage ripple | Increase Cclamp (try 470nF) |
| Vout doesn't regulate | Open-loop fixed duty; use feedback | Add proportional voltage feedback or UIC with IC= on Cout |
| Negative efficiency | Vbus source current direction convention | Ensure `Pin = -i(Vbus) * Vbus` (note negative sign) |
| ZVS not achieved | Lm too large or dead time too short | Reduce Lm by 20% or increase t_dead by 50ns |
| Qa switch stuck | Body diode model wrong polarity | D_qa should be `D_qa sw_node clamp_node` (anode at sw_node) |

### Verify ZVS in simulation:
```
Plot v(sw_node) and v(gate_q1) on same axes.
ZVS achieved when: v(sw_node) reaches ~0V BEFORE v(gate_q1) goes high.
ZVS failed when: v(sw_node) > 50V when v(gate_q1) transitions.
```

---

## Quick Reference: ACF vs Standard Flyback

| Parameter | Standard Flyback (RCD) | Active Clamp Flyback (ACF) |
|-----------|----------------------|--------------------------|
| Primary switch Vds | Vin + VOR + spike (~1.5-2× Vin) | Vin/(1-D) = **clamped** |
| FET voltage rating needed | 800-900V for 265VAC | **600-650V for 265VAC** |
| Leakage energy | Burned in RCD resistor (0.5-2W) | Recycled into Cclamp, used for ZVS |
| Switching loss | Coss × Vds² × fsw (0.5-2W) | **~0 (ZVS)** |
| Extra components | RCD clamp (R+C+D) | Clamp FET Qa + Cclamp film cap |
| Control complexity | Simple PWM | Complementary PWM + dead time |
| Lm | Large (r=0.4) | **Smaller (r=0.6-1.0) for ZVS** |
| Efficiency (60W) | ~88-92% | **92-96%** |
| Light-load efficiency | OK | Needs burst mode (reduced η at <20% load) |
| Cost delta | Baseline | +FET + film cap + driver IC |

---

## Design Review Lessons (from R6-4 Nicola + Standards Compliance Review)

These errors appeared in the R6-4 180W/20V ACF design and were caught in post-design review.
Every ACF design must be checked against each of these before release.

### L1: SR FET voltage — use Vout + Vbus/n, NOT Vout + VOR

```
Correct:  Vds_SR_max = Vout + Vbus_max / n
Wrong:    Vds_SR_max = Vout + VOR = Vout + n×Vout  ← this is primary-referred, not secondary!

At D=0.25, n=6.67, Vbus_max=450V, Vout=20V:
  Correct: 20 + 450/6.67 = 87.5V  (SR FET must survive this)
  Wrong:   20 + 133 = 153V         (3× too pessimistic — will incorrectly over-specify FET)

IPC-9592B derating: V_rated ≥ 1.5 × 87.5V = 131V minimum
→ 80V SR FETs (CSD19506KCS) are UNDERSIZED for any 400V bus flyback with n=6–8.
→ Use 100V minimum, 150V for comfortable margin.
```

### L2: Qa conduction loss — include the full primary current swing, not just reversal tail

```
Wrong:  I_Qa_rms ≈ Imag_rev/sqrt(3) × sqrt(1-D)   [captures reversal only]
Correct: I_Qa_rms = sqrt((1-D)/3 × (Ipk² - Ipk×Imag_rev + Imag_rev²))

The Qa conduction interval starts at +Ipk (load current) and ramps down through
zero to -Imag_rev. Both halves contribute to I²R loss.
Underestimate ratio: ~3-6× depending on Ipk/Imag_rev ratio.
```

### L3: Thermal derating formula — start from breakeven temperature, not from 25°C

```
Wrong:  Iout_max(Ta) = Iout_max_25 × (Tj_max - Ta) / (Tj_max - 25°C)
  (This derates from 25°C even if the hottest component is not the binding constraint)

Correct: Find Ta_breakeven = Tj_max - Pdiss_total × θja
         Iout_max(Ta) = Iout_full_load     for Ta ≤ Ta_breakeven
                      = Iout_full_load × (Tj_max - Ta) / (Tj_max - Ta_breakeven)   for Ta > Ta_breakeven

For ACF 180W/20V: transformer is the hottest component. ΔT_xfmr = P_mag × θ_th.
Ta_breakeven ≈ 40°C (limited by transformer at Ta=40°C → T_core=116°C).
Full current available to 40°C; linear derating above 40°C.
```

### L4: Q1 thermal calculation must include switching loss, not just conduction

```
Wrong (common mistake): Pdiss_Q1 = Pcond + Pgate ≈ 0.13W  (conduction only)
Correct: Pdiss_Q1 = Pcond + Psw_off + Pgate/2
       = 0.104 + 1.400 + 0.022 = 1.526W  (for 180W/100kHz SiC ACF)

The Q1 turn-off switching loss (1.4W) dominates even though ZVS turn-on saves
Coss energy. The heatsink must be sized for the TOTAL loss — the ZVS benefit
is already accounted for in P_coss = 0.

At θja = 15°C/W: ΔTj = 1.526 × 15 = 22.9°C  (not 1.9°C from conduction alone)
```

### L5: Cclamp sizing — use energy balance, not charge ripple formula

See Step 3 above for the corrected formula. The charge-ripple formula
`Cclamp_min = Imag_pk / (ΔV × fsw × (1-D))` is dimensionally inconsistent
and produces values 100× too small. Use the energy-balance derivation.

### L6: ZVS margin — report as energy ratio (e.g., 22.6×), not percentage

"170% ZVS margin" is undefined and internally contradictory with a 22.6×
energy ratio. Use: `ZVS_margin = E_avail/E_req` as a dimensionless ratio.
Minimum acceptable: 1.5×. Typical: 5×–25×.

### L7: Output capacitor voltage rating — must derate vs OVP trip point, not Vout

```
Vout = 20V, OVP trip = 23V (115%), cap must survive 23V.
IPC-9592B requires V_rated ≥ 1.25 × V_max_operational.
V_rated_min = 1.25 × 23V = 28.8V → use 35V-rated caps, NOT 25V.
25V caps at 20V output only give 1.25× at Vout, but only 1.09× at OVP.
```

### L8: PCB creepage — FR4 is Group IIIb, requires 16mm for reinforced 265VAC

```
Standard FR4: CTI ≈ 130–175 = IEC 62368-1 Material Group IIIb
Required creepage (reinforced, 265VAC, PD2): 16mm
Design typically uses 8mm → ONLY COMPLIANT if PCB slot is present.
The PCB slot breaks the surface creepage path entirely; air clearance (4.8mm) applies.
→ PCB slot under transformer is MANDATORY for IEC 62368-1 compliance with FR4.
Document this in the manufacturing spec and safety file.
```

### L9: Bus capacitor discharge hazard must be documented

```
470µF/450V bus capacitor: stored energy = ½ × 470e-6 × 450² = 47.6J
Time to discharge to safe level (<60V) with no bleed resistor: >10 minutes
IEC 62368-1 §5.7.4 requires <1s discharge to <120V for accessible terminals.
Open-frame modules: add safety warning in datasheet and application note.
For end-equipment: mandate active discharge circuit or 100kΩ/2W passive bleed.
```

---

## Integration with Heaviside Agents

**converter-designer:**
- Select ACF when: isolated, 30–150W, target η ≥ 90%
- Run `design_engine.py sweep --topology "Active Clamp Flyback"` for analytical sweep
- Lm is provided by the ZVS sizing formula above, NOT the standard r=0.4 formula
- Dead time is computed by `compute_zvs_dead_time(Llk, Ceq)` in design_engine.py

**magnetics-designer:**
- Call with topology=`flyback` (PyOM has no ACF-flyback topology; use flyback with ACF Lm target)
- Pass `lm_target_H = Lm_ACF` (NOT the standard ripple-ratio Lm)
- Specify `llk_max_fraction = 0.01` (keep leakage ≤ 1% of Lm for good ZVS)
- Explicitly tell PyOM the Lm target: use `desiredMagnetizingInductance` parameter

**control-designer:**
- Use standard Type 2 compensator (DCM) or Type 3 (CCM/DCM boundary)
- RHPZ is 1.5–2.5× higher than standard flyback → crossover can be 10–20% of RHPZ
- Add burst-mode hysteresis for light-load ZVS maintenance

**simulation-engineer:**
- Use template from this file (`netlists/acf-flyback-template.cir`)
- Timestep: ≤ 5ns (resonant transitions during dead time)
- Run time: ≥ 2ms to reach steady state (flyback converters are slow to settle)
- Verify ZVS: check v(sw_node) reaches ~0V before gate_q1 transition

**ray (adversarial reviewer):**
- Key challenges: Is ZVS verified at Vbus_max (worst-case low Imag_rev)?
- Dead time tight enough that Coss fully discharges? (not just partially)
- Is Cclamp rated for AC stress at fsw AND for DC Vclamp?
- Does the SR turn off correctly when secondary current reverses?
- Burst mode transition at light load — does efficiency drop unacceptably?
