---
description: Design a Weinberg (flyback current-fed push-pull) converter from specs, calculate components, generate ngspice netlist
---

# Weinberg Converter Design

## When to Use
- Isolated DC-DC conversion needed
- Flyback current-fed push-pull topology desired
- Energy transferred both when FETs are conducting and when not conducting
- Two magnetic components required: push-pull transformer + flyback inductor
- Only CCM equations available (from TI handbook)

## Circuit Description
The Weinberg converter is a flyback current-fed push-pull topology. Energy is transferred to the output BOTH when the FETs are conducting (through the push-pull transformer) and when the FETs are not conducting (through the flyback inductor). The two FETs switch alternately at 50% duty per switch with overlap controlled by t1.

Components: Q1 and Q2 (push-pull FETs), D1 and D2 (output rectifier diodes), D3 (flyback demagnetization diode), T1 (push-pull transformer with Np1/Np2/Ns), L_flyback (flyback inductor with Np/Ns), Ci (input cap), Co (output cap).

## Design Procedure

### Step 1: Reflected Inductances
```
Ls_pushpull = Lp_pushpull / (np/ns)^2
Ls_flyback = Lp_flyback / (np/ns)^2
```
Where np/ns is the turns ratio of the respective magnetic component.

### Step 2: Current Ripple
```
Iripple = (Vin - (Vout + Vf) * np/ns) * t1 / Lp_flyback
```

### Step 3: Magnetization Current
```
Imag = (Vout + Vf) * np/ns * t1 / Lp_pushpull
```

### Step 4: Component Stresses

**FET Q1/Q2:**
```
VQ_min = 0V
VQ_otheroff = Vin
VQ_otheron = 2 * np/ns * (Vout + Vf)
VQ_max = Vin + 2 * np/ns * (Vout + Vf)
```
Note: VQ_max represents the HIGHEST switch stress of any common topology. Size FETs accordingly.

**Flyback Demagnetization Diode D3:**
```
ID3_t1 = 0A
ID3_avg = INs_avg
VD3_min = VNs_min - Vout
VD3_max = Vf
```

### Step 5: Select Components
- **FETs**: V_DS rating >= 1.5 * VQ_max; be aware of very high switch stress
- **Push-Pull Transformer**: rated for magnetization current Imag and power throughput
- **Flyback Inductor**: L value for desired ripple; current rating > Ipri_max; low DCR
- **Diode D3**: V_R rating >= 1.3 * |VD3_min|; Schottky preferred for low Vf
- **Input cap**: voltage rating >= 1.5 * Vin_max; ripple current rating adequate
- **Output cap**: voltage rating >= 1.5 * Vout; ESR low enough for ripple spec

## Complete Equations (from TI Power Topologies Handbook)

### General
```
Iripple = (Vin - (Vout + Vf) * np/ns) * t1 / Lp_flyback
Imag = (Vout + Vf) * np/ns * t1 / Lp_pushpull
```

### CCM Timing
```
t1 = 1/(2*fsw) * (Vout + Vf) / (Vin * ns/np)
t2 = 1/(2*fsw) - t1
D = t1 * fsw
Iin_pulse_avg = (Vout + Vf) * Iout / (Vin * 2 * D)
Ipri_min = Iin_pulse_avg - Iripple/2
Ipri_max = Iin_pulse_avg + Iripple/2
```

### Primary Flyback Inductor Np
```
INp_avg = (Ipri_min + Ipri_max)/2 * t1 * 2 * fsw
VNp_min = -(Vout + Vf) * np/ns
VNp_max = Vin - (Vout + Vf) * np/ns
```

### Secondary Flyback Inductor Ns
```
INs_min = Ipri_min * np/ns
INs_max = Ipri_max * np/ns
INs_avg = (INs_min + INs_max)/2 * t2 * 2 * fsw
VNs_min = -VNp_max * ns/np
VNs_max = -VNp_min * ns/np
```

### Push-Pull Primary Np1/Np2
```
INp1_avg = (Ipri_min + Ipri_max)/2 * t1 * fsw
VNp1_min = -(Vout + Vf) * np/ns
VNp1_max = (Vout + Vf) * np/ns
```

### FET Q1/Q2
```
VQ_min = 0V
VQ_otheroff = Vin
VQ_otheron = 2 * np/ns * (Vout + Vf)
VQ_max = Vin + 2 * np/ns * (Vout + Vf)
```

### Flyback Demagnetization Diode D3
```
ID3_t1 = 0A
ID3_avg = INs_avg
VD3_min = VNs_min - Vout
VD3_max = Vf
```

## Ngspice Netlist Template

```spice
* Weinberg Converter (Flyback Current-Fed Push-Pull)
* Vin={Vin}V, Vout={Vout}V, Iout={Iout}A, fsw={fsw}Hz

.title Weinberg Converter

* Parameters
.param Vin={Vin}
.param fsw={fsw}
.param np_ns={np/ns}
.param Lp_flyback={Lp_flyback}
.param Lp_pushpull={Lp_pushpull}
.param Cin={Cin}
.param Cout={Cout}
.param Rload={Vout/Iout}
.param tstep={1/(fsw*200)}
.param tstop={50/fsw}
.param tstart={20/fsw}

* Flyback inductor coupling coefficient
.param K_flyback=0.998
* Push-pull transformer coupling coefficient
.param K_pushpull=0.998

* Input supply
Vin in 0 DC {Vin}

* PWM gate drives (alternating, 50% per switch with overlap)
Vpwm1 gate1 0 PULSE(0 10 0 1n 1n {0.5/fsw} {1/fsw})
Vpwm2 gate2 0 PULSE(0 10 {0.5/fsw} 1n 1n {0.5/fsw} {1/fsw})

* Push-pull FETs (ideal switch model)
.model SW1 SW(Ron=0.01 Roff=1Meg Vt=2.5 Vh=0.5)
S1 sw1 0 gate1 0 SW1
S2 sw2 0 gate2 0 SW1

* Flyback inductor (coupled)
* Primary: from input through to push-pull center tap
Lp_fly in ct {Lp_flyback} ic=0
Ls_fly flyback_sec 0 {Lp_flyback/(np_ns*np_ns)} ic=0
K1 Lp_fly Ls_fly {K_flyback}

* Push-pull transformer (coupled, center-tapped primary)
Lp1 ct sw1 {Lp_pushpull} ic=0
Lp2 ct sw2 {Lp_pushpull} ic=0
Ls1 pp_sec1 pp_ct {Lp_pushpull/(np_ns*np_ns)} ic=0
K2 Lp1 Ls1 {K_pushpull}
* Note: Lp2-Ls1 coupling handled by symmetric construction
* For full model, add second secondary winding:
* Ls2 pp_sec2 pp_ct {Lp_pushpull/(np_ns*np_ns)} ic=0
* K3 Lp2 Ls2 {K_pushpull}

* Output rectifier diodes (push-pull secondary)
.model DSCHOTTKY D(Is=1e-5 Rs=0.03 N=1.05 BV=100)
D1 pp_sec1 out DSCHOTTKY
D2 pp_sec2 out DSCHOTTKY

* Push-pull secondary center tap to output return
Rpp_ct pp_ct 0 0.001

* Flyback demagnetization diode D3
D3 flyback_sec out DSCHOTTKY

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
meas tran ILfly_avg avg i(Lp_fly) from=tstart to=tstop
meas tran ILfly_max max i(Lp_fly) from=tstart to=tstop
meas tran ILfly_min min i(Lp_fly) from=tstart to=tstop
meas tran Iin_avg avg i(Vin) from=tstart to=tstop

echo "=== Weinberg Converter Simulation Results ==="
print Vout_avg Vout_ripple
print ILfly_avg ILfly_max ILfly_min
let Pin = -Iin_avg * {Vin}
let Pout = Vout_avg * Vout_avg / {Rload}
let eff = Pout / Pin * 100
print Pin Pout eff

wrdata weinberg_results.csv v(out) v(ct) v(sw1) v(sw2) i(Lp_fly) i(Vin)
quit
.endc

.end
```
