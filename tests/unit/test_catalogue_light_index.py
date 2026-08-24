"""The light category index, and the bulk record fetch (ABT #886).

Classifying a BOM row used to cost gigabytes. ``lookup_mpn_category`` walks every
catalogue file until it finds the MPN, and each file's index held the FULL
envelope of every part — so a part number that resolves NOWHERE indexed the whole
catalogue before it could answer "not found". Measured at 10 GB peak locally; on
prod (7.9 GB) it was an outright ``Out of memory: Killed process``.

Two things replace it, and the tests that matter are the ones proving they give
the SAME answers as the heavy path they bypass:

* ``_tas_kind_index`` — mpn -> electrical subtype, no envelopes.
* ``lookup_part_fields_bulk`` — the handful of records a BOM actually needs, in
  one streaming pass instead of a whole-file index.
"""

from __future__ import annotations

import json

import pytest

import heaviside.pipeline.guardrails as g


def _magnetic(mpn: str, subtype: str, *, inductance: float | None = None) -> dict:
    electrical: dict = {"subtype": subtype}
    if inductance is not None:
        electrical["inductance"] = {"nominal": inductance}
    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": mpn,
                "datasheetInfo": {
                    "part": {"partNumber": mpn, "case": "0805"},
                    "electrical": [electrical],
                },
            },
            # A distributor SKU lives here. A regex over the raw line would index
            # it as though it were the manufacturer's part number — which is why
            # the light index uses the same traversal as the heavy one.
            "distributorsInfo": [{"name": "Mouser", "reference": f"994-{mpn}"}],
        }
    }


def _capacitor(mpn: str, farads: float) -> dict:
    return {
        "capacitor": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": mpn,
                "datasheetInfo": {
                    "part": {"partNumber": mpn, "case": "0402", "technology": "ceramic-class-2"},
                    "electrical": {"capacitance": {"nominal": farads}, "ratedVoltage": 16.0},
                },
            }
        }
    }


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    (tmp_path / "magnetics.ndjson").write_text(
        "\n".join(
            json.dumps(e)
            for e in (
                _magnetic("742792040", "chipBead"),
                _magnetic("BLM21AG601SN1", "chipBead"),
                _magnetic("74438356010", "inductor", inductance=1e-6),
            )
        )
    )
    (tmp_path / "capacitors.ndjson").write_text(
        "\n".join(json.dumps(e) for e in (_capacitor("EMK105BJ105KV-F", 1e-6),))
    )
    for cache in (
        g._TAS_INDEX_CACHE,
        g._TAS_BASE_INDEX_CACHE,
        g._TAS_SQUASHED_INDEX_CACHE,
        g._TAS_KIND_INDEX_CACHE,
        g._TAS_KIND_BASE_CACHE,
        g._TAS_KIND_SQUASHED_CACHE,
        g._TAS_LOOKUP_CACHE,
    ):
        cache.clear()
    monkeypatch.setattr(g, "_TAS_DATA_DEFAULT", tmp_path)
    return tmp_path


# ── the light index answers exactly what the heavy one does ──────────────────


def test_light_index_indexes_the_same_mpns_as_the_record_index(catalogue):
    """The property the whole change rests on. Verified over the shipped
    catalogue too — all eight files, ~539 000 parts, key sets identical and zero
    subtype disagreements — but pinned here so a traversal change cannot drift
    the two apart unnoticed."""
    for fname in ("magnetics.ndjson", "capacitors.ndjson"):
        path = catalogue / fname
        heavy = g._tas_file_index(path)
        light = g._tas_kind_index(path)
        assert set(heavy) == set(light), fname
        for mpn in light:
            assert (heavy[mpn].get("subtype") or "") == light[mpn], f"{fname}:{mpn}"


def test_a_distributor_sku_is_not_indexed_as_a_part(catalogue):
    """994-742792040 is Mouser's order code, not the manufacturer's MPN."""
    light = g._tas_kind_index(catalogue / "magnetics.ndjson")
    assert "742792040" in light
    assert "994-742792040" not in light


def test_the_light_index_holds_no_envelopes(catalogue):
    """The entire point: a subtype string per part, not the record."""
    light = g._tas_kind_index(catalogue / "magnetics.ndjson")
    assert all(isinstance(v, str) for v in light.values())
    assert light["blm21ag601sn1"] == "chipBead"
    assert light["74438356010"] == "inductor"


# ── classification, without building a record index ─────────────────────────


def test_classification_does_not_build_the_heavy_index(catalogue):
    assert g.lookup_mpn_category("742792040") == "chipBead"
    assert g.lookup_mpn_category("74438356010") == "magnetic"
    assert g.lookup_mpn_category("EMK105BJ105KV-F") == "capacitor"
    assert not g._TAS_INDEX_CACHE, "classification must not index whole envelopes"


def test_an_unresolvable_mpn_still_answers_none_cheaply(catalogue):
    """The pathological case: it consults every file and finds nothing."""
    assert g.lookup_mpn_category("NOSUCHPART-9999") is None
    assert not g._TAS_INDEX_CACHE


def test_packaging_and_separator_variants_still_classify(catalogue):
    """The tolerances from ABT #873/#878 must survive the index swap."""
    assert g.lookup_mpn_category("BLM21AG601SN1D") == "chipBead"  # taping code
    assert g.lookup_mpn_category("BLM-21A-G601SN1D") == "chipBead"  # separators
    assert g.lookup_mpn_category("EMK105BJ105KVF") == "capacitor"  # separator dropped


def test_only_kinds_narrows_the_search(catalogue):
    assert g.lookup_mpn_category("742792040", only_kinds={"capacitor"}) is None
    assert g.lookup_mpn_category("742792040", only_kinds={"magnetic"}) == "chipBead"


# ── the bulk record fetch ────────────────────────────────────────────────────


def test_bulk_fetch_returns_the_same_record_as_the_indexed_lookup(catalogue):
    bulk = g.lookup_part_fields_bulk({"capacitor": {"emk105bj105kv-f"}})
    indexed = g.lookup_part_fields("EMK105BJ105KV-F", "capacitor")
    assert bulk[("capacitor", "emk105bj105kv-f")]["capacitance"] == indexed["capacitance"]


def test_several_spellings_of_one_part_each_get_the_record(catalogue):
    """A BOM can name the same part four ways. Mapping a catalogue key to a
    single asked MPN let one row claim the record and starved the others, which
    then fell back to building the whole-file index."""
    asked = {"blm21ag601sn1d", "blm-21a-g601sn1d", "blm21/ag6/01sn1d", "blm21ag601sn1"}
    got = g.lookup_part_fields_bulk({"chipBead": asked})
    assert {k[1] for k in got} == asked
    assert {v["mpn"] for v in got.values()} == {"BLM21AG601SN1"}


def test_bulk_fetch_builds_no_index(catalogue):
    g.lookup_part_fields_bulk({"chipBead": {"742792040"}, "capacitor": {"emk105bj105kv-f"}})
    assert not g._TAS_INDEX_CACHE


def test_an_mpn_the_catalogue_lacks_is_simply_absent(catalogue):
    got = g.lookup_part_fields_bulk({"chipBead": {"742792040", "nosuchpart"}})
    assert ("chipBead", "742792040") in got
    assert ("chipBead", "nosuchpart") not in got


def test_empty_request_is_a_no_op(catalogue):
    assert g.lookup_part_fields_bulk({}) == {}
    assert g.lookup_part_fields_bulk({"chipBead": set()}) == {}


# ── G5's existence check (ABT #886, the 59-minute review) ────────────────────


def test_the_hallucination_check_uses_the_light_index(catalogue):
    """G5 asks "does this MPN exist anywhere in TAS?" once per substitute. It
    answered from the RECORD index, which meant loading the full envelope of
    every part in every file the glob reaches — including circuits.ndjson at
    1.2 GB, which contains no parts at all. That exceeded the memory budget, so
    the guard evicted and the next check rebuilt: three correction rounds turned
    a review into 59 minutes of thrash."""
    assert g._mpn_exists_in_tas("742792040", tas_data_dir=catalogue) is True
    assert g._mpn_exists_in_tas("EMK105BJ105KV-F", tas_data_dir=catalogue) is True
    assert g._mpn_exists_in_tas("NOSUCHPART-9999", tas_data_dir=catalogue) is False
    assert not g._TAS_INDEX_CACHE, "an existence check must not index whole envelopes"


def test_the_existence_check_still_spans_every_kind(catalogue, tmp_path):
    """It globs every NDJSON on purpose: a valid substitute of a kind with no
    lookup table of its own must not read as a hallucination."""
    import json as _json

    (catalogue / "igbts.ndjson").write_text(
        _json.dumps(
            {
                "semiconductor": {
                    "igbt": {
                        "manufacturerInfo": {
                            "name": "Infineon",
                            "reference": "IKW40N120H3",
                            "datasheetInfo": {"part": {"partNumber": "IKW40N120H3"}},
                        }
                    }
                }
            }
        )
    )
    g._TAS_KIND_INDEX_CACHE.clear()
    assert g._mpn_exists_in_tas("IKW40N120H3", tas_data_dir=catalogue) is True
