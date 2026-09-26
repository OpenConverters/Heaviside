"""The catalogue MPN index holds where a part is, not the part.

Holding every part's full envelope (every manufacturer, ~130k capacitors) cost
~8 GB to normalise one large BOM. The index now keeps a lean record and the
line's byte offset; a lookup reads that one line back.
"""

from __future__ import annotations

import json

import pytest

from heaviside.catalogue._reader import CatalogueReadError
from heaviside.pipeline import guardrails as g


def _cap(mpn: str, farads: float) -> dict:
    return {"capacitor": {"manufacturerInfo": {
        "name": "Würth Elektronik", "reference": mpn,
        "datasheetInfo": {"part": {"partNumber": mpn, "case": "0402"},
                          "electrical": {"capacitance": {"nominal": farads}, "ratedVoltage": 16.0}}}}}


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    envs = [_cap("885012205037", 1e-7), _cap("885012206046", 1e-7), _cap("885012208019", 2.2e-5)]
    path = tmp_path / "capacitors.ndjson"
    path.write_text("\n".join(json.dumps(e) for e in envs) + "\n")
    for c in (g._TAS_INDEX_CACHE, g._TAS_LOOKUP_CACHE):
        c.clear()
    g._hydrate_at.cache_clear()
    return tmp_path, envs


def test_the_index_holds_no_envelopes(catalogue) -> None:
    root, _ = catalogue
    index = g._tas_file_index(root / "capacitors.ndjson")
    assert index and all("raw_envelope" not in r for r in index.values())


def test_a_lookup_returns_the_full_record(catalogue) -> None:
    root, envs = catalogue
    rec = g._lookup_tas_part("885012206046", "capacitor", tas_data_dir=root)
    assert rec is not None and rec["mpn"] == "885012206046"
    assert rec["raw_envelope"] == envs[1]
    assert rec["capacitance"] == 1e-7 and rec["voltage"] == 16.0


def test_a_rewritten_file_is_reindexed_not_misread(catalogue) -> None:
    """magnetics.ndjson was rewritten by another session mid-job; the stale
    offsets must never return another part's line."""
    root, envs = catalogue
    path = root / "capacitors.ndjson"
    g._tas_file_index(path)
    # the catalogue is replaced under the running index (a data deploy)
    path.write_text("\n".join(json.dumps(e) for e in reversed(envs)) + "\n")
    rec = g._lookup_tas_part("885012205037", "capacitor", tas_data_dir=root)
    assert rec["mpn"] == "885012205037" and rec["raw_envelope"] == envs[0]


def test_a_stale_offset_in_the_same_mtime_is_still_caught(catalogue, monkeypatch) -> None:
    """Even if the stat check misses a rewrite (same size, coarse mtime), the
    re-read line is checked and the lookup re-indexes instead of misreading."""
    root, envs = catalogue
    path = root / "capacitors.ndjson"
    g._tas_file_index(path)
    stamp = g._TAS_INDEX_STAT[str(path)]
    path.write_text("\n".join(json.dumps(e) for e in reversed(envs)) + "\n")
    monkeypatch.setattr(g, "_file_stamp", lambda p: stamp)  # the rewrite goes unnoticed
    rec = g._lookup_tas_part("885012208019", "capacitor", tas_data_dir=root)
    assert rec["mpn"] == "885012208019" and rec["raw_envelope"] == envs[2]
