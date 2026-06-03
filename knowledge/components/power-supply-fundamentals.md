# Power Supply Design Fundamentals

Source: Patel & Fritz, "Switching Power Supply Design Review -- 60 Watt Flyback Regulator," Unitrode Seminar (TI SLUP072).

---

## 1. Topology Selection Guidelines

### 1.1 When to Use Flyback

The flyback topology is preferred for output power levels below ~150 W due to:

**Cost advantages:**
1. Simple transformer (coupled inductor) design -- single magnetic element, no output inductors
2. Low component count reduces assembly cost
3. Output rectifier reverse-voltage requirements are lower than in topologies with output filter inductors

**Performance advantages:**
1. Good voltage tracking in multi-output supplies (no intervening inductances in secondary circuits)
2. Good transient response (no output inductor charging delay each cycle)

### 1.2 Continuous vs. Discontinuous Current Mode

| Parameter | Discontinuous (DCM) | Continuous (CCM) |
|---|---|---|
| Transformer size | Smaller (lower average energy storage) | Larger |
| Stability | Easier (single-pole plant, no RHPZ) | Harder (two-pole plant, RHPZ present) |
| Peak currents | ~2x higher than CCM | Lower |
| Rectifier reverse recovery | Not critical (zero current at turn-off) | Critical |
| Transistor turn-on | Zero current (low turn-on loss, low RFI) | Nonzero (turn-on losses) |
| Output capacitor ESR | More stringent (high ripple current) | Less stringent |
| Cross-regulation | Worse (high di/dt, leakage effects) | Better |

**Decision:** Use DCM for lower-power designs where peak current stress is manageable and the simpler control loop is advantageous.

---

## 2. Control Strategy Selection

### 2.1 PWM vs. Variable Frequency

PWM control is preferred because:
1. Transformer design can be optimized at a fixed frequency
2. Fixed frequency produces narrow EMI spectrum -- easier to filter
3. Output ripple under light load is minimized
4. PWM ICs provide auxiliary functions (UVLO, soft-start, current limiting)
5. Enables voltage-feed-forward for improved regulation
6. Switching can be synchronized with external circuits (e.g., CRT displays)

### 2.2 Voltage Feed-Forward Technique

The sawtooth ramp slope is made proportional to Vin. As Vin increases, the pulse width decreases to maintain constant volt-seconds to the transformer primary. This provides:

**Open-loop output voltage (DCM flyback with feed-forward):**

```
Vo = K * Vc * sqrt(RL / (2*Lp))
```

where K is the feed-forward constant and Vc is the control voltage.

Key result: **Vo is independent of Vin** (vs. conventional PWM where Vo varies with Vin*D). This:
- Minimizes error amplifier gain requirements for adequate regulation
- Provides excellent audio susceptibility (cycle-by-cycle Vin compensation)
- Allows clamping the maximum volt-second product to optimize transformer size

**Without feed-forward (conventional PWM):**

```
Vo = Vin * D * sqrt(RL / (2*Lp))
```

---

## 3. Closing the Control Loop

### 3.1 Plant Transfer Function (DCM Flyback)

The control-to-output is a single-pole system:

```
G(s) = G_dc / (1 + s / (2*pi*fp))
```

where:
- G_dc = dVo/dVc = K * sqrt(RL / (2*Lp)) (with feed-forward)
- fp = 1 / (2*pi * RL * CE) -- the effective output RC pole

For the 60 W design example: G_dc = 6.1 (15.7 dB) at min load, fp ranges from 14.7 Hz (min load) to 36 Hz (max load).

### 3.2 Error Amplifier Compensation

**Design target:** OdB crossover near fsw/2 with >= 45 degree phase margin.

**Practical constraint:** The error amplifier open-loop bandwidth limits the achievable crossover. In the SLUP072 example, the UC3840 amplifier bandwidth limits crossover to ~15 kHz (vs. the desired 40 kHz = fsw/2).

**Compensation strategy:** Place a zero near the low-frequency output filter pole (20 Hz in the example) to maintain gain below crossover. The resulting loop response is:
- -20 dB/dec rolloff through crossover
- High DC gain for tight regulation
- Adequate phase margin (>45 degrees)

### 3.3 Primary-Side Control

Using an auxiliary transformer winding on the primary side to develop the feedback voltage eliminates the need for an optocoupler. This low-cost approach trades some output coupling accuracy for simplicity. With careful transformer design, regulation of +/-2% is achievable.

---

## 4. Design Checklist and Common Mistakes

### 4.1 Transformer Design (DCM Flyback)

**Primary inductance** determines the energy storage and peak current:

```
Lp = Vin^2 * D^2 * T / (2 * Po)    (simplified, 100% efficiency)
I_pk = sqrt(2 * Po / (Lp * fsw))
```

**Critical check:** Ensure the transformer resets completely each cycle in DCM. The off-time must be long enough for all stored energy to transfer to the secondary. If not, the converter enters CCM, changing the control characteristics.

### 4.2 Output Capacitor ESR

In DCM flyback, the output capacitor ripple is dominated by ESR:

```
V_ripple_ESR = I_sec_peak * ESR
```

The secondary peak current is high in DCM (typically 2x the average). The capacitor must be selected for ESR, not just capacitance. Often the required capacitance for acceptable ESR is much larger than needed for charge storage alone.

### 4.3 Voltage Clamping

Use the PWM IC's voltage clamp feature to limit the maximum control voltage, which limits the maximum volt-second product to the transformer. This allows further optimization of transformer size and prevents saturation under transient conditions.

### 4.4 Gate Drive for Power MOSFETs

A complementary emitter-follower (NPN + diode) provides fast switching of the power MOSFET gate capacitance:
- NPN transistor charges the gate quickly at turn-on (driven from bias supply through series resistor)
- Diode discharges the gate at turn-off (low impedance path)

### 4.5 Dynamic Current Limiting

Sense the switch current and terminate the on-pulse if it exceeds the current limit threshold. This provides cycle-by-cycle overcurrent protection and prevents transformer saturation during transient conditions.

---

## 5. Multi-Output Regulation

### 5.1 Cross-Regulation in Flyback

Loading one output affects regulation of other outputs due to:
- Changes in transformer leakage inductance energy
- Coupling between secondary windings
- ESR voltage drops in shared capacitors

**Mitigation:** Tight magnetic coupling (low leakage), separate output capacitors, and selecting the highest-power output as the regulated output.

### 5.2 Output Voltage Tracking

The flyback topology offers inherently good voltage tracking because there are no output filter inductors to decouple the secondaries. All outputs see the same volt-seconds applied to their respective windings, providing reasonable tracking without post-regulation in many applications.

---

## 6. Practical Design Rules

1. **Always verify current waveforms empirically** -- calculated peak currents assume ideal components. Leakage inductance spikes and ringing add to actual stress.
2. **Include snubber networks** for leakage inductance energy. RCD clamp across the primary switch limits voltage spikes to a safe level.
3. **Size the input capacitor** for the RMS ripple current, not just the DC voltage requirement. In offline flyback supplies, the input capacitor sees significant HF ripple current.
4. **Use voltage feed-forward** whenever the input voltage range exceeds 1.5:1 -- it dramatically reduces the demands on the error amplifier.
5. **Start with the transformer design** -- it determines the operating point, peak currents, and voltage stresses that drive all other component selections.
6. **Prototype and measure** -- the interaction between leakage inductance, ESR, layout parasitics, and component tolerances makes accurate prediction difficult. Bench verification at all operating corners is essential.
