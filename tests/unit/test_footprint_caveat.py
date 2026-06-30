"""One-EIA-size-larger substitutions are offered as PARTIAL with a verify-fit
caveat (and the old→new size), instead of being dropped as oversize."""
import pytest

from heaviside.pipeline import crossref_pipeline as CP
from heaviside.pipeline.crossref import CrossRefState

_OVERSIZE_BASE = CP._OVERSIZE_BASE


def _dims(case):
    d = CP._eia_dims_from_case(case)
    return (d[0], d[1], None)


# --- penalty tiers --------------------------------------------------------

def test_one_size_up_penalty_below_oversize_base():
    # 0402 -> 0603: penalised but NOT dropped (below the oversize floor)
    pen = CP._footprint_penalty(_dims("0402"), _dims("0603"))
    assert 0 < pen < _OVERSIZE_BASE


def test_one_size_up_beats_near_value_fit():
    # the one-size-up penalty must stay below the value-match weight so an exact
    # value one size up outranks a near-value fitting part
    pen = CP._footprint_penalty(_dims("0402"), _dims("0603"))
    assert pen < CP._VALUE_MATCH_WEIGHT


def test_two_sizes_up_stays_oversize():
    pen = CP._footprint_penalty(_dims("0402"), _dims("0805"))
    assert pen >= _OVERSIZE_BASE


def test_fit_penalty_below_one_size_up():
    fit = CP._footprint_penalty(_dims("0603"), _dims("0603"))
    one_up = CP._footprint_penalty(_dims("0402"), _dims("0603"))
    assert fit < CP._SLIGHTLY_OVERSIZE_BASE <= one_up


# --- tier classification --------------------------------------------------

@pytest.mark.parametrize("orig,sub,expected", [
    ("0603", "0603", True),              # fits
    ("0805", "0402", True),              # smaller fits
    ("0402", "0603", "one_size_larger"),
    ("0603", "0805", "one_size_larger"),
    ("0402", "0805", False),             # two sizes -> oversize
    ("0603", "1206", False),             # two sizes
])
def test_footprint_tier(orig, sub, expected):
    assert CP._footprint_tier(_dims(orig), _dims(sub)) == expected


def test_footprint_tier_unknown():
    assert CP._footprint_tier(None, _dims("0603")) == "unknown"
    assert CP._footprint_tier(_dims("0402"), None) == "unknown"


# --- caveat stage ---------------------------------------------------------

def test_caveat_downgrades_and_notes_one_size_up():
    st = CrossRefState(source_bom=[], target_manufacturer="W")
    st.crossref_result = [
        {"ref_des": "C7", "status": "recommended",
         "original_package": "0402", "substitute_package": "0603"},
    ]
    CP._stage_footprint_caveat(st)
    row = st.crossref_result[0]
    assert row["status"] == "partial"                 # downgraded
    assert "0402" in row["notes"] and "0603" in row["notes"]   # old -> new size
    assert "verify" in row["notes"].lower()
    assert row["footprint_caveat"] == {"original_package": "0402", "substitute_package": "0603"}


def test_caveat_leaves_fit_and_two_sizes_alone():
    st = CrossRefState(source_bom=[], target_manufacturer="W")
    st.crossref_result = [
        {"ref_des": "Cf", "status": "recommended",
         "original_package": "0603", "substitute_package": "0603"},   # fit
        {"ref_des": "Cb", "status": "recommended",
         "original_package": "0603", "substitute_package": "1206"},   # 2 sizes
        {"ref_des": "Cn", "status": "no_substitute",
         "original_package": "0402", "substitute_package": "0603"},   # not a sub
    ]
    CP._stage_footprint_caveat(st)
    assert all(r["status"] in ("recommended", "no_substitute") for r in st.crossref_result)
    assert all("footprint_caveat" not in r for r in st.crossref_result)
