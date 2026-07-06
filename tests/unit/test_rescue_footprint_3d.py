"""FAE round-6 (EVL1653F L1): the magnetic current-match rescue must gate
footprint with the SAME per-axis + height rule the main matcher uses, not an
area-only proxy. Area collapses aspect ratio (a same-area long-thin part doesn't
fit a square pad) and ignores height (the judge flagged a taller part whose
height was never compared). This locks the 3D gate + the height-aware note.
"""

from heaviside.pipeline import crossref_pipeline as CP


def _t(dims):
    return CP._dims_tuple_mm(dims)


ORIG = {"length": 6.0, "width": 6.0, "height": 4.8}  # 6mm-square molded inductor


def test_oversize_square_rejected_by_tier():
    # WE-HCIA 1050 10.2x10.2x5.1 for a 6x6 original -> >0.65 linear overflow.
    sub = {"length": 10.2, "width": 10.2, "height": 5.1}
    assert CP._footprint_tier(_t(ORIG), _t(sub)) is False


def test_same_area_wrong_aspect_rejected_by_tier():
    # 12x3 = 36 mm^2 == 6x6 = 36 mm^2: an area proxy PASSES, per-axis must FAIL.
    sub = {"length": 12.0, "width": 3.0, "height": 4.0}
    assert CP._dims_area_mm2(sub) == CP._dims_area_mm2(ORIG)  # area-blind would pass
    assert CP._footprint_tier(_t(ORIG), _t(sub)) is False


def test_fits_laterally_but_too_tall_rejected_by_tier():
    # same footprint, 2x the height -> the height axis drives tier False.
    sub = {"length": 6.0, "width": 6.0, "height": 10.0}
    assert CP._footprint_tier(_t(ORIG), _t(sub)) is False


def test_dims_tuple_mm_shapes():
    assert CP._dims_tuple_mm({"length": 6, "width": 6, "height": 4.8}) == (6.0, 6.0, 4.8)
    assert CP._dims_tuple_mm({"length": 6, "width": 6}) == (6.0, 6.0, None)  # height optional
    assert CP._dims_tuple_mm({"length": 6}) is None  # width required
    assert CP._dims_tuple_mm(None) is None


def test_note_surfaces_height_delta():
    # An accepted part that fits laterally but is slightly taller must say so.
    row = {"substitute_value": "1.8µH"}
    resc = {
        "summary": {"mpn": "X", "saturation_current": 25, "rated_current": 16},
        "inductance": 1.8e-6,
        "orig_area_mm2": 36.0, "sub_area_mm2": 40.0,
        "orig_dims_mm": {"length": 6, "width": 6, "height": 4.8},
        "sub_dims_mm": {"length": 6.3, "width": 6.3, "height": 5.1},
    }
    CP._apply_current_match_rescue(row, resc, 1.5e-6, 18, 13.3)
    assert "taller" in row["notes"]
    assert "5.1 vs 4.8 mm height" in row["notes"]
    assert "height clearance" in row["notes"]


def test_note_no_height_bit_when_not_taller():
    row = {"substitute_value": "1.5µH"}
    resc = {
        "summary": {"mpn": "X", "saturation_current": 25, "rated_current": 16},
        "inductance": 1.5e-6,
        "orig_area_mm2": 36.0, "sub_area_mm2": 36.0,
        "orig_dims_mm": {"length": 6, "width": 6, "height": 4.8},
        "sub_dims_mm": {"length": 6, "width": 6, "height": 4.5},  # shorter
    }
    CP._apply_current_match_rescue(row, resc, 1.5e-6, 18, 13.3)
    assert "taller" not in row["notes"]
