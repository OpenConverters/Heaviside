"""Packaging-suffix aware MPN resolution (ABT #137).

The BOM lists the base orderable MPN (Coilcraft XGL5050-153ME) while the
catalogue stores the reeled variant (XGL5050-153MEC), so exact-match original
resolution missed and the part's real Isat/IR never reached the gates.

The tests that matter most are the NEGATIVE ones: the ticket rejected a naive
prefix/suffix-tolerant match because it false-matched a shorter BOM MPN onto a
longer DIFFERENT part.
"""

from __future__ import annotations

from heaviside.pipeline.mpn_packaging import (
    build_base_index,
    build_squashed_index,
    expand_wanted,
    packaging_base,
    resolve,
    squashed,
)

# ── what a packaging suffix is (and is not) ──────────────────────────────────

def test_coilcraft_reel_letter_is_a_packaging_suffix():
    assert packaging_base("XGL5050-153MEC") == "XGL5050-153ME"
    assert packaging_base("XAL1010-102MED") == "XAL1010-102ME"


def test_murata_taping_letter_is_a_packaging_suffix():
    assert packaging_base("GRM188R61A475KE15D") == "GRM188R61A475KE15"
    assert packaging_base("GCM31CR71H106KA12L") == "GCM31CR71H106KA12"


def test_a_trailing_digit_is_never_stripped():
    """The ticket's trap: Wurth 7440320015 vs 74403200150 differ by a trailing
    DIGIT that changes the part. Stripping it would false-match two real parts."""
    assert packaging_base("74403200150") is None
    assert packaging_base("7440320015") is None


def test_unknown_vendors_are_left_alone():
    assert packaging_base("ACMS-1065-102-T") is None
    assert packaging_base("SOMEPART-123X") is None
    assert packaging_base("") is None


def test_murata_thickness_code_stays_in_the_base():
    """...KE15D and ...KE19L are DIFFERENT parts (15 vs 19 thickness code);
    only the final taping letter goes, so their bases stay distinct."""
    assert packaging_base("GRM188R61A475KE15D") != packaging_base("GRM188R61A475KE19L")


# ── index construction ───────────────────────────────────────────────────────

def test_base_index_maps_the_reeled_variant_onto_its_base():
    index = {"xgl5050-153mec": {"value": "15uH", "manufacturer": "Coilcraft"}}
    assert build_base_index(index) == {"xgl5050-153me": index["xgl5050-153mec"]}


def test_two_packaging_variants_of_one_part_collapse_cleanly():
    part = {"value": "1mH", "voltage": None, "package": "1010", "manufacturer": "Coilcraft"}
    index = {"xal1010-102mec": dict(part), "xal1010-102med": dict(part)}
    assert list(build_base_index(index)) == ["xal1010-102me"]


def test_an_ambiguous_base_is_dropped_never_guessed():
    index = {
        "xal1010-102mec": {"value": "1mH", "manufacturer": "Coilcraft"},
        "xal1010-102med": {"value": "10mH", "manufacturer": "Coilcraft"},   # not the same part
    }
    assert build_base_index(index) == {}


# ── resolution ───────────────────────────────────────────────────────────────

def test_bom_base_resolves_to_the_catalogue_reeled_variant():
    """The lt80603 L2 CRITICAL: the BOM's XGL5050-153ME must find -153MEC."""
    index = {"xgl5050-153mec": {"value": "15uH", "saturation_current": 21.0}}
    base = build_base_index(index)
    assert resolve("XGL5050-153ME", index, base)["saturation_current"] == 21.0


def test_bom_reeled_variant_resolves_to_the_catalogue_base():
    index = {"xgl5050-153me": {"value": "15uH"}}
    assert resolve("XGL5050-153MEC", index, build_base_index(index))["value"] == "15uH"


def test_an_exact_hit_always_wins():
    """Exact-first is what guarantees this can only ADD resolutions: every MPN
    that resolves today keeps resolving to the same record."""
    index = {"xal1010-102me": {"which": "base"}, "xal1010-102mec": {"which": "reeled"}}
    base = build_base_index(index)
    assert resolve("XAL1010-102ME", index, base)["which"] == "base"
    assert resolve("XAL1010-102MEC", index, base)["which"] == "reeled"


def test_a_different_part_still_does_not_resolve():
    index = {"74403200150": {"value": "15uH"}}
    assert resolve("7440320015", index, build_base_index(index)) is None


def test_a_miss_stays_a_miss():
    assert resolve("XGL9999-999ME", {}, {}) is None


def test_expand_wanted_maps_record_bases_back_to_the_wanted_mpn():
    assert expand_wanted({"xgl5050-153mec", "7440320015"}) == {"xgl5050-153me": "xgl5050-153mec"}


# ── Murata chip beads (ABT #873) ─────────────────────────────────────────────

def test_murata_chip_bead_taping_letter_is_a_packaging_suffix():
    """The customer BOM carries the orderable BLM21AG601SN1D; the catalogue
    stores the base BLM21AG601SN1. Verified against Digi-Key, which lists the
    ...D and has no ...SN1 — the trailing letter is the taping code."""
    assert packaging_base("BLM21AG601SN1D") == "BLM21AG601SN1"
    assert packaging_base("BLM31KN601SZ1K") == "BLM31KN601SZ1"
    assert packaging_base("BLM41PG102SN1L") == "BLM41PG102SN1"
    assert packaging_base("BLE18PK100SN1D") == "BLE18PK100SN1"
    assert packaging_base("BLA31AG601SN4D") == "BLA31AG601SN4"


def test_a_bead_base_ending_in_a_letter_is_left_alone():
    """BLF02GD162GNE's trailing E is part of the part number, not a taping code
    — the character before a taping letter is always the internal-code DIGIT."""
    assert packaging_base("BLF02GD162GNE") is None
    assert packaging_base("BLM21AG601SN1") is None   # already the base


def test_bom_bead_with_taping_code_resolves_to_the_catalogued_base():
    index = {"blm21ag601sn1": {"manufacturer": "Murata", "value": "600"}}
    assert resolve("BLM21AG601SN1D", index, build_base_index(index))["value"] == "600"


# ── separator-insensitive resolution (ABT #878) ──────────────────────────────

def test_squashing_removes_only_punctuation():
    assert squashed("BLM-21A-G601SN1D") == "BLM21AG601SN1D"
    assert squashed("BLM21/AG6/01SN1D") == "BLM21AG601SN1D"
    assert squashed("EMK105BJ105KV-F") == "EMK105BJ105KVF"
    assert squashed("") == ""


def test_a_mangled_bom_spelling_resolves_to_the_catalogue_part():
    """One BOM carried the same Murata bead four ways. All must find one part."""
    index = {"blm21ag601sn1": {"manufacturer": "Murata", "value": "600"}}
    base, sq = build_base_index(index), build_squashed_index(index)
    for spelling in (
        "BLM21AG601SN1D",
        "BLM-21A-G601SN1D",
        "BLM21/AG6/01SN1D",
        "BLM21-AG601/SN1D",
    ):
        hit = resolve(spelling, index, base, sq)
        assert hit is not None and hit["value"] == "600", spelling


def test_squashed_resolution_works_without_a_packaging_rule():
    """EMK105BJ105KVF ↔ EMK105BJ105KV-F: no vendor packaging rule applies, the
    two spellings differ only in punctuation."""
    index = {"emk105bj105kv-f": {"value": "1uF"}}
    sq = build_squashed_index(index)
    assert resolve("EMK105BJ105KVF", index, build_base_index(index), sq)["value"] == "1uF"


def test_an_ambiguous_squashed_key_is_dropped_never_guessed():
    index = {
        "abc-123": {"value": "1uF", "manufacturer": "X"},
        "abc123": {"value": "10uF", "manufacturer": "X"},   # not the same part
    }
    assert build_squashed_index(index) == {}


def test_an_exact_hit_still_wins_over_a_squashed_one():
    """Squashing is the LAST resort, so it can only add resolutions."""
    index = {"abc-123": {"which": "punctuated"}, "abc123": {"which": "plain"}}
    sq = build_squashed_index(index)
    assert resolve("ABC-123", index, {}, sq)["which"] == "punctuated"
    assert resolve("ABC123", index, {}, sq)["which"] == "plain"


def test_squashing_never_bridges_two_different_parts():
    """A trailing digit still changes the part, punctuation or not."""
    index = {"74403200150": {"value": "15uH"}}
    sq = build_squashed_index(index)
    assert resolve("7440320015", index, build_base_index(index), sq) is None


def test_resolve_without_a_squashed_index_is_unchanged():
    """The 3-argument call sites keep their exact previous behaviour."""
    index = {"emk105bj105kv-f": {"value": "1uF"}}
    assert resolve("EMK105BJ105KVF", index, build_base_index(index)) is None
