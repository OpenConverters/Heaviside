# How2Power.com -- Best Articles for Power Electronics Agents

Source: How2Power.com Design Guide (1,265+ articles, 355 PDFs downloaded)
Curated: 20 most valuable articles for Heaviside agent system

---

## EMI and Filtering

1. **TI EMI Guide for DC-DC Converters (17-part series)**
   Location: `EMI_Series/` (Parts 2-18)
   Author: Timothy Hegarty (TI)
   Comprehensive coverage: noise propagation, parasitics, radiated/conducted emissions, DM/CM filter design, behavioral modeling, active EMI filters, spread spectrum. The single most complete EMI resource in our collection.

2. **How Active EMI Filter ICs Reduce CM Emissions (4 parts)**
   Files: `How_Active_EMI_Filter_ICs_Reduce_Common-Mode_Emissions_in_Single-_And_Three-Phas*.pdf`
   Covers modeling ferrite and nanocrystalline CM chokes, loop-gain analysis for active CM filters. Essential for high-density filter design.

3. **Free Tool Takes The Drudgery Out Of Designing EMI Filters**
   File: `Free_Tool_Takes_The_Drudgery_Out_Of_Designing_EMI_Filters.pdf`
   Practical EMI filter design workflow with tool support.

## Magnetics Design

4. **Eddy-Current Effects In Magnetic Design (6-part series)**
   Files: `Eddy-Current_Effects_In_Magnetic_Design_Part_*.pdf`
   Author: Dennis Feucht
   Skin effect, proximity effect, conductor geometry, Dowell's formula, winding optimization, bundles. Parallels and extends our Dowell/Ferreira coverage.

5. **Step-By-Step Process Of Designing Flyback-Converter Coupled Inductors (2 parts)**
   Files: `Step-By-Step_Process_Of_Designing_Flyback-Converter_Coupled_Inductors_Part_*.pdf`
   Practical walkthrough: theory of magnetics + worked example. Directly supports magnetics-designer agent.

6. **Leakage Inductance (3-part series)**
   Files: `Leakage_Inductance_Part_*.pdf`
   Friend or foe, overcoming power losses and EMI, improving filtering/efficiency/density. Critical for flyback and forward converter magnetics.

7. **Optimized Magnetics Winding Design (3 parts)**
   Files: `Optimized_Magnetics_Winding_Design_Part_*.pdf`
   Minimized winding resistance for constant layers/strands and constant winding area. A "discovery over fifty years late."

8. **Ferrite Core Magnetics (2 parts)**
   Files: `Ferrite_Core_Magnetics_Part_*.pdf`
   Ungapped and gapped ferrite cores -- practical design methodology.

9. **Area-Product Method Can Simplify Core Selection -- But Beware Of The Constants**
   File: `Area-Product_Method_Can_Simplify_Core_SelectionBut_Beware_Of_The_Constants.pdf`
   Cautions on misuse of area-product constants. Important for magnetics-designer agent accuracy.

## Converter Topologies and Design

10. **Active Clamp Flyback Converters: How They Work And Tips For Design Success**
    File: `Active_Clamp_Flyback_Converters_How_They_Work_And_Tips_For_Design_Success.pdf`
    ACF operation principles and practical design tips. Supports our ACF topology knowledge.

11. **Design Of Dual Active Bridge Converters For Electric Vehicles**
    File: `Design_Of_Dual_Active_Bridge_Converters_For_Electric_Vehicles_Evaluating_Modulat*.pdf`
    DAB modulation schemes and operating frequencies for EV applications.

12. **25-kW SiC-Based Fast DC Charger (7-part series)**
    Location: `DC_Charger_Series/` (Parts 2-8)
    Authors: onsemi team
    Complete EV charger: PFC, DAB DC-DC, control, gate drive, thermal. Real-world high-power SiC design reference.

13. **A More-Efficient Half-Bridge LLC Resonant Converter: Four Methods For Controlling**
    File: `A_More-Efficient_Half-Bridge_LLC_Resonant_Converter_Four_Methods_For_Controlling.pdf`
    LLC control methods comparison. Extends our LLC resonant theory knowledge.

14. **Two-Switch Flybacks Outperform LLC Resonant Converters On Most Parameters**
    File: `Two-Switch_Flybacks_Outperform_LLC_Resonant_Converters_On_Most_Parameters.pdf`
    Provocative comparison -- useful for topology selection agent.

## Control and Compensation

15. **Current-Mode Control Series Compendium**
    File: `Current_Mode_Control_Series_Compendium.pdf`
    Author: Dennis Feucht (Innovatia)
    Comprehensive current-mode control theory. Supports control-designer agent.

16. **Modeling The Effects of Leakage Inductance On Flyback Converters (3 parts)**
    Files: `Modeling_The_Effects_of_Leakage_Inductance_On_Flyback_Converters_Part_*.pdf`
    Converter switching, average model, small-signal model -- complete modeling chain for flyback with leakage.

## Components and Selection

17. **Advantages Of GaN FETs Versus Best Of Breed Silicon MOSFETs**
    File: `Advantages_Of_GaN_FETs_Versus_Best_Of_Breed_Silicon_MOSFETs.pdf`
    Direct GaN vs Si comparison for component-selector agent.

18. **A Methodical Approach To Snubber Design**
    File: `A_Methodical_Approach_To_Snubber_Design.pdf`
    Systematic snubber design methodology. Supports protection-designer agent.

## Thermal and Reliability

19. **How To Thermally Model Magnetic Components**
    File: `How_To_Thermally_Model_Magnetic_Components.pdf`
    Thermal modeling for inductors/transformers. Supports thermal-analyst and magnetics-designer agents.

20. **Open-Source Power Inverter Design (25-part series)**
    Location: `OpenSource_Inverter_Series/` (Parts 1-25)
    Author: Dennis Feucht
    Complete inverter from specs through magnetics, control, protection, testing. A masterclass in power electronics design methodology. Especially valuable: Parts 7/12/15-21 on magnetics, Part 19 on controller, Part 24 on output filter.

---

## Agent Mapping

| Agent | Most Relevant Articles |
|-------|----------------------|
| magnetics-designer | 4, 5, 6, 7, 8, 9, 19 |
| emc-designer | 1, 2, 3 |
| converter-designer | 10, 11, 12, 13, 14, 20 |
| control-designer | 15, 16 |
| component-selector | 17 |
| protection-designer | 18 |
| thermal-analyst | 12 (Part 8), 19 |
| gate-drive-designer | 12 (Part 6) |
| simulation-engineer | 12 (Parts 3, 4), 16 |
