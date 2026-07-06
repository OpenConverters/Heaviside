#!/usr/bin/env python3
"""Seed the durable FAE-findings memory with part-level defects the adversarial
judges surfaced across the review rounds, so the crossref pipeline guards against
re-shipping them. Idempotent (record_findings de-dupes)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heaviside.pipeline.fae_memory import record_findings

FINDINGS = [
    {"substitute": "7440450015", "parameter": "rated current", "severity": "critical",
     "reality": "Wurth WE-LQ 1812 — rated current I_R,40K is only 1.75A; a low-current family that is often below the original's Irms (rating must be >= original)."},
    {"substitute": "74477009", "parameter": "footprint", "severity": "major",
     "reality": "Wurth WE-PD 1280 — 12x12x8.0mm; ~9x the board area (and ~2.6x the height) of a compact 4x4mm inductor and typically far over-dimensioned. Verify footprint/height fit."},
    {"substitute": "784325018", "parameter": "footprint", "severity": "major",
     "reality": "Wurth WE-HCIA 1050 — 10.2x10.2x5.1mm; ~2.6x the area of a 6mm-square original — will not drop into the original land pattern."},
    {"substitute": "885012106032", "parameter": "dielectric / DC-bias", "severity": "major",
     "reality": "Wurth X5R 0603 22uF/10V — a smaller case for a bias-sensitive bulk cap worsens DC-bias capacitance loss vs a larger-case original; X5R (85C) is also below X7R/X7T grades."},
    {"substitute": "885012107005", "parameter": "DC-bias", "severity": "major",
     "reality": "Wurth X5R 0805 22uF/6.3V — aggressive high-cap/low-voltage part with a steep DC-bias curve; effective capacitance near the rating can be a fraction of nameplate."},
]
n = record_findings(FINDINGS, design="review-rounds-1-5")
print(f"recorded {n} finding(s)")
