# MOSFET enrichment report (outputCapacitance / totalGateCharge / gateThresholdVoltage)

Date: 2026-06-12
Source dataset: `TAS/data/mosfets.ndjson` (6,712 rows)
Patch file: `scripts/enrichment/mosfets_patch.ndjson` (505 lines, one per MPN; **not** applied to the dataset)

Every value in the patch was read literally from a manufacturer datasheet PDF fetched during this
session (curl + pdftotext, the table line with min/typ/max and test conditions is quoted in each
patch line's `evidence`). No values were estimated, interpolated, or recalled from memory.

## Missing counts BEFORE (rows where the field is absent, checked in both
`manufacturerInfo.datasheetInfo` and the misplaced root-level `datasheetInfo`)

| field                | missing rows |
|----------------------|--------------|
| outputCapacitance    | 4,828        |
| totalGateCharge      | 2,764        |
| gateThresholdVoltage | 4,562        |

Missing-Coss rows by manufacturer: Infineon 2,609 · Vishay 1,971 · Texas Instruments 187 ·
Vishay Siliconix 16 · STMicroelectronics 11 · Wolfspeed 7 · ON Semi/onsemi 11 · Navitas 6 · others ~10.

## Patched (this session)

| field                | rows patched |
|----------------------|--------------|
| outputCapacitance    | 505          |
| totalGateCharge      | 192          |
| gateThresholdVoltage | 483          |

Only fields that were missing on the target row were emitted (3 Vth keys were dropped from the
AIMBF170R…M1NEW rows because those rows already carry Vth; their `…New` twins get it).

Validation performed: all 505 MPNs resolve to dataset rows, zero duplicates, all C in
[1e-12, 1e-8] F, all Qg in [1e-10, 1e-6] C, Vth emitted as dimensionWithTolerance
(`minimum`/`nominal`/`maximum` exactly as the datasheet's min/typ/max columns; min/max only when
no typ is printed, negative values for P-channel parts as printed).

### Families covered (all per-part or shared-family datasheets, URL recorded per line)

- **Infineon (490 rows)** — the bulk, all from infineon.com direct PDFs:
  - OptiMOS S4/S3/S2 legacy (IPx120N04S4, IPx80N06S4, IPx100N10/12S3, IPB180N04S4 family,
    IPB160N04S4, IPB240N04S4, IPB64N25S3, IPB35N10S3L, IPC/IPD S2/S4/S5 small-can parts)
  - OptiMOS N3 G (IPP/IPB600N25N3, 200N25N3, 320N20N3, 110N20N3/107N20N3, 086N10N3)
  - Automotive OptiMOS (IAUA/IAUC/IAUS/IAUT/IAUZ/IAUMN/IAUCN/IAUTN/IAUZN S5/S6/S7 — ~200 rows)
  - CoolMOS P6/P7 (IPx60R160P6, 380P6, 600P6, IPP60R280P7), CM8 (IPLT60R024–180CM8)
  - CoolSiC M1/M2H 650/750/1200/1700V (IMZA75R…, AIMZA75R…, IMT65R075M2H, AIMBG120R…,
    AIMCQ120R…, AIMZH(N)120R…, AIMBF170R…, AMF12S…, AMM12S… — Qg + Vth + Coss)
  - CoolGaN (IGB/IGC/IGD/IGK/IGL/IGLD/IGLR/IGLT/IGOT/IGT/IGI 600/650/700V, incl. bidirectional
    IGLT…B2 switch-mode Coss only)
  - OptiMOS source-down/SC (ISC019N10NM8SC, ISCH57/75/92N04NM7VSC, IQE012N03LM5CG(SC),
    IQE020N04LM6CG(SC))
  - Legacy IR brand (IRF2804(S), IRF4905L/S, IRFR/U3607, IRFR/U5305, IRFB/S38N20D, IRFB/S4410Z,
    IRFB/S52N15D, IRLR/U024N, AUIRF7640S2/7669L2/7675M2/7769L2, AUIRFP4110/4568, AUIRFS6535,
    AUIRLR3110Z)
- **Navitas (5 rows)**: NV6012C-RA, NV6015C-RA, NV6115-RA, NV6133A-RA, NV6427-RA, NV6428-RA (Coss)
- **Vishay Siliconix (4 rows)**: SISS588DN-T1-GE3/BE3, SISS30LDN-T1-GE3/UE3 (Coss)
- **Wolfspeed (3 rows)**: CAS120M12BM2, CAS300M12BM2, CAS300M17BM2 (Coss)
- **EPC (2 rows)**: EPC2031, EPC2218A (Coss)

## Biggest remaining groups (missing Coss after this patch: 4,323)

| group | rows | why not done / how to proceed |
|---|---|---|
| Vishay, datasheetUrl is a search page (`vishay.com/en/search/?query=…`) | 1,967 | No direct PDF in TAS. Needs a doc-number resolver (vishay.com/docs/<num>/<part>.pdf) or scraping the search page per part. Largest single opportunity. |
| Infineon, direct PDF | 1,889 | Same pipeline as this session works; purely a matter of more passes over `queue.tsv` (lines 430+, one PDF ≈ 1–2 rows). |
| Infineon, product-page URL (`/cms/en/product/...xxx.html`) | 214 | URL is a family landing page; need to resolve the real PDF per part. |
| Texas Instruments (`ti.com/lit/gpn/<part>`) | 162 + 19 no-url | ti.com blocks plain curl (returns HTML interstitial). Needs a browser-grade fetcher. |
| Vishay Siliconix / STM / Wolfspeed / onsemi direct PDFs | ~30 | Small; fetchable next pass. |

## Skipped rows and data-quality findings (surfaced, not papered over)

1. **Wrong datasheetUrl in TAS (do not enrich from these):**
   - `GAN033-650WSP` (labelled Wolfspeed) and `GAN032-650WSB` (labelled Texas Instruments) point to
     the Nexperia GAN041-650WSB datasheet — wrong part *and* wrong manufacturer label (these are
     Nexperia GAN FETs).
   - `STP12N60M2` (ST) points to the AOS AOB7S60 datasheet; `STP6N60M2` points to an AOS TO-220
     *packaging drawing*.
   - `CMF20120D` (Wolfspeed) points to Microchip MSC080SMA120B-SiC datasheet.
2. **Garbled PDFs (no ToUnicode map, pdftotext outputs glyph soup):** Infineon CoolMOS CFD
   2011-era sheets `ipx65r110cfd`, `ipx65r110cfda`, `ipx65r190cfd`, `ipx65r150cfd` (9 rows),
   `irfr9024n` (2 rows). Would need OCR.
3. **Datasheet does not state Coss numerically:** Vishay SI7461DP (graph only) — skipped per the
   no-guessing rule.
4. **Ambiguous multi-die parts skipped:** AMM12S36LB1Z2 (two different electrical tables in one
   PDF), IAUCN04S7L025AH (M1/M2 dies with different Coss), IAUTN08S5N012L (ON+LINFET dual).
5. **Third-party datasheet host:** BSC093N04LSG row ("Unknown" manufacturer) points to an
   ickimg.com copy — not verifiable as the manufacturer's document, skipped.
6. **TI fetch failures:** `ti.com/lit/...` URLs return an HTML interstitial to curl
   (CSD17579Q3 etc.). Toshiba (`toshiba.semicon-storage.com`) likewise. Wolfspeed CCS020M12CM2 /
   CCS050M12CM2 asset URLs return empty bodies.
7. **Dataset dedup artifact:** many Infineon rows exist twice with `…NEW` / `…New` partNumber
   suffixes (same physical part). The patch covers both spellings; worth a dedup pass in TAS.
8. **Mis-shelved manufacturers:** several rows labelled "Texas Instruments"/"Wolfspeed" are
   actually other vendors' parts (see item 1); manufacturer-name hygiene pass recommended.

## Reproduction

Working artifacts (cached PDFs + extracted text): `/tmp/mosfet_ds/` (queue at
`/tmp/mosfet_ds/queue.tsv`, ordered by shared-datasheet leverage; this session consumed lines
1–429). Fetcher: `/tmp/mosfet_ds/fetch_extract.sh <start> <end>`.

---

## Session 2 (2026-06-12, continuation): mechanical pipeline over the full queue + Vishay + TI

Patch grew 505 -> 4,208 lines (3,702 new (mpn,field)-deduped lines). All new values were
machine-extracted from pdftotext of fetched manufacturer PDFs by `/tmp/mosfet_ds/auto_parse.py`,
validated against the 505 human-extracted session-1 rows as ground truth
(**2,963 overlapping field extractions, 0 disagreements**) before being trusted; the parser is
conservative (exactly-one-match + plausibility range + unit required, else skip & log).

### Values filled this session (per source)

| source | Coss | Qg | Vth | other |
|---|---|---|---|---|
| Infineon direct PDFs (queue lines 1-2292) | 1,360 | 171 | 1,466 | |
| Vishay (doc-id via `vishay.com/api/search-predictive/?searchChoice=part&query=<mpn>` -> `docs/{doc_no}/{p1001}.pdf`) | 1,600 | 553 | 931 | SQM40061EL `part.case`=TO-263 |
| Texas Instruments (`ti.com/lit/ds/symlink/<mpn>.pdf` — works with plain curl + browser UA, contrary to session-1 note) | 7 | 0 | 98 | |
| other (Wolfspeed/EPC re-emits) | 6 | 1 | 1 | |

mosfets schema_invalid: **4,315 -> 2,965** (validate_tas). Tests: `tests/regression/tas` +
`test_generated_types.py` = 81 passed.

### Also fixed (separate one-off scripts, datasheet-sourced, tables embedded in the scripts)

- `scripts/repair_mosfet_conflicts.py` — the 6 dual-datasheetInfo conflict rows (IPP60R190P6,
  IPA60R190P6, IPP60R099P7, IPP60R080P7, IPP60R060P7, IPP60R280C6). IPP60R280C6's row was
  contaminated with IPP60R280P7 data (wrong datasheetUrl too) — fully rebuilt from the real
  IPx60R280C6 Rev 2.3 sheet.
- `scripts/repair_mosfet_dictcaps.py` — all 61 dict-shaped capacitance fields ({"typical":x})
  replaced with the datasheet numbers (the old dicts had Ciss==Coss scrape artifacts).

### apply_patches conflicts (14, investigated, all benign — fill-only left them unchanged)

Legacy IRF part numbers (IRF530N, IRF740, IRFB3307, IRFP250N, IRFP260N, IRFP460) exist as both
Infineon-branded and Vishay-branded TAS rows sharing `part.partNumber`; each row keeps the values
from its own manufacturer's datasheet, the cross-matched patch line is rejected by the fill-only
gate. Not data errors.

### Remaining gaps (counted after this session)

- still missing: Coss 1,349 · Qg 1,847 · Vth 1,583 (schema also requires onResistance 743,
  continuousDrainCurrent 429, drainSourceVoltage 276 on other rows — out of scope this session).
- 129 unique Infineon PDFs are garbled (no ToUnicode; 2010-era CoolMOS C6/CFD, some BSC/IPC):
  text extraction impossible, needs OCR or the PDF-rendering Read tool per file. Listed in
  /tmp/auto_all2.log (`grep garbled`).
- 25 Infineon queue URLs are dead (404 even on retry).
- Vishay: 105 MPNs NORESOLVE in the search API (mostly discontinued IRF*/SiH* legacy), 74 resolved
  doc-ids whose PDFs 404 under every filename variant, ~700 Qg cells genuinely ambiguous
  (dual VGS=10/4.5 V rows or dual-die N+P packages — skipped on purpose), P-channel negative Vth
  skipped by the plausibility gate.
- TI: ~45 rows are gate drivers/misfiled (UCC*, LM*), 15 LMG GaN with only 7 Coss extractable
  ("GaN output capacitance" row), 28 symlink 404s.
- Working artifacts for the next pass: /tmp/mosfet_ds/ (queue.tsv, auto_parse.py, vishay_map*.tsv,
  vishay/, ti/, *.txt caches; PDFs were deleted — /tmp filled up).

---

## Session 3 (2026-06-13): Qg batch campaign — Vishay + Infineon + TI

### Summary

| action | count |
|---|---|
| Vishay Qg patches generated | 1,147 |
| Infineon Qg patches generated | 342 |
| TI Qg patches generated | 1 (LMS1225) |
| Total new Qg patches applied | 1,490 |
| Rows changed in mosfets.ndjson | 1,490 |
| schema_invalid before session 3 | 2,946 |
| schema_invalid after session 3 | 2,179 |
| Misfiled gate drivers quarantined | 2 (UCC27511, UCC27321 → mosfets.quarantine_misfiled_drivers.ndjson) |

### Qg parser details

- **Vishay**: `pdftotext -layout` mode used. Two strategies:
  1. Summary page "Qg typ. (nC) at VGS = X V  value" — takes RIGHTMOST number after "(nC)" to avoid grabbing the voltage condition.
  2. Electrical table: Qg row identified by `\bQg\b` (standalone), values extracted from rightmost column area (pos 80+ for same line, pos 95+ for continuation lines).
  - Dual-channel parts (P+N or dual N-Ch die) detected and skipped: 33.
  - Empty PDFs: 108. Not found: 4. No PDF: 122.

- **Infineon**: Both "Total gate charge" (older format) and "Gate charge total" (newer CoolSiC format) detected.
  - IPC bare die parts (IPC302N10N3 etc.) don't include Qg in datasheet per design — 21 parts.
  - Empty PDFs: 15. Not found total: 21.

- **TI**: Only LMS1225 patched (45 nC from family datasheet).
  - All CSD86/87/88 series are **dual-MOSFET power blocks** (half-bridge "NexFET" pairs) — skipped as ambiguous.
  - LMG3x10/3x22/3x26/2100 GaN FETs: no accessible TI datasheet PDF (404 at `ti.com/lit/ds/symlink/`).
  - 2N7002L: datasheet has no Qg specification (small-signal device).

### IPB045N12N3 Qg status

The orchestrator task designated this as highest priority (activates C_boot in buck BOM). **Not patched.**
- Infineon product page (`/cms/en/product/mosfet/IPB045N12N3`) returns 404.
- All known CDN URL variants tried (paths `/24/49/`, `/28/60/`, etc.) — all 404.
- Part appears discontinued with no accessible datasheet. Cannot patch without a datasheet source.
- Ron=6.5 mΩ is confirmed from existing TAS data.

### Quarantined gate drivers

Created `TAS/data/mosfets.quarantine_misfiled_drivers.ndjson` with 2 confirmed TI pure gate drivers:
- **UCC27511**: TI low-side gate driver (single-channel, high-speed)
- **UCC27321**: TI low-side gate driver (single 9-A high-speed)

The originally reported "~45 misfiled drivers" was an approximation. After systematic analysis:
- LMG* (LMG3410, LMG3422, LMG3424, LMG3526, LMG3522, LMG3600, etc.) — TI GaN-FET power stages with integrated drivers, carry VDS/ID/Ron specs → **mosfets** (kept).
- LMS1225 — "MOSFET with Integrated Driver" → **mosfets** (kept).
- UCC27511, UCC27321 — pure gate driver ICs with no VDS/Ron specs → **quarantined**.

Script: `scripts/quarantine_mosfet_drivers_20260613.py`

### Remaining gaps after session 3 (mosfets.ndjson, 6,678 rows)

| field | missing | notes |
|---|---|---|
| totalGateCharge (Qg) | 355 | Vishay 267 (empty/dual/no-PDF) · Infineon 46 (bare die/garbled) · TI 33 (dual pkg/GaN no-DS) · others 9 |
| outputCapacitance (Coss) | 1,315 | unchanged from session 2 |
| gateThresholdVoltage (Vth) | 1,564 | unchanged from session 2 |
| onResistance (Ron) | 748 | unchanged |
| continuousDrainCurrent (ID) | 434 | unchanged |
| drainSourceVoltage (VDS) | 281 | unchanged |

### Patch files

- `scripts/enrichment/mosfets_patch.ndjson` — master patch file (5,697 lines = session 1+2+3)
- `scripts/enrichment/mosfets_patch_20260613b.ndjson` — session-3 batch (1,489 lines, Vishay + Infineon Qg)
