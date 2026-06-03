# RMS Values of Commonly Observed Converter Waveforms (from Erickson Appendix A)

Reference: Erickson & Maksimovic, "Fundamentals of Power Electronics" 3rd ed., Appendix A, pp.1034-1039.

These formulas are used across all converter topologies for component stress calculations, thermal design, and efficiency estimation.

## Basic Waveforms

### DC (constant)

```
I_rms = I
```

### DC plus linear ripple (e.g., inductor current in CCM)

```
I_rms = I * sqrt(1 + (1/3) * (Delta_i / I)^2)
```

where I is the DC component and Delta_i is the peak-to-peak ripple amplitude.

**Design note**: For typical 20-40% ripple ratio (Delta_i/I = 0.2 to 0.4), the RMS is only 0.3-1.3% higher than the DC value. The ripple contribution to RMS is usually negligible for inductor current.

### Square wave

```
I_rms = I_pk
```

### Sine wave

```
I_rms = I_pk / sqrt(2)
```

## Pulsating Waveforms (Switch and Diode Currents)

### Pulsating rectangular waveform (ideal switch current)

Current = I_pk during interval D*T_s, zero otherwise:

```
I_rms = I_pk * sqrt(D)
```

### Pulsating waveform with linear ripple (realistic switch/diode current in CCM)

Current follows a trapezoidal shape of average value I during interval D*T_s, with peak-to-peak ripple Delta_i, zero otherwise:

```
I_rms = I * sqrt(D) * sqrt(1 + (1/3) * (Delta_i / I)^2)
```

**This is the key formula for MOSFET and diode RMS current in CCM converters.**

### Triangular waveform (two slopes, returns to zero)

Current rises from 0 to I_pk during D1*T_s, then falls from I_pk to 0 during D2*T_s:

```
I_rms = I_pk * sqrt((D1 + D2) / 3)
```

### Triangular waveform (single slope, returns to zero)

Current rises from 0 to I_pk during D1*T_s, then drops to 0:

```
I_rms = I_pk * sqrt(D1 / 3)
```

**This is the key formula for DCM inductor/switch current.**

### Triangular waveform with no DC component

Symmetric triangular ripple about zero, peak-to-peak = 2*Delta_i:

```
I_rms = Delta_i / sqrt(3)
```

**This is the capacitor ripple current formula** (for the AC component of capacitor current).

## Special Waveforms

### Center-tapped bridge winding waveform

Current = I_pk during D*T_s, then I_pk/2 during remaining time (as in center-tapped rectifier secondary):

```
I_rms = (1/2) * I_pk * sqrt(1 + D)
```

### General stepped waveform

Current has value I_1 during D1*T_s, value I_2 during D2*T_s, etc.:

```
I_rms = sqrt(D1*I_1^2 + D2*I_2^2 + D3*I_3^2 + ...)
```

## General Piecewise Waveform Method

For a periodic waveform composed of n piecewise segments:

```
I_rms = sqrt(sum_{k=1}^{n} D_k * u_k)
```

where D_k is the duty cycle of segment k, and u_k is the contribution of segment k.

### Segment contributions u_k:

**Constant segment** (current = I_1 throughout):

```
u_k = I_1^2
```

**Triangular segment** (current ramps from 0 to I_1):

```
u_k = (1/3) * I_1^2
```

**Trapezoidal segment** (current ramps from I_1 to I_2):

```
u_k = (1/3) * (I_1^2 + I_1*I_2 + I_2^2)
```

**Sinusoidal segment, half or full period** (peak value I_pk):

```
u_k = (1/2) * I_pk^2
```

**Sinusoidal segment, partial period** (from angle theta_1 to theta_2, in radians):

```
u_k = (1/2) * I_pk^2 * [1 - sin(theta_2 - theta_1) * cos(theta_2 + theta_1) / (theta_2 - theta_1)]
```

## Application to Common Converter Topologies

### Buck converter (CCM)

```
Inductor:   I_L_rms = I_out * sqrt(1 + (1/3)*(Delta_iL/I_out)^2)
MOSFET:     I_Q_rms = I_out * sqrt(D) * sqrt(1 + (1/3)*(Delta_iL/I_out)^2)
Diode:      I_D_rms = I_out * sqrt(1-D) * sqrt(1 + (1/3)*(Delta_iL/I_out)^2)
Cap (AC):   I_C_rms = Delta_iL / sqrt(12)   (triangular ripple approximation)

where Delta_iL = (V_out * (1-D)) / (L * f_sw)
```

### Boost converter (CCM)

```
Inductor:   I_L_rms = I_in = I_out/(1-D) * sqrt(1 + (1/3)*(Delta_iL/I_in)^2)
MOSFET:     I_Q_rms = I_in * sqrt(D) * sqrt(1 + (1/3)*(Delta_iL/I_in)^2)
Diode:      I_D_rms = I_in * sqrt(1-D) * sqrt(1 + (1/3)*(Delta_iL/I_in)^2)
Output Cap: I_C_rms = sqrt(I_D_rms^2 - I_out^2)   (diode pulsating current minus DC)

where Delta_iL = (V_in * D) / (L * f_sw)
```

### Buck-boost / Flyback (CCM)

```
Inductor:   I_L_rms = I_L * sqrt(1 + (1/3)*(Delta_iL/I_L)^2)
                      where I_L = I_out / (1-D)
MOSFET:     I_Q_rms = I_L * sqrt(D) * sqrt(1 + (1/3)*(Delta_iL/I_L)^2)
Diode:      I_D_rms = I_L * sqrt(1-D) * sqrt(1 + (1/3)*(Delta_iL/I_L)^2)
Output Cap: I_C_rms = sqrt(I_D_rms^2 - I_out^2)

where Delta_iL = (V_in * D) / (L * f_sw)
```

### DCM operation (any topology)

In DCM, the inductor/switch current is triangular, rising from 0 to I_pk during D1*T_s, falling to 0 during D2*T_s:

```
I_L_rms = I_pk * sqrt((D1 + D2) / 3)
I_Q_rms = I_pk * sqrt(D1 / 3)
I_D_rms = I_pk * sqrt(D2 / 3)
```

where I_pk is the peak inductor current:

```
I_pk = V_in * D1 / (L * f_sw)      (for buck-boost/flyback)
I_pk = (V_in - V_out) * D1 / (L * f_sw)   (for buck)
```

## Worked Example from Appendix A

A transistor current waveform with a current spike due to diode stored charge:

| Segment | Shape | Duration (us) | D_k | Current (A) | u_k (A^2) |
|---------|-------|---------------|-----|-------------|-----------|
| 1 | Triangular (0 to 20A) | 0.2 | 0.02 | 0->20 | 133 |
| 2 | Constant (20A) | 0.2 | 0.02 | 20 | 400 |
| 3 | Trapezoidal (20A to 2A) | 0.1 | 0.01 | 20->2 | 148 |
| 4 | Constant (2A) | 5.0 | 0.50 | 2 | 4 |
| 5 | Triangular (2A to 0) | 0.2 | 0.02 | 2->0 | 1.3 |
| 6 | Zero | 4.3 | 0.43 | 0 | 0 |

Period = 10 us

```
I_rms = sqrt(0.02*133 + 0.02*400 + 0.01*148 + 0.50*4 + 0.02*1.3 + 0) = 3.76 A
```

Without the current spike (segments 1-3), the RMS would be approximately 2.0 A. The brief current spike significantly increases the RMS value despite its very short duration.

## Quick Reference: Capacitor RMS Current

Output capacitor RMS current is critical for capacitor selection and lifetime. For any topology:

```
I_C_rms = sqrt(I_switch_rms^2 - I_dc^2)   (for the pulsating port)
```

where I_switch_rms is the RMS current of the switch or diode connected to the capacitor, and I_dc is the DC (average) current through the capacitor port.

For the **input** capacitor of a boost or buck-boost:

```
I_Cin_rms = Delta_iL / sqrt(12)   (just the inductor ripple, since inductor is in series with input)
```

For the **output** capacitor of a buck:

```
I_Cout_rms = Delta_iL / sqrt(12)  (same reason -- inductor feeds output directly)
```

For the **output** capacitor of a boost or buck-boost (pulsating diode current):

```
I_Cout_rms = sqrt(I_out^2 * D / (1-D) + (1/12) * Delta_iL^2 * (1-D))   (approximate)
```

Or more simply: I_Cout_rms ~ I_out * sqrt(D/(1-D)) for negligible ripple.
