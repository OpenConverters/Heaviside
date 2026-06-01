#!/bin/bash
# Run all 10 CRE→CR designs. Restart-safe: skips already-completed designs.
cd /home/alf/OpenConverters/Heaviside
python3 scripts/run_all_cre_cr.py 2>/dev/null
