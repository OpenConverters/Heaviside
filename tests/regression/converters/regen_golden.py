"""Regenerate ``golden_baseline.json`` from the current corpus.

Run with ``python -m tests.regression.converters.regen_golden``.

This is a deliberate, manual step: any change to the snapshot is
treated as a reviewed schema / extractor change, not a silent drift.
"""

from __future__ import annotations

import json

from tests.regression.converters.test_converter_corpus import (
    GOLDEN_PATH,
    _build_current_snapshot,
)


def main() -> int:
    snap = _build_current_snapshot()
    GOLDEN_PATH.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN_PATH} ({len(snap)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
