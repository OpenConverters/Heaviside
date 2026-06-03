# PSMA Magnetics Specification Checklist
Source: PSMA Magnetics Committee (www.psma.com)

## Purpose
Comprehensive checklist of parameters needed to specify a magnetic device (transformer, inductor, choke) to a manufacturer. Organized in 3 tiers from basic to specialized.

## Tier 1 — Basic (minimum to start design)

### Market & Commercial
- Market: Medical / Commercial / Industrial / Military / Space
- Market locations: USA, Far East, Europe, worldwide
- Target price @ quantity
- Sample quantity, Production quantity, Need dates

### Electrical (per winding)
- Primary rated voltage: min/nom/max, Vpeak/Vrms
- Dielectric withstanding voltage (HiPot): voltage and leakage at altitude
- Operating frequency: min/nom/max, fixed or variable
- Waveform: square wave, push-pull, sinusoidal
- Primary current: min/nom/max, Ipeak/Irms/Idc
- Ripple current: max, Ipeak, Ipk-pk (especially inductors)
- Primary inductance: value ±% @Vrms, Idc bias, frequency
- Secondary voltage(s): min/nom/max, Vpeak/Vrms
- Secondary current(s): min/nom/max, Ipeak/Irms/Idc
- Load impedance (matching transformers)
- Regulation: ±% or volts, no load to full load
- Secondary circuit: half-wave, full-wave, rectifier scheme
- Schematic: with polarity and pinout
- Application: buck, flyback, Cuk, half-bridge, pulse, sense, magamp, inductor

### Mechanical
- Dimensions (L x W x H) or (dia x H)
- Lead breakout
- Terminal type: flex, through-hole, SMT
- Winding order: interleaving, taps, core leg location
- Marking: supplier PN, date code, pin#
- Mounting: SMT, leaded, chassis, clamped
- Wire type: gauge, double-insulated, Litz, flat, bi-filar
- Insulation class: NEMA/UL, MIL-PRF-27 (A, B, F, H)

### Environmental
- Temperature operating: min to max, ambient or coldplate
- Max temperature operating: internal temperature rise

## Tier 2 — Detailed (further specification)

### Electrical
- Transient primary voltage: Vpeak for xx seconds
- Duty cycle range: min to max
- Input volt-seconds: max, especially for flyback
- Primary DCR: ohms
- Secondary DCR: ohms
- Output watt-seconds: max, especially for flyback
- Secondary configuration: tapped, center-tap
- Faraday shielding (internal): foil, braid, effectiveness, termination
- Faraday shielding (external): tape with drain wire for CM capacitance
- Test conditions required: special tests
- Signal integrity: pulse and signal transformers
- Loss: max AC and DC losses, core and copper

### Mechanical
- Weight: grams or pounds
- Case or encapsulant: open, encased, or potted
- Terminal solder: solder type, Lead Free required?
- Winding location: integrated with PCB
- Heat sink or shield: if required
- Gap location(s): if critical
- Core: manufacturer PN, material, or equivalent
- Bobbin: manufacturer PN, material
- Hum Band: low-freq/audio transformers, orthogonal strap

### Environmental
- Temperature storage: min to max
- Cooling method
- Humidity: % RH, condensing or not

### Standards
- MIL-PRF-27 (power transformers), MIL-PRF-21038 (pulse)
- MIL-STD-461/462 (EMI)
- Safety: UL/CSA/IEC

## Tier 3 — Specialized (high reliability)

### Electrical
- Primary leakage inductance: value ±% @Vrms and Hz
- Self-resonant frequency: nominal ±Hz
- Turns ratio(s): nominal ±%, transformer or coupled inductors

### Environmental
- Altitude operating and storage
- Vibration: levels over frequency
- Thermal shock: # cycles, temperature range
- Thermal cycles: # cycles, temperature range
- Derating criteria: voltage, current, % peak flux density

### Testing
- Qualification requirements
- Production screening
- Certificate of Conformance (C of C)

## Sample Build Instructions (Flyback Transformer Example)
The PSMA provides a template for build instructions including:
- Schematic diagram with polarity and pinout
- Winding table: winding name, voltage, pins, turns, wire type, wire gauge, insulation layers
- Mechanical drawing with dimensions
- Notes on stacked outputs, winding techniques

## How Heaviside Uses This Checklist
The magnetics-designer agent should generate a specification that covers AT MINIMUM all Tier 1 parameters. Tier 2 parameters should be included for production designs. Tier 3 for high-reliability applications.

When submitting a magnetic design for manufacturing, the output should map to this checklist structure so the transformer vendor can build it without ambiguity.
