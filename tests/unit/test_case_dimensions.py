"""Tests for the category-aware case-code → dimensions resolver.

Pins the disambiguation rules from the case-dimension research — especially the
gotcha that the same 4-digit string means different things per component family
(chip L×W vs molded-power-inductor footprint×height), which is what made the L1
"package differs 4040 vs 4020 / fits footprint" report a false alarm.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline.case_dimensions import resolve_dimensions


def _mm(case, cat):
    t = resolve_dimensions(case, cat)
    return tuple(round(x * 1000, 3) if x is not None else None for x in t) if t else None


class TestPowerInductorVsChip:
    def test_we_mapi_4020_is_footprint_by_height(self):
        # The L1 case: "4020" on a molded power inductor = 4.0×4.0×2.0 mm,
        # NOT 4.0×2.0 (L×W). This is the disambiguation that fixes the false
        # "package differs" alarm.
        assert _mm("4020", "magnetic") == (4.0, 4.0, 2.0)

    def test_power_inductor_6045(self):
        assert _mm("6045", "magnetic") == (6.0, 6.0, 4.5)

    def test_chip_code_stays_lxw_for_passive(self):
        # A standard EIA chip code is L×W even in the magnetic category (chip
        # inductor), never re-read as footprint×height.
        assert _mm("0805", "magnetic") == (2.0, 1.25, None)


class TestChipPassives:
    def test_resistor_keeps_height(self):
        assert _mm("0402", "resistor") == (1.0, 0.5, 0.35)

    def test_mlcc_height_dropped(self):
        # MLCC height varies with value/dielectric → not encoded in the code.
        assert _mm("0402", "capacitor") == (1.0, 0.5, None)

    def test_metric_chip_code(self):
        assert _mm("3216", "capacitor") == (3.2, 1.6, None)


class TestPackagesAndAliases:
    def test_sot23(self):
        assert _mm("SOT-23", "mosfet") == (2.9, 1.3, 1.1)

    def test_sc70_alias_to_sot323(self):
        assert _mm("SC-70", "diode") == _mm("SOT-323", "diode") == (2.0, 1.25, 0.95)

    def test_to252_alias_to_dpak(self):
        assert _mm("TO-252", "mosfet") == _mm("DPAK", "mosfet") == (6.1, 6.6, 2.3)

    def test_soic8(self):
        assert _mm("SOIC-8", "analog") == _mm("SO-8", "analog") == (4.9, 3.9, 1.75)


class TestTantalumAndCans:
    def test_tantalum_letter(self):
        assert _mm("B", "capacitor") == (3.5, 2.8, 1.9)

    def test_tantalum_metric_with_height_suffix(self):
        assert _mm("7343-43", "capacitor") == (7.3, 4.3, 4.3)

    def test_aluminum_can_dxl(self):
        assert _mm("16x25", "capacitor") == (16.0, 16.0, 25.0)


class TestNoFabrication:
    def test_unknown_returns_none(self):
        assert resolve_dimensions("nonsense", "resistor") is None
        assert resolve_dimensions("", "resistor") is None
        assert resolve_dimensions(None, "resistor") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
