# Capacitor enrichment patch report

Date: 2026-06-12. Output: `scripts/enrichment/capacitors_patch.ndjson` (one JSON patch object per line, keyed by `mpn`).

## Inventory before

- TAS rows: 134,956 (`TAS/data/capacitors.ndjson`)
- Rows missing `esr`: 118,681
- Rows missing `rippleCurrent`: 121,512
- Bulk of the missing rows are MLCCs (TDK / Taiyo Yuden / Murata / Samsung) where ESR is frequency-dependent and not a datasheet scalar - skipped per instructions.
- The largest coherent enrichable family: Vishay BCcomponents aluminum electrolytics (MAL2xxxx ordering codes), whose series datasheets carry an 'Electrical Data and Ordering Information' table listing every ordering code next to its rated ripple current and ESR / max impedance.

## What was patched

- **8,811 unique MPNs** patched, covering **35,244 TAS rows** (TAS holds ~4 duplicate rows per MPN).
- `rippleCurrent` (+`rippleCurrentFrequency`): 8,811 MPNs
- `esr` (+`esrFrequency`): 8,068 MPNs (142 RHS and 269 PLT-SI tables carry no ESR/Z column, so those are ripple-only)
- Missing `esr`: 118,681 -> 86,409 rows
- Missing `rippleCurrent`: 121,512 -> 86,268 rows

All values were read from the series datasheet ratings tables (pdftotext -layout + per-series column config). For the radial 1xx 'R'/'C' series the datasheet specifies **max impedance Z at 100 kHz** instead of ESR; that value is stored in `esr` with the evidence string explicitly recording 'datasheet specifies max impedance Z, not ESR'. True-ESR series (snap-in / power ST/SI, axial A-series) use the table's max ESR column with the stated measurement frequency (100 Hz or 120 Hz). Ripple-current measurement frequency/temperature (e.g. 100 Hz / 85 C, 100 kHz / 105 C, 120 Hz / 105 C, 10 kHz / 125 C) is captured per series in `rippleCurrentFrequency` and the evidence text.

## Series covered (58 Vishay datasheets)

| Series | MPNs patched | with esr | Source |
|---|---|---|---|
| 101/102 PHR-ST | 492 | 492 | https://www.vishay.com/docs/28371/101102phrst.pdf |
| 269 PLT-SI | 398 | 0 | https://www.vishay.com/docs/28581/269plt-si.pdf |
| 142 RHS | 345 | 0 | https://www.vishay.com/docs/28402/142rhs.pdf |
| 056/057 PSM-SI | 268 | 268 | https://www.vishay.com/docs/28340/056057psmsi.pdf |
| 136 RVI | 263 | 263 | https://www.vishay.com/docs/28321/136rvi.pdf |
| 058/059 PLL-SI | 240 | 240 | https://www.vishay.com/docs/28342/058059pll-si.pdf |
| 104 PHL-ST | 230 | 230 | https://www.vishay.com/docs/28389/104phlst.pdf |
| 159 PUL-SI | 228 | 228 | https://www.vishay.com/docs/28341/159pulsi.pdf |
| 257 PRM-SI | 226 | 226 | https://www.vishay.com/docs/28460/257prm-si.pdf |
| 157 PUM-SI | 224 | 224 | https://www.vishay.com/docs/28338/157pumsi.pdf |
| 259 PHM-SI | 224 | 224 | https://www.vishay.com/docs/28441/259phmsi.pdf |
| 500 PGP-ST | 214 | 214 | https://www.vishay.com/docs/28390/500pgpst.pdf |
| 021 ASM | 210 | 210 | https://www.vishay.com/docs/28325/021asm.pdf |
| 202 PML-ST | 204 | 204 | https://www.vishay.com/docs/28467/202pmlst.pdf |
| 148 RUS | 197 | 197 | https://www.vishay.com/docs/28315/148rus.pdf |
| 158 PUL-SI | 194 | 194 | https://www.vishay.com/docs/28375/158pulsi.pdf |
| 150 RMI | 187 | 187 | https://www.vishay.com/docs/28323/150rmi.pdf |
| 146 RTI | 183 | 183 | https://www.vishay.com/docs/28401/146rti.pdf |
| 138 AML | 165 | 165 | https://www.vishay.com/docs/28332/138aml.pdf |
| 172 RLX | 165 | 165 | https://www.vishay.com/docs/28499/172rlx.pdf |
| 118 AHT | 157 | 157 | https://www.vishay.com/docs/28334/118aht.pdf |
| 170 RVZ | 151 | 151 | https://www.vishay.com/docs/28462/170rvz.pdf |
| 190 RTL | 146 | 146 | https://www.vishay.com/docs/28465/190rtl.pdf |
| 106 PED-ST | 142 | 142 | https://www.vishay.com/docs/28384/106pedst.pdf |
| 096 PLL-4TSI | 141 | 141 | https://www.vishay.com/docs/28392/096pll4tsi.pdf |
| 156 PUM-SI | 136 | 136 | https://www.vishay.com/docs/28337/156pumsi.pdf |
| 132/133 ALL-DIN | 136 | 136 | https://www.vishay.com/docs/28366/alldin.pdf |
| 184 CPNS | 134 | 134 | https://www.vishay.com/docs/28437/184cpns.pdf |
| 119 AHT-DIN | 133 | 133 | https://www.vishay.com/docs/28335/119ahtdin.pdf |
| 050/052 PED-PW | 127 | 127 | https://www.vishay.com/docs/28345/050-052ped-pw.pdf |
| 140 RTM | 122 | 122 | https://www.vishay.com/docs/28322/140rtm.pdf |
| 090 PUL-SI | 122 | 122 | https://www.vishay.com/docs/28387/090pulsi.pdf |
| 501 PGM-ST | 120 | 120 | https://www.vishay.com/docs/28456/501pgm-st.pdf |
| 030/031 AS | 116 | 116 | https://www.vishay.com/docs/28327/030031as.pdf |
| 193 PUR-SI | 116 | 116 | https://www.vishay.com/docs/28458/193pursi.pdf |
| 150 CRZ | 115 | 115 | https://www.vishay.com/docs/28395/150crz.pdf |
| 246 RTI-V | 108 | 108 | https://www.vishay.com/docs/28422/246rti-v.pdf |
| 048 RML | 105 | 105 | https://www.vishay.com/docs/28318/048rml.pdf |
| 125 ALS | 105 | 105 | https://www.vishay.com/docs/28464/125als.pdf |
| 126 ALX | 101 | 101 | https://www.vishay.com/docs/28570/126alx.pdf |
| 094 PME-SI | 98 | 98 | https://www.vishay.com/docs/28382/094pmesi.pdf |
| 095 PLL-4TSI | 98 | 98 | https://www.vishay.com/docs/28393/095pll4tsi.pdf |
| 152 RMH | 95 | 95 | https://www.vishay.com/docs/28320/152rmh.pdf |
| 041 ASH, 042 ASH, 043 ASH | 95 | 95 | https://www.vishay.com/docs/28329/041042043ash.pdf |
| 198 PHR-SI | 94 | 94 | https://www.vishay.com/docs/28339/198phr.pdf |
| 162/163 PLL-PW | 90 | 90 | https://www.vishay.com/docs/28347/162163pll-pw.pdf |
| 160 RLA | 88 | 88 | https://www.vishay.com/docs/28420/160rla.pdf |
| 140 CRH | 87 | 87 | https://www.vishay.com/docs/28396/140crh.pdf |
| 299 PHL-4TSI | 86 | 86 | https://www.vishay.com/docs/28432/299phl4tsi.pdf |
| 093 PMG-SI | 81 | 81 | https://www.vishay.com/docs/28383/093pmgsi.pdf |
| 051/053 PEC-PW | 80 | 80 | https://www.vishay.com/docs/28346/051053pe.pdf |
| 142 CVZ | 80 | 80 | https://www.vishay.com/docs/28577/142cvz.pdf |
| 146 CTI | 79 | 79 | https://www.vishay.com/docs/28403/146cti.pdf |
| 120 ATC | 75 | 75 | https://www.vishay.com/docs/28336/120atc.pdf |
| 250 RMI-V | 66 | 66 | https://www.vishay.com/docs/28423/250rmi-v.pdf |
| 036 RSP | 57 | 57 | https://www.vishay.com/docs/28312/036rsp.pdf |
| 116 RLL | 45 | 45 | https://www.vishay.com/docs/28316/116rll.pdf |
| 013 RLC | 27 | 27 | https://www.vishay.com/docs/28313/013rlc.pdf |

## Verification

- 3 random MPN expansions per series (58 series, ~170 spot checks) were printed alongside the raw datasheet table row and checked by eye: ordering-code suffix, ripple column and ESR/Z column all decode correctly, including the multi-table docs (056/057, 058/059, 050/052, 051/053, 162/163, 101/102, 132/133), the case-code columns (118 AHT, 119 AHT-DIN, 030/031 AS, 041-043 ASH) and the chip LxWxH series (150 CRZ, 140 CRH, 142 CVZ, 146 CTI, 184 CPNS).
- Cross-check on high-ESR rows: 021 ASM 2.2 uF/63 V row states tan d 0.09 and ESR 65.2 Ohm; tan d/(2 pi 100 Hz C) = 65.1 Ohm - consistent (used only as a sanity check, the stored value is the datasheet's).
- Structural guarantee: a patch line is only emitted when the prefix+suffix MPN reconstructed from the table exists verbatim in TAS; 100% of reconstructed codes matched, zero table lines were rejected by the column-count validator in the final run.

## Remaining (not patched) and why

- **Vishay film (MKP385/385e, MKT1820/1813, KP1830, MKP1848*, MKP383, F339M, MKP386M) and tantalum (TR3/TP3/TL3, 150D/293D/173D/595D/195D/194D/592D, TMCM, wet-tantalum DLA/735D) - ~9,000 rows**: every one of these TAS rows has a placeholder `partNumber` equal to the series name (e.g. all 611 TR3 rows have partNumber 'TR3'), so an MPN-keyed patch cannot address them. Fix the catalog MPNs first.
- **Nichicon (427 rows)**: these rows already carry `rippleCurrent`; they are missing only `esr`, and Nichicon catalogs state tan d, not ESR, for these series (LKS/LKG/UZR checked). Deriving ESR from tan d is a formula, not a datasheet value - skipped per the no-fabrication rule.
- **Wurth (254), Chemi-Con (238), KEMET (209)**: fragmented across many small per-part or per-family PDFs (15-50 rows each); low yield per fetch. Candidates for a follow-up pass.
- **MLCCs (~60k rows)**: ESR intentionally skipped (frequency-dependent); vendors do not state a scalar.

Parser: `scripts/enrichment/vishay_alu_parse.py` (per-series column configs documented inline; PDFs cached in /tmp/vishay_ds during the run).
