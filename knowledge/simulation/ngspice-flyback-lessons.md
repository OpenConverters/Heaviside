# Ngspice Flyback Simulation — Lessons Learned (updated after 60W design review)

## Critical: Coupled Inductor Polarity in Flyback

In ngspice, coupled inductors using the K statement follow dot convention. For a flyback converter:

### Correct Configuration:
```spice
* Primary: current flows IN through the dot terminal
* in -> Lpri -> sw_node (dot on 'in' side)
Lpri in sw_node {Lp} IC=0

* Secondary: current flows OUT through the dot terminal when primary turns off
* Dot on 'sec_dot', current flows: 0 -> Lsec -> sec_dot -> diode -> out
Lsec 0 sec_dot {Ls} IC=0
Kcoupling Lpri Lsec {coupling}

* Diode from sec_dot to output
Drect sec_dot out Dschottky
```

### Why This Works:
- When the primary switch is ON, current ramps up in Lpri (dot-positive end = 'in')
- When the switch opens, the flux reversal causes sec_dot to go positive
- Current flows: ground -> Lsec(undotted) -> sec_dot(dotted) -> Dout -> output -> load -> ground

### Common Mistakes:
1. **Wrong Lsec node order**: `Lsec sec_dot 0` vs `Lsec 0 sec_dot` — this FLIPS the polarity
2. **Diode direction**: Must be from sec_dot TO output, not from output to sec_dot
3. **Secondary ground reference**: The secondary ground must be the output return, connected to load return

## DCM Flyback Design Equations (Verified by Simulation)

### Primary Inductance for DCM:
```
Lp = Vin_min^2 * D_max^2 / (2 * Pout/eta * fsw)
```

### Peak Current:
```
Ipeak = Vin * D / (Lp * fsw)
```
At nominal Vin=310V, D=0.212, Lp=470uH, fsw=65kHz: Ipeak = 2.15A (confirmed by sim)

### Duty Cycle in DCM:
```
D = sqrt(2 * Pout/eta * Lp * fsw) / Vin
```
At Vin=310V: D = sqrt(2*70.6*470e-6*65e3)/310 = 0.212 (confirmed)

### Output Voltage Accuracy:
Simulation showed 12.20V vs 12V target (+1.6%) — acceptable, closed-loop control handles this.

### Output Ripple:
With 3000uF / 5mohm ESR: 140mV pk-pk (spec was <250mV) — PASS with margin.

## Ngspice Convergence Tips for Flyback

1. **Use `.options method=gear`** — better for switching circuits than default trapezoidal
2. **Set `reltol=0.003`** — slightly relaxed from default 0.001
3. **Add input capacitor** — even with DC source, `Cin in 0 100u IC={Vdc}` helps convergence
4. **RCD clamp model** — use `N=1.5` or higher for clamp diode to soften the turn-on
5. **Step size** — 50ns works well for 65kHz (about 300 points per cycle)
6. **Simulation length** — 5ms (325 cycles) with measurement from 3ms gives good steady-state
7. **IC statements** — set `IC=0` on inductors, `IC={Vout}` on output cap, `IC={Vin}` on input cap

## Switch Voltage Margin Issue

At Vin_max=375V with VOR=100V:
- Vds = Vin + VOR + Vspike = 375 + 100 + ~100 = 575V
- On 600V FET: only 4% margin — NOT ENOUGH

### Solutions (pick one):
1. **Use 650V or 700V MOSFET** — simplest, slight Rds_on penalty
2. **Reduce VOR to 80V** — increases D_max to 0.28, reduces Vds to 375+80+80=535V (12% margin)
3. **Improve RCD clamp** — lower Rclamp to absorb more energy, reduces spike
4. **Use active clamp** — best efficiency but more complex

## Variable Reference in .control Block

Ngspice `.control` blocks cannot use `.param` names directly. Use literal values:
```spice
* WRONG:
let Pout = Vo_avg * Vo_avg / R_load    <-- R_load not accessible

* CORRECT:
let Pout = Vo_avg * Vo_avg / 2.4       <-- use literal value
```

## Design Review Lessons (from 60W Flyback v1 → v2 iteration)

### 1. Always simulate at ALL three corners
- Vin_min (207V for European): worst duty cycle, highest primary RMS
- Vin_nom (293V): typical operating point
- Vin_max (375V): worst switch voltage stress, worst clamp dissipation
- The v1 design only simulated nominal and missed that Vsw exceeded 600V at high line

### 2. RCD Clamp sizing is critical — not an afterthought
- Rclamp too high (15k) = clamp doesn't absorb energy = voltage spike kills FET
- Correct sizing: Rclamp = Vclamp² / Pclamp
- Pclamp = 0.5 * Llk * Ipeak² * fsw * Vclamp / (Vclamp - VOR)
- kc = 1.3-1.5 (clamp voltage / VOR ratio)
- With 2% leakage at 60W: clamp dissipation is 1-2W
- With 5% leakage: clamp dissipation jumps to 5-8W — dominates efficiency

### 3. VOR selection drives everything
- Higher VOR = lower duty cycle = lower primary RMS = better efficiency
- BUT higher VOR = higher Vds_max = need bigger FET
- Sweet spot for universal input 600V FET: VOR = 80-110V
- Sweet spot for 650V FET: VOR = 80-90V
- Sweet spot for 800V FET: VOR = 100-130V

### 4. DCM efficiency is worse than you think at 60W
- RCD clamp loss dominates (5-10W with moderate coupling)
- Secondary diode sees high peak current (np/ns × Ipeak)
- Efficiency realistically 81-85% with standard transformer
- Need k > 0.995 to get >87% — requires careful winding design
- For >90%: consider active clamp flyback or forward topology

### 5. Simulation efficiency is optimistic
- Ideal switch models underestimate switching loss
- Ideal diode models underestimate conduction loss
- No gate drive loss, no controller quiescent power
- No transformer AC resistance or core loss (unless explicitly modeled)
- Real prototype typically 2-5% lower efficiency than simulation
- Always build an honest loss budget alongside the simulation

### 6. Secondary diode thermal is often the bottleneck
- In DCM flyback, Isec_peak = Ipeak × np/ns (can be 10-20A)
- Schottky Vf increases with current — 0.5V at 5A but 0.8V at 17A
- P_diode = Irms × Vf_effective ≈ 2.5-5W for 60W flyback
- Always needs heatsink in TO-220 package
- Consider synchronous rectification for >90% efficiency

### 7. The transformer coupling coefficient k determines the design quality
- k = 0.97 (poor winding): Llk = 3% × Lp, high clamp loss, high voltage spike
- k = 0.985 (typical bobbin-wound): Llk = 1.5% × Lp, moderate clamp loss
- k = 0.995 (sandwich wound, careful): Llk = 0.5% × Lp, low clamp loss
- k = 0.998+ (interleaved planar): Llk < 0.2% × Lp, minimal clamp loss
- Improving k from 0.985 to 0.995 can improve efficiency by 3-5%
