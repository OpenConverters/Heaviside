---
description: Design a Flyback converter from specs, calculate components, generate ngspice netlist
---

# Flyback Converter Design

## When to Use
- Isolated topology needed
- Power level < 100W (most common isolated topology in this range)
- Multiple outputs needed (easy to add extra windings)
- Wide input voltage range (e.g., universal AC input)
- Cost-sensitive isolated design

## Circuit Description
The Flyback converter is the isolated version of the Buck-Boost. Energy is stored in the transformer air gap during t1 (FET on), then transferred to the secondary during t2 (FET off). The "transformer" is really a coupled inductor — it must have an air gap for energy storage, unlike a true transformer.

Components: Q1 (primary FET), D1 (secondary rectifier diode), Lp (primary inductance), Ls (secondary inductance), Ci (input cap), Co (output cap), RCD clamp (snubber for leakage energy).

Key relationship between primary and secondary inductance:
```
Ls = Lp / (np/ns)^2
```

The Flyback has a Right-Half-Plane Zero (RHPZ) that limits control bandwidth:
```
frhpz = Vout * (1 - D)^2 / (2 * pi * D * Ls * Iout)
```

Can operate in CCM, DCM, or Transition (boundary/CrCM) mode.

## Design Procedure

### Step 1: Choose VOR and Turns Ratio (from Maniktala Ch3)
VOR (Reflected Output Voltage) is the secondary voltage reflected to primary:
```
VOR = (Vout + Vf) * np/ns
```
Typically set VOR ~ 100V for universal input (85-265 VAC).
```
np/ns = VOR / (Vout + Vf)
```

### Step 2: Maximum Duty Cycle
```
D_max = VOR / (Vin_min + VOR)
```
Keep D_max < 0.5 for CCM stability.

### Step 3: Choose Current Ripple Ratio
Target r = 0.4 (optimal tradeoff per Maniktala).
```
r = deltaI / Ipri_avg
```

### Step 4: Calculate Primary Inductance
```
Lp = Vin_min * D_max / (r * Ipri_avg * fsw)
```
Where:
```
Ipri_avg = (Vout + Vf) * Iout / Vin_min
```

### Step 5: Ripple Current
```
Iripple = Vin * t1 / Lp
```

### Step 6: Verify CCM/DCM Boundary
CCM guaranteed when Ipri_min > 0. For light loads, converter enters DCM.
At the CCM/DCM boundary, Ipri_min = 0 (Transition Mode).

### Step 7: Component Stresses

**Primary Winding Np:**
```
V_Np_min = -(Vout + Vf) * np/ns = -VOR
V_Np_max = Vin
```

**Secondary Winding Ns:**
```
V_Ns_min = -Vin * ns/np
V_Ns_max = Vout + Vf
```

**FET Q1 (THIS IS THE KEY STRESS):**
```
V_Q1_max = Vin + (Vout + Vf) * np/ns = Vin + VOR
I_Q1_avg = (Ipri_min + Ipri_max)/2 * t1 * fsw
```
Note: Leakage inductance causes additional voltage spikes above V_Q1_max — snubber/clamp is essential.

**Diode D1:**
```
V_D1_min = -Vout - Vin * ns/np
I_D1_avg = Iout
```

**Zener/RCD Clamp:**
```
Vz = VOR + margin
```
Limits the leakage-induced voltage spike on FET drain.

### Step 8: Select Components
- **FET**: V_DS rating >= 1.5 * (Vin_max + VOR); I_D rating > Ipri_max; low Rds_on
- **Diode**: V_R rating >= 1.3 * |V_D1_min|; fast recovery or Schottky for low Vout
- **Coupled Inductor**: Lp value from Step 4; current rating > Ipri_max; low leakage inductance
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating adequate
- **Output cap**: voltage rating >= 1.5 * Vout; ESR low enough for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = Vin * t1 / Lp
Ls = Lp / (np/ns)^2
```

### CCM Timing
```
t1 = (1/fsw) * (Vout + Vf) * np/ns / (Vin + (Vout + Vf) * np/ns)
t2 = 1/fsw - t1
Ipri_min = (Vout + Vf) * Iout / (Vin * fsw * t1) - Iripple/2
Ipri_max = Ipri_min + Iripple
Isec_min = Ipri_min * np/ns
Isec_max = (Ipri_min + Iripple) * np/ns
Iin_avg = (Vout + Vf) * Iout / Vin
```

### DCM Timing
```
t1 = sqrt(2 * Iout * Lp * (Vout + Vf) / (fsw * Vin^2))
t2 = t1 * (Vin + (Vout + Vf) * np/ns) / ((Vout + Vf) * np/ns) - t1
t3 = 1/fsw - t1 - t2
Ipri_min = 0A
Ipri_max = Iripple
Isec_min = 0A
Isec_max = Ipri_max * np/ns
```

### Transition Mode (Boundary/CrCM)
```
Iin_avg = (Vout + Vf) * Iout / Vin
fsw_transition = Vin / (2 * (sqrt(8 * Vin / (np/ns * pi * Vout)) + 1)^2 * Iin * Lp)
```
Variable frequency operation, primary current hits zero each cycle.

### Primary Winding Np
```
V_Np_min = -(Vout + Vf) * np/ns
V_Np_max = Vin
```

### Secondary Winding Ns
```
V_Ns_min = -Vin * ns/np
V_Ns_max = Vout + Vf
```

### FET Q1
```
I_Q1_avg = (Ipri_min + Ipri_max)/2 * t1 * fsw
V_Q1_min = 0V
V_Q1_max = Vin + (Vout + Vf) * np/ns
```

### Diode D1
```
I_D1_avg = (Isec_min + Isec_max)/2 * t2 * fsw
V_D1_min = -Vout - Vin * ns/np
V_D1_max = Vf
```

### Input Capacitor Ci
```
I_Ci_min_t1 = -(Ipri_max) + Iin_avg
I_Ci_max_t1 = -(Ipri_min) + Iin_avg
I_Ci_t2_t3 = Iin_avg
```

### Output Capacitor Co
```
I_Co_min = Isec_min - Iout (during t2)
I_Co_max = Isec_max - Iout (during t2)
I_Co_t1 = -Iout (during t1, diode is off)
```

## DCM vs CCM Selection Guide (from Basso Ch7)

### DCM Advantages:
- Small inductor (transformer)
- No RHPZ in low-frequency range — higher crossover achievable
- First-order system, simple to stabilize even in voltage mode
- Simple low-cost secondary diode (no reverse recovery losses)
- No turn-on switching losses on MOSFET (current starts from zero)
- Valley switching possible in quasi-resonant mode
- Easy synchronous rectification implementation
- No subharmonic oscillations in current mode

### DCM Disadvantages:
- Large AC ripple — higher conduction losses (MOSFET, ESR, copper)
- Bigger hysteresis losses in ferrite (large flux swing)

### CCM Advantages:
- Low AC ripple — less conduction losses
- Lower hysteresis losses (operation on BH minor loops)
- Lower output ripple

### CCM Disadvantages:
- Reverse recovery losses on secondary diode AND primary MOSFET
- Requires fast or Schottky diodes
- Turn-on losses on MOSFET (ID ≠ 0 at turn-on)
- Needs slope compensation if D > 50%
- RHPZ limits bandwidth
- Larger inductance = larger transformer

### Rule of Thumb:
- **< 30W**: DCM is preferred
- **30-100W**: CCM at low line, DCM at high line (best tradeoff)
- **High Vout, low Iout** (e.g., 130V/1A): DCM preferred (slow diodes OK)
- **Low Vout, high Iout** (e.g., 5V/10A): CCM preferred (reduces RMS)

## RCD Clamp Design (from Basso Ch7)

The leakage inductance spike at switch turn-off must be clamped. RCD clamp is most common:

### Clamp voltage selection:
```
Vclamp = kc * VOR
```
Where VOR = (Vout + Vf) * np/ns (reflected output voltage), kc = 1.3-1.5 typical

### Component values:
```
Rclamp = Vclamp^2 / (Pclamp)
Cclamp = Vclamp / (deltaV_clamp * fsw)   [where deltaV_clamp ≈ 10% of Vclamp]
```

### Clamp power dissipation:
```
Pclamp = 0.5 * Llk * Ipeak^2 * fsw * Vclamp / (Vclamp - VOR)
```
Conservative (assumes 100% of MOSFET current at turn-off flows through RCD).

### Diode selection for clamp:
- Must be FAST (not Schottky) — sees full Vclamp + Vin
- Typical: UF4007 or similar ultrafast recovery

## Practical Flyback Design Methodology (from Basso worked examples)

### Step 1: Select operating mode (DCM/CCM)
- < 30W: DCM, 65 kHz typical
- > 60W: CCM at low line

### Step 2: Turns ratio from MOSFET voltage budget
```
N = ns/np >= (Vout + Vf) / (Vds_max/kD - Vin_max/kc)
```
Where kD = 0.85 MOSFET derating, kc = 1.5 clamp overshoot factor
For 600V MOSFET on universal mains: N ≈ 0.16-0.25 (1/N = 4-6)

### Step 3: Primary inductance
**DCM boundary:**
```
Lp = Vbulk_min^2 * D_max / (2 * Pout/eta * fsw)
```

**CCM with ripple ratio:**
```
Lp = Vbulk_min * D_max / (deltaIr * Ipri_avg * fsw)
```
Where deltaIr = 0.5-1.0 for universal mains, 0.8-1.6 for European mains

### Step 4: MOSFET selection
- Voltage: Vds_max = Vin_max + VOR + Vspike (typically 600V for universal)
- RDS(on): Calculate from allowed conduction loss and RMS current
- Gate charge: affects driver losses = Qg * Vgs * fsw

### Step 5: Output capacitor
- In DCM, ESR typically dominates ripple:
```
R_ESR_max ≈ deltaV_ripple / (Ipeak_sec)
```
- Parallel multiple caps to reduce ESR and share RMS current
- Verify RMS current rating > calculated RMS

### Step 6: Sense resistor
```
Rsense = Vlimit / (Ipeak * 1.1)   [10% margin]
```
Where Vlimit = current sense threshold of controller (typically 0.5-1.0V)

## Small-Signal Transfer Functions (from Basso Appendix 2A)

Use buck-boost equations with these modifications for flyback:
- Keep Lp and Ri on primary side
- Reflect C and R to primary: C' = C/N^2, R' = R/N^2
- OR: Calculate Ls = Lp*N^2, reflect Ri to secondary: Ri' = N*Ri

### CCM Current-Mode (most common):
- Single dominant pole + RHPZ + ESR zero
- Subharmonic pole at fsw/2 if D > 0.5 (needs slope compensation)
- RHPZ: f_RHPZ = R'*(1-D)^2 / (2*pi*Lp)

### DCM (any control mode):
- First-order system (single pole)
- No RHPZ in useful frequency range
- Much easier to compensate

### Compensation:
- DCM + current mode: Type 2 (or even Type 1 for PFC-like applications)
- CCM + current mode: Type 2, limited by RHPZ
- CCM + voltage mode: Type 3, limited by RHPZ (avoid if possible)

## Ngspice Netlist Template

```spice
* Flyback Converter
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz
* Turns ratio np/ns={n}

.title Flyback Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param duty={D}
.param Lp={Lp}
.param Ls={Ls}
.param K=0.98
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param Rclamp={Rclamp}
.param Cclamp={Cclamp}
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Input supply
Vin in 0 DC {Vin}

* PWM gate drive
Vpwm gate 0 PULSE(0 10 0 1n 1n {duty/fsw} {1/fsw})

* Primary-side switch (ideal MOSFET model)
.model NMOS NMOS(VTO=2 KP=100 LAMBDA=0)
M1 drain gate 0 0 NMOS W=100u L=1u

* Coupled inductors (transformer with air gap)
* Dot convention: dot on 'in' side of Lp, dot on 'sec_d' side of Ls
* When M1 turns off, current transfers to secondary through D1
Lp in drain {Lp} ic=0
Ls sec_d 0 {Ls} ic=0
K1 Lp Ls {K}

* RCD clamp on primary (absorbs leakage energy)
Dclamp drain clamp_node DFAST
Rclamp clamp_node in {Rclamp}
Cclamp clamp_node in {Cclamp} ic={Vin*0.5}
.model DFAST D(Is=1e-8 Rs=0.01 N=1.0 BV=200 TT=10n)

* Secondary rectifier diode
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 sec_d out DSCHOTTKY

* Output capacitor
C_out out 0 {Cout} ic={Vout*0.9}

* Input capacitor
C_in in 0 {Cin}

* Load
R_load out 0 {Rload}

* Simulation
.tran {tstep} {tstop} 0 {tstep} uic

.control
run

* Steady-state measurements
let tstart = {tstart}
let tstop = {tstop}
meas tran Vout_avg avg v(out) from=tstart to=tstop
meas tran Vout_ripple pp v(out) from=tstart to=tstop
meas tran ILp_avg avg i(Lp) from=tstart to=tstop
meas tran ILp_max max i(Lp) from=tstart to=tstop
meas tran ILp_min min i(Lp) from=tstart to=tstop
meas tran ILs_avg avg i(Ls) from=tstart to=tstop
meas tran ILs_max max i(Ls) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop
meas tran Vdrain_max max v(drain) from=tstart to=tstop

echo "=== Flyback Converter Simulation Results ==="
print Vout_avg Vout_ripple
print ILp_avg ILp_max ILp_min
print ILs_avg ILs_max
print Vdrain_max
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata flyback_results.csv v(out) v(drain) i(Lp) i(Ls) i(Vin)
quit
.endc

.end
```

## Validated 60W Design Example (220VAC to 12V/5A)

This design was validated through simulation and reviewed by Ray (adversarial) and Nicola (quality).

### Specifications
- Input: 220V AC (195-265V RMS) → bridge rectifier → 207-375V DC
- Output: 12V / 5A (60W), ripple < 120mV
- Mode: DCM, 65 kHz, current-mode control (UC3843)

### Final Component Values
| Component | Value | Part | Rationale |
|---|---|---|---|
| VOR | 80V | — | Keeps Vds < 500V with margin for 650V FET |
| np/ns | 6.4 (48:8 turns) | — | VOR/(Vout+Vf) = 80/12.5 |
| Lp | 370-470 uH | EE35/3C95 | DCM boundary at Vin_min full load |
| FET | 650V, Rds=0.45R | SPP11N60C3 | 25% margin at Vin_max |
| Diode | 100V/20A Schottky | MBR20100CT | Vrev = Vin_max/n + Vout = 59V + 12V |
| Cout | 3×1000uF/25V | Low-ESR aluminum | ESR_total ≈ 5 mohm |
| Cin (bulk) | 100uF/400V | Nichicon UVZ | Holdup > 30ms |
| RCD clamp R | 1.8k/10W | Wirewound | Sized for Llk energy at k=0.985 |
| RCD clamp C | 150nF/400V | Film | 10% ripple on clamp voltage |
| RCD clamp D | UF4007 | Ultrafast 1kV | Fast recovery essential |
| Controller | UC3843 | DIP-8 | Current-mode, built-in UVLO/OCP |
| Rsense | 0.39 ohm | Metal film | OCP at 2.56A (1V/0.39R) |
| NTC | 5R cold / 0.5R hot | SL10-5R0 | Inrush limiting |

### Simulation Results (all three corners)
| Parameter | 207V | 293V | 375V | Spec |
|---|---|---|---|---|
| Vout | 11.81V | 12.07V | 12.10V | 12V ±5% |
| Ripple | 42mV | 57mV | 113mV | <120mV |
| Vsw peak | 318V | 406V | 488V | <650V |
| FET margin | 51% | 37% | 25% | >20% |
| Efficiency | 84.4% | 83.3% | 81.8% | >80% |

### Key Lessons from This Design
1. VOR=100V on 600V FET gives only 4% margin at Vin_max — unacceptable
2. RCD clamp Rclamp must be sized from leakage energy, not guessed
3. Transformer coupling k dominates efficiency (k=0.985→84%, k=0.995→87%)
4. Always simulate at Vin_min AND Vin_max, not just nominal
5. Build an honest loss budget — simulation efficiency is optimistic by 2-5%

---

## Synchronous Rectification (SR) for Flyback

### Why SR is Essential
At 5V output, a Schottky diode drops 0.3-0.5V = 6-10% of Vout gone immediately. SR MOSFET: Vds = Iout × Rds_on ≈ 5A × 3mΩ = 15mV. Savings: 1.5-2.5W at 5V/5A.

**Rule: Use SR for any flyback with Vout ≤ 12V or Pout > 30W.**

### SR MOSFET Selection
- Voltage rating: ≥ 1.3 × (Vin_max / n + Vout) where n = np/ns
- Current rating: ≥ 2 × Iout_max (for peak current in DCM)
- Rds_on: Target Vf_schottky / Iout_peak (e.g., 0.4V / 20A = 20mΩ max)
- Qg: Low as possible — SR gate drive losses add up at high fsw
- Body diode: Must handle current during dead time before SR turns on

### SR Timing
- SR turns ON when secondary current starts flowing (detected by Vds going negative)
- SR turns OFF before primary switch turns on (prevent shoot-through)
- Dead time: 50-200ns typical
- In simulation: use voltage-controlled switch with Vds sensing

### Ngspice SR Flyback Template
```spice
* === FLYBACK WITH SYNCHRONOUS RECTIFICATION ===
* Secondary side: SR MOSFET replaces output diode

.param Lp = {actual_Lm}
.param Ls = {actual_Lm / (np_ns)^2}
.param k_coupling = {sqrt(1 - Llk/Lp)}
.param Rds_sr = 0.003          ; SR MOSFET Rds_on
.param Qg_sr = 20e-9           ; SR gate charge
.param dead_time = 100e-9      ; Dead time before primary turn-on

* Coupled inductors (same as standard flyback)
Lpri sw_node 0 {Lp} IC=0
Lsec 0 sec_dot {Ls} IC=0
Kcoupling Lpri Lsec {k_coupling}

* Primary switch (same as standard)
S1 sw_node 0 gate_pri 0 SWIDEAL ON
.model SWIDEAL SW(RON=0.1 ROFF=1e9 VT=0.5 VH=0.1)

* SR MOSFET on secondary (replaces diode)
* Model as ideal switch + Rds_on, controlled by secondary voltage
S_sr sec_dot out_node sr_gate 0 SW_SR ON
.model SW_SR SW(RON={Rds_sr} ROFF=1e9 VT=0.5 VH=0.1)

* SR body diode (conducts during dead time)
D_body sec_dot out_node DBODY
.model DBODY D(IS=1e-12 RS=0.01 N=1.5 BV=100)

* SR gate drive: ON when Vsec_dot > Vout (secondary conducting)
* OFF during dead time before primary turns on
B_sr_gate sr_gate 0 V = {
+  if(V(sec_dot) > V(out_node) + 0.1, 5,
+    if(V(gate_pri) > 2, 0, 
+      if(V(sec_dot) > V(out_node) - 0.1, 5, 0)))}

* Output filter
Cout out_node 0 {Cout_value} IC={Vout}
Rload out_node 0 {Vout/Iout}
```

### SR Loss Calculation
```
P_sr_cond = Irms_sec² × Rds_on(Tj)       ; Typically 0.1-0.5W vs 1-3W for Schottky
P_sr_gate = Qg_sr × Vgs_sr × fsw         ; Gate drive overhead
P_sr_body = I_deadtime × Vf_body × 2 × dead_time × fsw  ; Body diode during dead time
P_sr_total = P_sr_cond + P_sr_gate + P_sr_body

; Compare: P_schottky = Iavg × Vf + Irms² × Rd ≈ 1-3W for typical designs
; SR saves: P_schottky - P_sr_total ≈ 0.8-2.5W (2-5% efficiency gain)
```

---

## Quasi-Resonant (QR) Valley Switching for Flyback

### Why QR Matters
After secondary current reaches zero (DCM), the primary inductance and switch Coss ring. Valley switching turns on the primary at the first voltage valley, reducing:
- Turn-on switching loss: Vds at valley ≈ Vin - VOR (vs Vin + VOR without QR)
- Coss loss: 0.5 × Coss × (Vin-VOR)² vs 0.5 × Coss × (Vin+VOR)²
- At 100kHz, 400V: saves 0.5-2W depending on Coss

**Rule: Use QR for any flyback above 15W where efficiency > 85% is needed.**

### QR Operating Principle
1. Primary switch turns OFF → energy transfers to secondary
2. Secondary current ramps down to zero (DCM boundary)
3. Magnetizing inductance Lm rings with Coss: f_ring = 1/(2π√(Lm×Coss))
4. Vds oscillates: peak = Vin + VOR, valley = Vin - VOR
5. Turn ON primary at first valley → minimum turn-on loss
6. Frequency varies with load (lighter load = longer ring time = lower fsw)

### QR Timing
- t_ring_half = π × √(Lm × Coss)  ; Time from Isec=0 to first valley
- Valley Vds ≈ Vin - (Vout + Vf) × (np/ns)
- If Vin > 2 × VOR: valley voltage is always positive (preferred)
- If Vin < 2 × VOR: valley can go below zero (body diode conducts)

### Ngspice QR Flyback Template
```spice
* === QUASI-RESONANT FLYBACK (Valley Switching) ===
* Variable frequency: turns ON at first Vds valley after Isec = 0

.param Lp = {actual_Lm}
.param Ls = {actual_Lm / (np_ns)^2}
.param k_coupling = {sqrt(1 - Llk/Lp)}
.param Coss = 100e-12          ; Switch output capacitance
.param t_on = {D_nom / fsw_nom}  ; Fixed on-time (constant for QR)

* Coupled inductors
Lpri in sw_node {Lp} IC=0
Lsec 0 sec_dot {Ls} IC=0
Kcoupling Lpri Lsec {k_coupling}

* Switch Coss (models the ringing)
Coss_sw sw_node 0 {Coss}

* Primary switch
S1 sw_node 0 gate_qr 0 SWIDEAL ON
.model SWIDEAL SW(RON=0.1 ROFF=1e9 VT=0.5 VH=0.1)

* QR gate drive: constant on-time, turn ON at valley detection
* Valley = when dV/dt of sw_node crosses zero going positive (minimum)
* Simplified: detect when V(sw_node) < V(in) after secondary current = 0
* In practice, use a timer-based approach:
Vpulse_qr gate_qr 0 PULSE(0 10 0 10n 10n {t_on} {1/fsw_nom})
* NOTE: For true QR, the period must adapt. Simplified fixed-frequency here.
* For variable frequency QR, use behavioral source with valley detection.

* Secondary diode (or SR — see SR template above)
D1 sec_dot out_node DFAST
.model DFAST D(IS=1e-14 RS=0.01 N=1.05 BV=100 TT=20n)

* RCD clamp (still needed but smaller — less leakage energy at lower turn-on Vds)
Dclamp sw_node clamp_node DCLAMP
.model DCLAMP D(IS=1e-14 RS=0.1)
Rclamp clamp_node in {Rclamp_value}
Cclamp clamp_node in {Cclamp_value}

* Output filter
Cout out_node 0 {Cout_value} IC={Vout}
Rload out_node 0 {Vout/Iout}
```

### QR Loss Savings vs Fixed-Frequency
```
; Fixed-frequency turn-on: Vds = Vin + VOR (worst case)
P_turnon_fixed = 0.5 × Coss × (Vin + VOR)² × fsw

; QR valley turn-on: Vds ≈ Vin - VOR (best case first valley)  
P_turnon_qr = 0.5 × Coss × (Vin - VOR)² × fsw

; Savings example at Vin=310V, VOR=80V, Coss=100pF, fsw=100kHz:
; Fixed: 0.5 × 100e-12 × 390² × 100e3 = 0.76W
; QR:    0.5 × 100e-12 × 230² × 100e3 = 0.26W
; Saving: 0.50W (at this single loss source)

; Total QR benefit (including reduced clamp stress):
; Typically 1-3W savings = 2-5% efficiency gain at 30-60W
```

---

## Combined SR + QR Flyback (Best Efficiency)

For maximum efficiency, combine both techniques:

| Technique | Saves | How |
|---|---|---|
| SR alone | 1.5-2.5W | Eliminates diode Vf loss |
| QR alone | 0.5-2W | Reduces turn-on switching + Coss loss |
| SR + QR | 2-4W | Combined savings, 4-8% efficiency gain |

**Expected efficiency with SR + QR at 35W universal input: 85-90%**
(vs 78% with fixed-freq + RCD + Schottky)

### When to Use What
| Power | Target Eff | Recommendation |
|---|---|---|
| < 5W | ≥ 80% | Fixed-freq DCM + Schottky (or integrated IC) |
| 5-15W | ≥ 85% | QR + Schottky (or integrated IC like InnoSwitch) |
| 15-50W | ≥ 88% | QR + SR |
| 50-100W | ≥ 90% | ACF + SR or two-switch forward |
| > 100W | ≥ 93% | LLC + SR |
