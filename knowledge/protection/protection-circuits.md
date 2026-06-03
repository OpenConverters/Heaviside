# Protection Circuit Design for Switchmode Power Supplies

Source: Billings & Morey, *Switchmode Power Supply Handbook*, 3rd Ed. (2010)

---

## 1. Overcurrent Protection

### 1.1 Overview of Protection Types

| Type | Method | Recovery | Best For |
|---|---|---|---|
| Primary overpower limiting | Monitor primary power; limit if exceeded | Auto | Low-cost flyback supplies |
| Delayed overpower shutdown | Trip after overload persists beyond safe period | Manual reset (power cycle) | Best protection for both supply and load |
| Pulse-by-pulse current limiting | Terminate "on" pulse if instantaneous current exceeds limit | Auto (cycle-by-cycle) | All topologies; essential for transformer protection |
| Constant current limiting | Limit output current to fixed maximum | Auto | Professional-grade multi-output supplies |
| Foldback (reentrant) current limiting | Reduce current as output voltage drops | Auto (but prone to lockout) | Linear regulators only; NOT recommended for SMPS |

### 1.2 Pulse-by-Pulse (Cycle-by-Cycle) Current Limiting

**Principle:** The primary switching current is monitored in real time (via current sense resistor or current transformer). If the instantaneous current exceeds a threshold, the current "on" pulse is immediately terminated.

**Advantages:**
- Fastest protection for switching devices
- Protects against transformer staircase saturation
- Current-mode control provides this inherently
- Protects primary components on every switching cycle

**Implementation:**
- Sense resistor in source/emitter of switching device, or
- Current transformer in series with primary winding
- Comparator terminates PWM pulse when V_sense > V_threshold

**Threshold setting:**
```
V_threshold = I_peak_max * R_sense
```

For current-mode control ICs, typically V_threshold = 1.0 V (e.g., pin 10 of ML4826), so:
```
R_sense = 1.0 V / I_peak_max
```

Add RC noise filter on sense input to prevent false triggering from switching transients.

### 1.3 Constant Current Limiting (Output)

**Principle:** Each output has its own current limit, set independently. When load current reaches the limit, current is held constant and voltage drops.

**Characteristic:** At limit point, output current stays approximately constant as load resistance decreases toward zero (V falls, I stays at I_limit).

**Design notes:**
- Current limit should not exceed load rating by more than 20%
- In multi-output systems, sum of individual current limits may exceed total power rating
- Supplement with primary power limiting to protect primary components
- No lockout issues with nonlinear loads (unlike foldback)
- Recommended for all professional-grade SMPS

**Typical tolerance:** Current limit may vary by +/-20% from nominal as load resistance approaches zero. For precise constant-current, use a dedicated constant-current supply design.

### 1.4 Foldback (Reentrant) Current Limiting

**Principle:** As output voltage drops (due to overload), the current limit point also reduces, decreasing power dissipation at short circuit.

**Critical limitation -- NOT recommended for SMPS:**
- Essential in linear regulators (reduces series transistor dissipation at short circuit)
- Unnecessary in SMPS (switching element dissipation does not depend on output voltage)
- Causes lockout with nonlinear loads (tungsten lamps, motor loads, capacitive loads)
- Causes lockout with cross-connected loads (bipolar supply configurations)

**Lockout mechanism:**
- Nonlinear load line can intersect the reentrant characteristic at multiple stable points
- Supply may settle at a low-voltage operating point and never reach full output
- Lockout can also occur during power-up of capacitive loads

**If foldback must be used, cures include:**
- Modify reentrant characteristic to fall outside nonlinear load line
- Add NTC thermistor in series with load (linearizes load characteristic)
- Use time-delayed shutdown instead (delayed overpower trip)

### 1.5 Hiccup Mode Protection

A variant of delayed overpower shutdown:
- Supply enters current limit
- If overload persists beyond a defined safe period, supply shuts down
- After a cooldown delay, supply attempts restart via soft-start
- If fault persists, cycle repeats (hiccup pattern)
- Limits average power dissipation during sustained faults
- Automatically recovers when fault is removed

---

## 2. Overvoltage Protection (OVP)

### 2.1 Three Types

**Type 1 -- SCR Crowbar:**
- SCR short-circuits the output when OVP threshold is exceeded
- Requires fuse or circuit breaker to clear the fault current
- Fast, definitive protection
- Most common for 5 V logic outputs

**Type 2 -- Voltage Clamping:**
- Shunt zener diode or transistor regulator limits output voltage
- No delay; no reset required
- Relies on source resistance or current limiting to prevent clamp destruction
- Suitable for low-power outputs

**Type 3 -- Voltage Limiting (Converter Shutdown):**
- Independent control loop detects OVP and shuts down or limits the converter
- Preferred for SMPS (fails safe to zero output)
- Uses optocoupler for isolated feedback to primary-side SCR or shutdown circuit
- May be latching (requires power cycle) or self-recovering

### 2.2 SCR Crowbar Design

**Simple crowbar circuit:**
```
Output --> ZD1 --> R4 --> C1 --> SCR gate
                              |
                         SCR (anode to output, cathode to return)
                              |
                         R5 (current limiting)
```

**Operating principle:**
1. Overvoltage causes ZD1 to conduct
2. Current charges gate delay capacitor C1 via R4
3. When C1 reaches ~0.6 V, SCR fires
4. SCR short-circuits output through R5
5. Fuse FS1 clears to disconnect supply

**Design equations:**

Delay time (approximate):
```
t_delay = C1 * 0.6 / I_gate
```
where I_gate = (V_ovp - V_zener - 0.6) / R4

**Fuse selection for crowbar:**
- Fuse I^2t rating must be LESS than SCR I^2t rating
- Use fast-blow semiconductor fuses (lowest I^2t for given current rating)
- SCR must also absorb energy from output capacitor discharge: E_cap = 0.5 * C * V^2
- Total SCR I^2t budget = fuse let-through I^2t + capacitor discharge I^2t
- For stress < 10 ms, thermal conduction from junction is negligible; I^2t is nearly constant

**Improved crowbar (using comparator IC):**
- Replace ZD1 with precision reference (e.g., TL431) + comparator
- Well-defined trip voltage, independent of SCR gate voltage variations
- Delay time well-defined via R4, C1 network

### 2.3 Active Voltage Clamp with Crowbar Backup

For critical applications, combine fast voltage clamping with delayed crowbar:

**Operating sequence:**
1. Overvoltage detected by comparator A1
2. Clamp transistor Q1 conducts immediately, shunting excess current
3. Voltage on Q1 emitter resistor R6 begins charging SCR gate capacitor C1
4. If overvoltage persists, SCR fires after delay period

**Adaptive delay:**
- Small overvoltage stress => long delay (prevents nuisance trips)
- Large overvoltage/high current => short delay (fast protection)
- Very high stress => zener bypass of delay network => near-instantaneous SCR firing

### 2.4 SMPS Voltage Limiting (Type 3)

**Preferred for switchmode supplies because:**
- SMPS inherently "fails safe" to zero output (transformer provides galvanic isolation)
- Most failure modes result in zero output, not overvoltage
- No need for heavy crowbar SCR and fuse

**Implementation:**
- Independent optocoupler triggers primary-side SCR to inhibit converter drive
- OVP control loop must be completely independent of main voltage control loop
- Single component failure must not defeat both control and protection

**Critical design rule:** Never use the same IC for both voltage regulation and OVP. If the IC fails, both functions are lost.

---

## 3. Undervoltage Lockout (UVLO)

### 3.1 Input UVLO

**Purpose:** Prevent converter operation when input voltage is too low for proper switching.

**Why necessary:**
- Low input voltage causes ill-defined drive waveforms
- Power switches may not saturate properly => excessive dissipation => failure
- Control circuits need minimum supply voltage for correct operation

**Implementation:**
- Linked to soft-start circuit
- Converter is inhibited until input voltage exceeds a minimum threshold
- Hysteresis provided by auxiliary winding feedback to prevent squegging (rapid on-off cycling at threshold)
- Separate threshold for turn-on (higher) and turn-off (lower) to provide hysteresis

### 3.2 Output Undervoltage Protection

**Active undervoltage suppression circuit:**
- Stores energy in two capacitors C1 and C2 (charged in parallel from supply)
- During undervoltage transient, switches C1 and C2 in series (providing 2*Vs header voltage)
- Darlington transistor acts as both switch and linear regulator
- Self-tracking -- responds to any deviation below normal (no voltage presetting needed)
- 75% of stored energy is available (compared to ~50% for simple shunt capacitor)
- Response: limits voltage dip to ~30 mV during transient load demands
- Best positioned close to the transient load

---

## 4. Soft-Start Circuits

### 4.1 Purpose

- Reduce inrush current to output capacitors and inductors
- Prevent transformer flux doubling in push-pull/bridge topologies
- Establish correct inductor and capacitor working conditions gradually
- Prevent turn-on voltage overshoot

### 4.2 Operating Principle

1. On power-up, soft-start capacitor C1 is discharged
2. Inverting input of PWM error amplifier is held high, inhibiting output pulses
3. Once input voltage exceeds threshold (via UVLO), inhibit transistor releases C1
4. C1 charges via resistor R3, progressively reducing inhibit voltage
5. PWM pulse width increases gradually from zero to regulated value
6. Voltage control amplifier takes over when output reaches target

**Timing:**
```
t_softstart ~ 5 * R3 * C1
```

### 4.3 Turn-On Overshoot Prevention

**Problem:** During soft-start, control amplifier is saturated high. When output voltage approaches setpoint, the compensation capacitor must slew from saturation to the correct bias point. This delay causes overshoot.

**Solution (ramped reference):**
- Replace fixed reference with an RC-charged reference that ramps up slowly
- Reference voltage increases at a rate slower than soft-start ramp
- Control amplifier establishes correct bias at a lower voltage
- Final approach to target voltage is fully under amplifier control (asymptotic)
- Select C1 for optimum damping: too small = underdamped (overshoot), too large = overdamped (slow)

---

## 5. Inrush Current Limiting

### 5.1 Series Resistors (Low Power)

- Simple fixed resistors in series with AC input
- Compromise between acceptable inrush current and operating loss
- Use surge-rated resistors (wirewound or carbon composition)
- For dual voltage: use two resistors (R1, R2) -- parallel at low-voltage, series at high-voltage

### 5.2 NTC Thermistors (Low-Medium Power)

- High resistance when cold (limits inrush) => low resistance when hot (low operating loss)
- Advantage: much lower steady-state loss than fixed resistors
- Disadvantage: if power cycled rapidly, thermistor is still hot => reduced inrush protection
- Disadvantage: slow warmup may delay full regulation

### 5.3 Active Limiting (Triac/Relay Bypass)

- Start resistor limits inrush during capacitor charging
- After capacitors are fully charged, triac or relay shorts the resistor
- Triac driven from auxiliary winding on main transformer
- Converter soft-start delay ensures capacitors charge before converter starts

**Critical sequencing:**
1. Input capacitors charge through start resistor
2. Wait for capacitors to fully charge (UVLO/delay)
3. Energize bypass triac/relay
4. Begin converter soft-start
5. If converter starts before capacitors are charged, a second inrush occurs when bypass activates

### 5.4 PFC Stage Inrush (High Power)

- Bypass diode D3 across boost inductor L1 diverts inrush current around inductor (prevents saturation)
- NTC thermistors in series with electrolytic capacitors (cold: ~50 ohm, hot: <2 ohm)
- Converter must not start until input capacitors are charged and NTCs have warmed

---

## 6. Thermal Shutdown

**Principle:** Monitor temperature of critical components; shut down converter when temperature exceeds safe limit.

**Implementation approaches:**
- Thermistor (NTC) mounted on heat sink near critical semiconductors, feeding comparator
- Thermal switch (bimetallic) in series with enable/inhibit circuit
- PTC thermistor in series with power path (self-limiting)
- IC-integrated thermal shutdown (most modern PWM controllers include this)

**Design guidelines:**
- Set shutdown threshold below component absolute maximum ratings
- Provide hysteresis (typically 10-20C) to prevent oscillation at threshold
- Allow restart only after sufficient cooldown
- MTBF of SMPS doubles for every 10-15C reduction in operating temperature below 25C

---

## 7. Snubber Design

### 7.1 RC Snubber (Dissipative)

**Purpose:** Provide alternative current path during switching device turn-off, limiting dV/dt and preventing secondary breakdown.

**Circuit:** D1, C1, R1 across switching device (collector-emitter or drain-source).

**Operation:**
1. During device turn-off, primary inductance maintains current flow
2. Current diverts through D1 into C1 (C1 initially discharged)
3. Voltage rise rate on device is controlled by C1 value
4. After flyback period, C1 discharges through R1 during next "on" period

### 7.2 Snubber Capacitor Sizing

**By calculation:**

```
C1 = (Ip * tf) / (2 * 0.7 * Vceo)
```

where:
- Ip = peak primary current at turn-off (A)
- tf = collector/drain current fall time (s)
- Vceo = device voltage rating (V)
- Factor 0.7 = target voltage at 70% of Vceo when current reaches zero
- Factor 2 = accounts for linear current ramp (mean current = Ip/2)

**Rate of voltage rise:**

```
dVc/dt = Ip / (2 * C1)
```

### 7.3 Snubber Resistor Sizing

**Discharge time constraint:**
```
R1 = t_on_min / (2 * C1)
```

where t_on_min = minimum "on" period (at maximum input voltage, minimum load).

The CR time constant should be < 50% of minimum "on" period to ensure C1 is fully discharged before the next turn-off.

### 7.4 Snubber Dissipation

**Turn-off dissipation in switching device:**
```
P_Q1_off = 0.5 * C1 * (0.7 * Vceo)^2 * f
```

**Dissipation in snubber resistor:**
```
P_R1 = 0.5 * C1 * Vc^2 * f
```

where Vc = voltage across C1 before turn-on:
- Complete energy transfer (discontinuous mode): Vc = Vcc (supply voltage)
- Continuous mode: Vc = Vcc + V_reflected_secondary

### 7.5 RCD Snubber (Flyback Voltage Clamp)

**Self-tracking voltage clamp** for flyback converters:

```
Primary winding --> D2 (to C2+) --> C2 --> R1 (to Vcc)
```

- C2 charges to slightly above reflected secondary voltage
- During turn-off, D2 conducts to clamp collector voltage
- Between pulses, R1 discharges C2 back to equilibrium
- Clamp voltage is self-adjusting (tracks operating conditions)
- Do not make clamp voltage too low; need >= 30% above reflected secondary for efficient current transfer through leakage inductance
- If R1 dissipation is too high, replace with energy recovery winding

### 7.6 Weaving Low-Loss Snubber Diode

**A near-lossless snubber technique using a special slow-recovery diode:**

1. Snubber diode D5 is forward-biased during "on" period (via auxiliary supply through R2)
2. At turn-off, primary current diverts into D5 in reverse direction
3. D5's reverse recovery time exceeds device turn-off time
4. Device turns off with only auxiliary supply voltage on collector (~12V)
5. After device is fully off and D5 blocks, collector voltage rises to flyback value
6. Recovered charge stored in auxiliary capacitor C1 for next cycle

**Requirements:**
- Snubber diode reverse recovery time > switching device turn-off time
- Special medium-speed soft-recovery diodes (e.g., Philips BYX 30 SN)
- Auxiliary supply voltage (~12 V typical)

**Advantages:** Near-zero turn-off switching loss; device turns off under ~12 V stress instead of 800+ V.

### 7.7 Rectifier Diode Snubbing

- Output rectifier diodes also need snubbing to reduce:
  - Voltage overshoot from lead/trace inductance
  - Reverse recovery ringing (EMI source)
- Small RC snubber across each rectifier diode
- Empirical optimization in prototype

---

## 8. Crowbar Circuits (Summary)

### 8.1 When to Use

- Linear regulator outputs (series pass transistor can fail short)
- 5 V logic outputs (TTL is vulnerable to overvoltage > 6.25 V)
- When output voltage must be guaranteed not to exceed a hard limit

### 8.2 When NOT to Use (or to use alternatives)

- SMPS outputs generally (fail-safe to zero; use Type 3 voltage limiting instead)
- Multi-output SMPS (crowbar on one output may cause overvoltage on others)

### 8.3 Fuse Selection for Crowbar

- Use fast-blow (semiconductor) fuses for minimum I^2t let-through
- Fuse I^2t < SCR I^2t (including output capacitor discharge energy)
- Slow-blow fuse I^2t can be 100x higher than fast fuse at same current rating
- Consider arc energy in high-voltage or inductive circuits (adds to I^2t)
- External energy sources (from system) may exceed crowbar SCR rating -- system engineer must specify worst-case external fault conditions

### 8.4 Critical Design Rules

1. Protection loop must be independent of main control loop
2. Single component failure must not defeat both regulation and protection
3. SCR must survive until fuse clears
4. Include current limiting resistor in SCR anode to limit capacitor discharge di/dt
5. Test under all failure modes including power-up transients
