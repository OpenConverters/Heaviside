# Diodes enrichment report (TAS/data/diodes.ndjson)

Date: 2026-06-12. Patch file: `scripts/enrichment/diodes_patch.ndjson` (82 lines, one JSON object per line, mpn-keyed). The data file itself was NOT modified.

## Before

- 7,838 rows total.
- 7,138 rows missing `reverseRecoveryCharge` (Qrr).
- 380 rows missing `forwardVoltage` (Vf).

## Breakdown of the 7,138 missing-Qrr rows

| bucket | rows | disposition |
|---|---|---|
| Synthetic series (fabricated MPNs like `InUF0240N003SOD-3234321`, series `Schottky_25V`, `TVS_5V`, `Zener_12V`, `Ultrafast_200V`, `SiC_Schottky_1200V`, 18 groups x 270) | 4,860 | **Unenrichable** - these are not real parts; no datasheet exists. |
| Real parts of types that do not spec Qrr (schottky 2,046 / sicSchottky 683 / tvs ~1,122 / zener ~1,006 / esd 98, counted after removing synthetic) | 1,595 | **Correctly absent** - skipped per policy (never write 0). |
| Recovery-relevant real parts (ultrafast / standard / rectifier / fast) | 683 | Enrichment target. 21 patched; see below. |

## Patched

- **Vf: 163 rows** via 82 patch lines (some lines cover many rows because rows share an mpn string).
- **Qrr: 21 rows** (every Vishay HEXFRED / FRED Pt Gen 5 row found).

### Sources used (all fetched and read this session)

| series | rows | fields | source |
|---|---|---|---|
| TI BZX84Cx (+-Q1) | 20 | Vf (max 0.9 V @ 10 mA) | ti.com/lit/ds/symlink/bzx84c5v6.pdf (SLVSI95B), bzx84c5v6-q1.pdf (SLVSI70D) |
| TI BZX84WCx (+-Q1) | 20 | Vf (max 0.9 V @ 10 mA) | ti.com/lit/ds/symlink/bzx84wc5v6.pdf (SLVSI96C), bzx84wc5v6-q1.pdf (SLVSI97C) |
| TI BZX884Cx (+-Q1) | 18 | Vf (max 0.9 V @ 10 mA) | ti.com/lit/ds/symlink/bzx884c5v6.pdf (SLVSJ25A), bzx884c5v6-q1.pdf (SLVSJ28A) |
| Vishay VS-VSK.56 modules (D/E/J/C) | 28 | Vf (VFM 1.6 V max @ pi x 60 A) | vishay.com/docs/94625/vs-vsk56.pdf |
| Vishay VS-VSK.71 modules | 28 | Vf (VFM 1.6 V max @ pi x 80 A) | vishay.com/docs/94626/vs-vsk71.pdf |
| Vishay VS-VSK.91 modules | 28 | Vf (VFM 1.55 V max @ pi x 100 A) | vishay.com/docs/94627/vs-vsk91.pdf |
| Vishay VS-HFA HEXFRED (8 parts) | 8 | Vf typ + Qrr typ | vishay.com/docs/94044..94072/vs-hfa*.pdf (per-part docs) |
| Vishay VS-U5FH/U5FX FRED Pt Gen 5 (13 parts) | 13 | Vf typ + Qrr typ | vishay.com/docs/96933..96948/vs-u5f*.pdf (per-part docs) |

Datasheets fetched but yielding documented no-spec results: Vishay UF5400-UF5408 (doc 88756: trr only, no Qrr - skipped, not derived from trr).

## Biggest remaining groups

### Vf (217 rows left)

- 60 rows: Vishay `VS-VSK.166/196/236` (doc 94357) - **ambiguous**: all 60 rows share one mpn string but VFM differs per subseries (1.43 / 1.38 / 1.46 V). Cannot patch one value honestly. Needs per-row keys (the rows also store IF(AV) 165/195/230 A in `reverseVoltage` - see data bugs).
- 28 rows: Vishay `VS-T40HF/T70HF/T85HF/T110HF` (doc 93587) - same ambiguity (VFM 1.30/1.35/1.27/1.35 V per rating, one shared mpn string).
- ~110 rows: Vishay 1-2-row singles (VS-xxNQ/CNQ schottky modules, VS-VSKxS, VS-UFB/UFL, VS-SC, VS-QA, VS-RA...) - each has its own per-part datasheet; enrichable with more budget using the doc-id probe technique (`vishay.com/docs/{id}/{partnumber-lowercase}.pdf`, ids cluster per family).
- 3 rows: VS-U5FH240FA120, VS-U5FH120EA120, VS-U5FX120EA120 - doc ids not found in probed ranges.
- 1 row: TI UC1611-SP (an LCD/display driver IC misfiled as an ESD diode - see data bugs).

### Qrr (662 recovery-relevant rows left)

- 60 + 28: the two ambiguous Vishay module groups above (their docs are standard-recovery; Qrr not specced anyway).
- 84: VS-VSK.56/71/91 modules - verified not specced (standard diodes).
- 47: "Vishay" no-series ultrafast (HER103...HER1602V, 6A05...): attribution doubtful (HER parts are Rectron/Diotec-style numbers; row URLs are datasheetpdf.com search pages). Not patched.
- 30 MACOM MID12X-Sx, 25 Rohm RSR012Exx: obscure/dubious MPNs, only search-page URLs - not patched.
- ~115: bridge rectifiers and standard recovery (GBU/GBJ/KBPC/DF/DB/GBP/MB/1N539x/BY/RL) - datasheets do not spec Qrr (50/60 Hz parts).
- 16 Vishay BYV + 9 BYT: **misattributed** - BYV26/27/29 are NXP/WeEn parts, BYT08P/BYT12P are STMicroelectronics; dataset says Vishay with fake `vishay.com/docs/99999/...` URLs.
- 14 ST STTH (real parts, real st.com URLs): **st.com unreachable from this environment** (TLS/HTTP2 errors on curl and WebFetch timeout). Retry later; their datasheets are the obvious next win.
- Rest: onsemi ES/MUR (trr-only datasheets, expected no Qrr), misc singles.

## Data-quality findings

1. **4,860 synthetic rows** (62% of the file): 18 groups x 270 rows with generated part numbers (`ViSC0028N009SOD-323001`...) and no datasheet URLs. They can never be enriched and pollute any per-manufacturer statistics.
2. **Shared-mpn series rows**: several Vishay module families store the series title as `partNumber` for every row (60x identical mpn for VSK.166/196/236). Rows are only distinguishable by electrical values, and an mpn-keyed patch cannot address them individually.
3. **Field scrambling in Vishay module rows**: `reverseVoltage` holds IF(AV) (165/195/230 A) and `forwardCurrent` holds TC (e.g. 114, 85) for the series-title rows; HFA/U5F singles also show implausible IF/VR combinations (e.g. VS-HFA135NH40PbF, a 400 V part, has reverseVoltage 138).
4. **Placeholder/fake datasheet URLs**: `example.com/datasheets/...`, `datasheetpdf.com/search?q=...`, and `vishay.com/docs/99999/...` (404) appear on thousands of rows.
5. **Misattributed manufacturers**: BYV/BYT series labelled Vishay (actually NXP/WeEn and ST); "Vishay" HER405A/B/C/D-style suffixes don't exist in Vishay's catalogue.
6. **Non-diode part**: UC1611-SP (TI display driver) sits in the diodes file as subType esd.
