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
    expand_wanted,
    packaging_base,
    resolve,
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
